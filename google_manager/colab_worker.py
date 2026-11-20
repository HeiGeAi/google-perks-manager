"""Process boundary for the upstream Colab Python client.

This entry point does not load the CLI's global profile, credentials or update
settings. Its private pipe receives one verified bearer grant from the app.
"""

import json
import logging
import math
import os
import re
import sys
import uuid
from urllib.parse import urlsplit

from .colab import GPUS, ProviderError
from .remote_jobs import execute_job


def safe_instance_id(value):
    return isinstance(value, str) and re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}", value) is not None


def region_from_provider(value):
    try:
        url = urlsplit(value)
        host = url.hostname or ""
        if (
            url.scheme != "https" or url.username is not None or url.password is not None
            or url.port not in {None, 443}
            or not host.endswith((".prod.r.colab.dev", ".googleusercontent.com"))
        ):
            return None
        prefix, separator, _ = host.partition(".prod.")
        if not separator:
            return None
        match = re.fullmatch(r"([a-z]+(?:-[a-z]+)+[0-9]+)(?:-[a-z])?", prefix.rsplit(".", 1)[-1])
        return match[1] if match else None
    except (TypeError, ValueError):
        return None


def usage_amount(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0:
        raise ProviderError("Google 返回的算力用量无法核对。")
    return value


def observe(client, request_error=ProviderError):
    instances = []
    seen = set()
    for assignment in client.list_assignments():
        endpoint = assignment.endpoint
        if not safe_instance_id(endpoint) or endpoint in seen:
            raise ProviderError("Google 返回的实例标识无法核对。")
        seen.add(endpoint)
        gpu = assignment.accelerator.value
        instances.append({
            "id": endpoint,
            "gpu": "CPU" if gpu == "NONE" else gpu,
            "region": region_from_provider(assignment.runtime_proxy_info.url),
        })
    try:
        usage = client.get_consumption_user_info()
        balance = usage_amount(usage.paid_compute_units_balance)
        hourly = usage_amount(usage.consumption_rate_hourly)
        error = None
    except (request_error, OSError, ValueError, AttributeError, ProviderError):
        balance = hourly = None
        error = "已读取实例列表，Google 算力余额和计费费率待核对。"
    return {
        "balance_cu": balance,
        "hourly_cu": hourly,
        "instances": instances,
        "eligible_gpus": [],
        "error": error,
    }


def execute(payload):
    try:
        import requests
        from colab_cli.client import Accelerator, Client, ColabRequestError, GetAssignmentResponse, Prod, TooManyAssignmentsError, Variant
    except ImportError:
        return {"error": "指定的 Python 无法导入 google-colab-cli。", "status": 503}

    class RequestSession(requests.Session):
        def request(self, method, url, **kwargs):
            kwargs.update(timeout=(8, 45), allow_redirects=False)
            response = super().request(method, url, **kwargs)
            if 300 <= response.status_code < 400:
                raise ProviderError("Colab 返回了意外的重定向。")
            return response

    action = payload.get("action")
    token = payload.get("access_token")
    if not isinstance(token, str) or not token or "\r" in token or "\n" in token:
        return {"error": "账号授权不可用，请重新连接。", "status": 401}

    logger = logging.getLogger("google_manager.upstream")
    logger.disabled = True
    os.environ["COLAB_CLI_ASSIGN_RETRIES"] = "0"

    try:
        with RequestSession() as session:
            session.trust_env = False
            session.headers["Authorization"] = f"Bearer {token}"
            client = Client(Prod(), session, logger=logger)
            if action in {"submit_job", "review_job"}:
                return execute_job(action, client, RequestSession, payload)
            if action == "observe":
                return observe(client, ColabRequestError)
            if action in {"start", "inspect_start"}:
                gpu = payload["gpu"]
                if gpu not in GPUS:
                    raise ProviderError("请选择支持的 Colab 硬件。", 400)
                nonce = uuid.UUID(payload["nonce"])
                variant = Variant.DEFAULT if gpu == "CPU" else Variant.GPU
                accelerator = Accelerator.NONE if gpu == "CPU" else Accelerator(gpu)
                if action == "inspect_start":
                    # Reconcile the original nonce, as the installed CLI does before retrying.
                    assignment = client._get_assignment(nonce, variant, accelerator)
                    if isinstance(assignment, GetAssignmentResponse):
                        return {"instance_id": None}
                else:
                    assignment = client.assign(nonce, variant=variant, accelerator=accelerator)
                if not safe_instance_id(assignment.endpoint):
                    raise ProviderError("启动响应缺少实例标识，请刷新实例列表。", uncertain=True)
                return {"instance_id": assignment.endpoint}
            if action == "stop":
                target = payload["instance_id"]
                if not safe_instance_id(target) or not any(a.endpoint == target for a in client.list_assignments()):
                    raise ProviderError("所选实例已不在这个账号的当前列表里，请刷新。", 404)
                client.unassign(target)
                return {"ok": True}
            raise ProviderError("操作不存在。", 400)
    except ProviderError as exc:
        return {"error": str(exc), "status": exc.status, "uncertain": exc.uncertain}
    except TooManyAssignmentsError:
        return {"error": "Google 暂未分配实例，请核对该账号的容量和配额。", "status": 409}
    except ColabRequestError as exc:
        status = exc.response.status_code
        if status in {401, 403}:
            return {"error": "Google 拒绝 Colab 授权。请核对这个账号的权限和同意范围。", "status": status}
        if status in {400, 404, 409, 412, 429}:
            return {"error": "Google 暂未接受操作，请核对实例与账号配额。", "status": status}
        return {"error": f"Colab 本次返回 HTTP {status}，请刷新该账号核对。", "status": 502, "uncertain": True}
    except (requests.RequestException, ValueError, KeyError, AttributeError, TypeError):
        return {"error": "无法确认 Colab 的返回，请刷新该账号后核对。", "status": 502, "uncertain": True}


if __name__ == "__main__":
    if sys.argv[1:] == ["--authorize"]:
        from .colab_auth import run_authorization

        try:
            run_authorization()
        except Exception:
            raise SystemExit(1) from None
        raise SystemExit(0)
    try:
        output = execute(json.load(sys.stdin))
    except Exception:
        output = {"error": "Colab 组件发生异常，请刷新该账号后核对。", "status": 502, "uncertain": True}
    json.dump(output, sys.stdout, ensure_ascii=False, allow_nan=False)
