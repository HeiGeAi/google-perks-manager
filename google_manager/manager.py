"""Account-scoped product operations shared by the local HTTP endpoints."""

import json
import math
import os
import re
import uuid
from datetime import datetime, timezone
from pathlib import Path

from .accounts import AccountStore, UserError, empty_observation, label, timestamp
from .colab import GPUS, HARDWARE, HIGH_RAM_CHOICES, Colab, ProviderError
from .deployment import H3_REGIONS, PROJECTS
from .drive_archive import DriveArchive
from .editor_tunnel import EditorTunnels
from .oauth import GoogleOAuth
from .production import Production


BENEFIT_STATUSES = {"unverified", "available", "claimed", "not_eligible"}
CACHE_SECONDS = 60
ACK_VISIBILITY_SECONDS = 120


class Manager:
    def __init__(self, data_dir, catalog_path, *, oauth=None, colab=None, client_config=None):
        self.store = AccountStore(data_dir)
        self.catalog_path = Path(catalog_path)
        self.oauth = oauth if oauth is not None else GoogleOAuth()
        self.colab = colab if colab is not None else Colab()
        self.client_config = client_config or os.environ.get("GOOGLE_MANAGER_OAUTH_CLIENT")
        self.production = Production(self)
        self.drive = DriveArchive(self.store, self.production)
        self.editor_tunnels = EditorTunnels()

    def benefits(self):
        try:
            catalog = json.loads(self.catalog_path.read_text(encoding="utf-8"))
        except FileNotFoundError:
            return []
        except (OSError, ValueError):
            raise UserError("权益目录暂时无法读取。", 500) from None
        if isinstance(catalog, dict):
            catalog = catalog.get("benefits")
        if not isinstance(catalog, list) or any(not isinstance(item, dict) or not item.get("id") for item in catalog):
            raise UserError("权益目录格式错误。", 500)
        return catalog

    def state(self):
        accounts = []
        for account in self.store.all():
            observation = account["observation"]
            if observation["status"] == "current":
                checked = datetime.fromisoformat(observation["checked_at"])
                if (datetime.now(timezone.utc) - checked).total_seconds() > CACHE_SECONDS:
                    observation["status"] = "cached"
            connected = self.store.has_credential(account["id"])
            auth = "connected" if connected else "disconnected"
            if self.oauth.connecting(account["id"]):
                auth = "connecting"
            if account.get("authorization_invalid") and auth == "connected":
                auth = "disconnected"
            accounts.append({
                "id": account["id"],
                "name": account["name"],
                "email": account["email"],
                "auth_status": auth,
                "auth_error": account["auth_error"],
                "observation": observation,
                "benefit_status": account["benefit_status"],
                "operation_pending": bool(account.get("operation_pending")),
            })
        return {"accounts": accounts, "benefits": self.benefits(), "supported_gpus": list(GPUS),
                "supported_hardware": list(HARDWARE), "high_ram_hardware": sorted(HIGH_RAM_CHOICES),
                "h3_regions": sorted(H3_REGIONS),
                "deployment_projects": {key: {k: v for k, v in row.items() if k not in {"port", "recipe"}}
                                        for key, row in PROJECTS.items()}}

    def connect(self, account_id, body, redirect_uri):
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            config_path = body.get("client_config_path") or self.client_config
            if config_path:
                result = self.oauth.begin(account, config_path, redirect_uri)
            elif body.get("desktop_only"):
                raise UserError("Open Accounts in the app and click Connect to Google, or pass a configured Desktop OAuth client.")
            else:
                result = self.oauth.begin_cli(account, self.colab)
            self.store.update(account_id, auth_error=None)
            return result

    def finish_cli_login(self, account_id, attempt, code):
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            try:
                subject, credential = self.oauth.finish_cli(account, attempt, code)
                self.store.bind(account_id, subject, credential)
                self.store.update(account_id, authorization_invalid=False)
            except UserError as exc:
                self.store.update(account_id, auth_error=str(exc))
                raise

    def oauth_callback(self, state, code, declined=False):
        account_id = self.oauth.account_for_state(state)
        with self.store.account_lock(account_id):
            login = self.oauth.consume(state)
            account = self.store.one(account_id)
            try:
                if declined:
                    raise UserError("Google 授权未完成。可以回到账号页重试。")
                subject, credentials = self.oauth.exchange(account, login, code)
                self.store.bind(login.account_id, subject, credentials)
                self.store.update(login.account_id, authorization_invalid=False)
            except UserError as exc:
                self.store.update(login.account_id, auth_error=str(exc))
                raise

    def import_cli(self, account_id, path):
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            subject, credential = self.oauth.import_cli(account, path)
            self.store.bind(account_id, subject, credential)
            self.store.update(account_id, authorization_invalid=False)
            self.oauth.cancel(account_id)

    def record_error(self, account, exc):
        previous = account["observation"]
        observation = {
            **previous,
            "status": "cached" if previous["checked_at"] else "error",
            "error": str(exc),
        }
        update = {"observation": observation}
        if exc.status in {401, 403}:
            update["auth_error"] = str(exc)
            update["authorization_invalid"] = True
        self.store.update(account["id"], **update)

    def _observe(self, account, token):
        requested_at = datetime.now(timezone.utc)
        received = self.colab.observe(token)
        names = account["instance_names"]
        hardware = {item["id"]: item.get("hardware") for item in account["observation"]["instances"]}
        observation = {
            **empty_observation(),
            **received,
            "status": "current",
            "checked_at": timestamp(),
            "instances": [
                {"id": item["id"], "name": names.get(item["id"]) or item["id"], "gpu": item["gpu"],
                 "region": item.get("region"), "machine_shape": item.get("machine_shape"), "hardware": hardware.get(item["id"])}
                for item in received["instances"]
            ],
        }
        updates = {"observation": observation, "authorization_invalid": False, "auth_error": None}
        pending = account.get("operation_pending")
        if pending and pending.get("instance_id"):
            visible = any(item["id"] == pending["instance_id"] for item in observation["instances"])
            acknowledged_at = pending.get("acknowledged_at")
            elapsed = (requested_at - datetime.fromisoformat(acknowledged_at)).total_seconds() if acknowledged_at else 0
            if visible or elapsed >= ACK_VISIBILITY_SECONDS:
                updates["operation_pending"] = None
            elif not acknowledged_at:
                updates["operation_pending"] = {**pending, "acknowledged_at": observation["checked_at"]}
        self.store.update(account["id"], **updates)
        return observation

    def refresh(self, account_id):
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            try:
                token = self.oauth.access(account, self.store)
                return self._observe(account, token)
            except UserError as exc:
                self.record_error(account, exc)
                raise

    def start(self, account_id, body):
        if body.get("confirmed") is not True:
            raise UserError("请先确认 Colab 启动与可能的算力消耗。", 403)
        gpu = body.get("gpu")
        high_mem = body.get("high_mem", False)
        if gpu not in HARDWARE or type(high_mem) is not bool or (high_mem and gpu not in HIGH_RAM_CHOICES):
            raise UserError("请选择支持的 Colab 硬件。")
        name = label(body.get("name"), "实例名称", 48)
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            acknowledged_id = (account.get("operation_pending") or {}).get("instance_id")
            try:
                token = self.oauth.access(account, self.store)
                observed = self._observe(account, token)
            except UserError as exc:
                self.record_error(account, exc)
                raise
            account = self.store.one(account_id)
            if acknowledged_id and not any(item["id"] == acknowledged_id for item in observed["instances"]):
                if account.get("operation_pending"):
                    return {
                        "status": "pending",
                        "instance_id": acknowledged_id,
                        "message": "Colab 已回复分配。请刷新，等待实例出现在列表中。",
                    }
                raise UserError("最新列表未找到上次分配的实例。请核对列表后重新确认启动。", 409)
            if any(item["name"].casefold() == name.casefold() for item in observed["instances"]):
                raise UserError("该账号已有同名运行实例，请先从列表核对。", 409)
            pending = account.get("operation_pending")
            if pending and pending["name"] != name:
                raise UserError("上次启动结果待核对。请保留上次的实例名称再试。", 409)
            if pending and (pending["gpu"], pending.get("high_mem", False)) != (gpu, high_mem):
                previous_shape = {"high_mem": True} if pending.get("high_mem") else {}
                previous_id = self.colab.inspect_start(token, pending["gpu"], pending["nonce"], **previous_shape)
                if previous_id:
                    self.store.update(
                        account_id, instance_names={**account["instance_names"], previous_id: name},
                        operation_pending={**pending, "instance_id": previous_id, "acknowledged_at": timestamp()},
                    )
                    raise UserError("Google 已给上一请求分配实例，请刷新核对后操作。", 409)
                pending = None
            pending = pending or {"nonce": uuid.uuid4().hex, "name": name, "gpu": gpu, "high_mem": high_mem}
            self.store.update(account_id, operation_pending=pending)
            try:
                instance_id = self.colab.start(token, gpu, pending["nonce"], **({"high_mem": True} if high_mem else {}))
            except ProviderError as exc:
                if not exc.uncertain:
                    self.store.update(account_id, operation_pending=None)
                    if exc.status not in {401, 403}:
                        raise
                self.record_error(account, exc)
                raise
            names = {**account["instance_names"], instance_id: name}
            account = self.store.update(
                account_id,
                instance_names=names,
                operation_pending={**pending, "instance_id": instance_id, "acknowledged_at": timestamp()},
            )
            try:
                refreshed = self._observe(account, token)
            except UserError as exc:
                self.record_error(account, exc)
                return {"status": "pending", "instance_id": instance_id, "message": "Colab 已回复分配，请刷新核对实例。"}
            visible = any(item["id"] == instance_id for item in refreshed["instances"])
            return {
                "status": "confirmed" if visible else "pending",
                "instance_id": instance_id,
                "message": "已从当前列表核对新实例。" if visible else "Colab 已回复分配，实例暂未出现在读取结果中。",
            }

    def inspect_instance(self, account_id, instance_id):
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            token = self.oauth.access(account, self.store)
            observation = self._observe(account, token)
            instance = next((row for row in observation["instances"] if row["id"] == instance_id), None)
            if not instance:
                raise UserError("当前账号已查不到这个 Colab 实例。", 404)
            instance["hardware"] = {**self.colab.inspect(token, instance_id), "checked_at": timestamp()}
            self.store.update(account_id, observation=observation)
            return instance

    def open_editor(self, job_id):
        job = self.production.get(job_id)
        if job["kind"] != "deploy" or job["state"] != "done" or PROJECTS[job["project"]]["port"] is None:
            raise UserError("请先在这台实例上完成应用部署。", 409)
        account_id, instance_id = job["account_id"], job["instance_id"]
        with self.store.account_lock(account_id):
            token = self.oauth.access(self.store.one(account_id), self.store)
            details = self.colab.editor_connection(token, instance_id, job["project"])
            return self.editor_tunnels.open(account_id, instance_id, details)

    def stop(self, account_id, body):
        if body.get("confirmed") is not True:
            raise UserError("请确认释放时已保存实例中的文件。", 403)
        target = body.get("instance_id")
        if not isinstance(target, str) or not re.fullmatch(r"[a-zA-Z0-9][a-zA-Z0-9_.-]{0,159}", target):
            raise UserError("请从这个账号的实例列表选择目标。")
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            self.production.allow_release(account_id, target, body.get("discard_jobs") is True)
            try:
                token = self.oauth.access(account, self.store)
                # The provider rechecks the exact assignment before release.
                self.colab.stop(token, target)
                self.editor_tunnels.close_for(account_id, target)
            except UserError as exc:
                self.record_error(account, exc)
                raise
            try:
                refreshed = self._observe(account, token)
            except UserError as exc:
                self.record_error(account, exc)
                return {"status": "pending", "instance_id": target, "message": "已提交释放，请刷新核对实例和费率。"}
            present = any(item["id"] == target for item in refreshed["instances"])
            if not present:
                self.production.mark_released(account_id, target)
                names = {key: value for key, value in account["instance_names"].items() if key != target}
                updates = {"instance_names": names}
                if (account.get("operation_pending") or {}).get("instance_id") == target:
                    updates["operation_pending"] = None
                self.store.update(account_id, **updates)
            message = "已提交释放，实例列表尚未更新。" if present else "已从当前列表确认实例释放。"
            if refreshed["hourly_cu"] is None:
                message += "算力费率待核对。"
            return {"status": "pending" if present else "confirmed", "instance_id": target, "message": message}

    def update_benefit(self, account_id, benefit_id, body):
        if not any(item["id"] == benefit_id for item in self.benefits()):
            raise UserError("找不到这项权益。", 404)
        status = body.get("status")
        note = body.get("note", "")
        if not isinstance(status, str) or status not in BENEFIT_STATUSES:
            raise UserError("请选择权益状态。")
        if not isinstance(note, str) or len(note) > 500:
            raise UserError("权益备注最多 500 个字符。")
        with self.store.account_lock(account_id):
            account = self.store.one(account_id)
            statuses = account["benefit_status"]
            statuses[benefit_id] = {
                "status": status,
                "note": note.strip(),
                "checked_at": timestamp() if status != "unverified" else None,
                "source": "user",
            }
            self.store.update(account_id, benefit_status=statuses)
