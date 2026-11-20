"""Command-line entry point for Agents operating their user's local app."""

import argparse
import hashlib
import http.client
import json
import os
import re
import subprocess
import sys
import uuid
import webbrowser
from pathlib import Path
from urllib.parse import urlsplit

from . import __version__
from .accounts import UserError, read_private_json
from .local_files import ensure_process_running
from .agent_service import AGENT_HEADER, CONFIRM_COMMANDS, PLAN_TIERS, RUNTIME_FILE
from .colab import GPUS


MAX_RESPONSE = 8 * 1024 * 1024


def guide(language):
    english = language == "en"
    return {
        "interface_version": 1,
        "app_version": __version__,
        "bundle_usage": "<GoogleManagerService absolute path> --agent [--data-dir PATH] COMMAND",
        "source_usage": "python3 -m google_manager.agent [--data-dir PATH] COMMAND",
        "workflows": [
            "Open the app on this computer. Read state to choose an account and verify its connection before acting."
            if english else "在本机打开 App。操作前先读取 state，选择账号并核对授权状态。",
            "For benefits, read the catalog and official claim and verification URLs. Have the user complete any Google consent, financial or subscription step. Record claimed only after verifying Google's result."
            if english else "在权益目录读取官方领取、核验地址和步骤。由用户完成 Google 授权、计费或订阅操作，核验到账结果后再记录已领取。",
            "Confirm the exact account, target instance, actual fee if available, and intended operation with the user before using --confirm. For Breeze TTS 2, use the model only for research and non-commercial purposes."
            if english else "使用 --confirm 前，请让用户确认账号、目标实例、可核对的当次费率及具体操作。Breeze TTS 2 自托管仅用于研究和非商用。",
            "After submitting a job, read job status and collect when needed. Use the local artifact path from a completed job. Reuse the same job ID on retry."
            if english else "提交后读取 job 进度，必要时 collect 核对并取回。任务完成后可读取作品本机路径，重试时沿用原任务 ID。",
            "Saved local outputs are available across accounts. In Studio, users can open an output folder or save a copy to a Google Drive sync folder."
            if english else "已保存的本机作品可跨账号查看。用户可在创作页打开作品文件夹，或转存到 Google Drive 同步目录。",
            "Keep credentials on the user's computer. Import only an authorized official Colab CLI token file by path. A cached observation describes the recorded time, and a plan filter describes catalog coverage."
            if english else "凭证保存在本机。导入官方 Colab CLI 授权时只传用户指定的文件路径。历史快照对应记录时间，方案筛选对应目录范围。",
        ],
        "commands": {
            "guide": "guide [--language en|zh-CN]",
            "state": "state [--account ID]",
            "benefits": "benefits [--account ID] [--plan pro|ultra-5x|ultra-20x] [--language en|zh-CN]",
            "benefit": "benefit BENEFIT [--account ID] [--plan pro|ultra-5x|ultra-20x] [--language en|zh-CN]",
            "jobs": "jobs [--account ID]",
            "job": "job JOB [--account ID]",
            "refresh": "refresh ACCOUNT",
            "account-add": "account-add --name NAME --email EMAIL --confirm",
            "connect": "connect ACCOUNT [--client-config PATH] --confirm",
            "import-cli": "import-cli ACCOUNT --token-file PATH --confirm",
            "claim-record": "claim-record ACCOUNT BENEFIT --status available|claimed|not_eligible|unverified [--note TEXT] --confirm",
            "instance-start": "instance-start ACCOUNT --gpu CPU|T4|L4|A100|H100|G4 --name NAME --confirm",
            "instance-stop": "instance-stop ACCOUNT INSTANCE --confirm",
            "tts": "tts ACCOUNT INSTANCE --title TITLE --text TEXT [--instruction TEXT] [--job-id JOB] --confirm",
            "video": "video ACCOUNT INSTANCE --title TITLE --text TEXT [--ratio landscape|portrait] [--seconds 3|5] [--seed N] [--job-id JOB] --confirm",
            "mux": "mux --title TITLE (--video-job JOB|--video-path PATH) (--audio-job JOB|--audio-path PATH) [--account ID] [--job-id JOB]",
            "collect": "collect JOB [--account ID]",
        },
    }


def parser():
    cli = argparse.ArgumentParser(prog="GoogleManagerService --agent", description="Google Perks Manager local Agent CLI")
    cli.add_argument("--data-dir", type=Path, help="the same local data directory as the running app")
    commands = cli.add_subparsers(dest="command", required=True)
    help_command = commands.add_parser("guide", help="print commands and workflow in JSON, even when the app is closed")
    help_command.add_argument("--language", choices=("en", "zh-CN"), default="en")
    for name in ("state", "benefits", "benefit", "jobs", "job", "collect"):
        command = commands.add_parser(name)
        command.add_argument("--account")
        if name in {"benefits", "benefit"}:
            command.add_argument("--plan", choices=PLAN_TIERS)
            command.add_argument("--language", choices=("en", "zh-CN"), default="en")
        if name == "benefit":
            command.add_argument("benefit")
        if name in {"job", "collect"}:
            command.add_argument("job_id")

    commands.add_parser("refresh").add_argument("account")
    add = commands.add_parser("account-add")
    add.add_argument("--name", required=True)
    add.add_argument("--email", required=True)
    connect = commands.add_parser("connect", help="open Google consent in the user's browser")
    connect.add_argument("account")
    connect.add_argument("--client-config")
    importer = commands.add_parser("import-cli")
    importer.add_argument("account")
    importer.add_argument("--token-file", required=True)
    claim = commands.add_parser("claim-record", help="save a user-verified result in the local app")
    claim.add_argument("account")
    claim.add_argument("benefit")
    claim.add_argument("--status", choices=("available", "claimed", "not_eligible", "unverified"), required=True)
    claim.add_argument("--note")

    start = commands.add_parser("instance-start")
    start.add_argument("account")
    start.add_argument("--gpu", choices=GPUS, required=True)
    start.add_argument("--name", required=True)
    stop = commands.add_parser("instance-stop")
    stop.add_argument("account")
    stop.add_argument("instance")

    for name in ("tts", "video"):
        command = commands.add_parser(name)
        command.add_argument("account")
        command.add_argument("instance")
        command.add_argument("--title", required=True)
        command.add_argument("--text", required=True)
        command.add_argument("--job-id")
        command.add_argument("--confirm", dest="confirmed", action="store_true", help="user confirmed this operation")
        if name == "tts":
            command.add_argument("--instruction", default="")
        else:
            command.add_argument("--ratio", choices=("landscape", "portrait"), default="landscape")
            command.add_argument("--seconds", type=int, choices=(3, 5), default=5)
            command.add_argument("--seed", type=int)

    mux = commands.add_parser("mux")
    mux.add_argument("--account")
    mux.add_argument("--title", required=True)
    mux.add_argument("--job-id")
    for name in ("video", "audio"):
        source = mux.add_mutually_exclusive_group(required=True)
        source.add_argument(f"--{name}-job")
        source.add_argument(f"--{name}-path")
    for command in (add, connect, importer, claim, start, stop):
        command.add_argument("--confirm", dest="confirmed", action="store_true", help="user confirmed this operation")
    return cli


def discovery(data_dir):
    try:
        record = read_private_json(data_dir / RUNTIME_FILE)
        port = record["port"]
        pid = record["pid"]
        token = record["token"]
        if (type(port) is not int or not 1 <= port <= 65535 or type(pid) is not int or pid <= 1
                or not isinstance(token, str) or not re.fullmatch(r"[A-Za-z0-9_-]{43}", token)):
            raise ValueError
        ensure_process_running(pid)
        return port, token
    except (OSError, ValueError, KeyError, TypeError, UserError):
        raise UserError("Open Google Perks Manager on this computer and use the app's local data directory.", 503) from None


def request(data_dir, body):
    port, token = discovery(data_dir)
    connection = http.client.HTTPConnection("127.0.0.1", port, timeout=180)
    try:
        connection.request("POST", "/api/agent", body=json.dumps(body, ensure_ascii=False).encode("utf-8"), headers={
            "Origin": f"http://127.0.0.1:{port}",
            "Content-Type": "application/json",
            AGENT_HEADER: token,
        })
        response = connection.getresponse()
        raw = response.read(MAX_RESPONSE + 1)
        if len(raw) > MAX_RESPONSE:
            raise ValueError
        result = json.loads(raw)
        if not isinstance(result, dict):
            raise ValueError
        if response.status >= 400:
            error = result.get("error")
            raise UserError(error if isinstance(error, str) else "The local request failed.", response.status)
        return result
    except (OSError, http.client.HTTPException, ValueError):
        raise UserError("The local app did not return a result. Check its state or existing job before retrying.", 503) from None
    finally:
        connection.close()


def open_consent(url):
    if not isinstance(url, str):
        raise UserError("The app did not return a Google authorization address.", 502)
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "accounts.google.com":
        raise UserError("The Google authorization address could not be verified.", 502)
    try:
        if sys.platform == "darwin":
            opened = subprocess.run(["/usr/bin/open", url], capture_output=True, timeout=10).returncode == 0
        else:
            opened = webbrowser.open(url)
    except (OSError, subprocess.SubprocessError, webbrowser.Error):
        opened = False
    if not opened:
        raise UserError("Could not open Google consent. Start authorization from Accounts in the app.", 503)


def main(argv=None):
    args = parser().parse_args(argv)
    body = {key: value for key, value in vars(args).items() if key != "data_dir" and value is not None}
    command = args.command
    if command == "guide":
        print(json.dumps(guide(args.language), ensure_ascii=False))
        return 0
    if command in {"tts", "video", "mux"}:
        body.setdefault("job_id", uuid.uuid4().hex)
    if command == "video":
        # A retry with the same job ID must reproduce the original job spec.
        seed = int.from_bytes(hashlib.blake2s(body["job_id"].encode(), digest_size=4).digest(), "big")
        body.setdefault("seed", seed)
    for key in ("client_config", "token_file", "video_path", "audio_path"):
        if key in body:
            body[key] = str(Path(body[key]).expanduser().absolute())
    try:
        if command in CONFIRM_COMMANDS and not body["confirmed"]:
            raise UserError("This command requires user approval and --confirm.", 403)
        from .server import default_data_dir
        path = args.data_dir or os.environ.get("GOOGLE_MANAGER_DATA_DIR") or default_data_dir()
        result = request(Path(path).expanduser(), body)
        if command == "connect":
            open_consent(result.pop("open_url", None))
            result["authorization_started"] = True
            result["next_action"] = "review_google_consent_in_browser"
        print(json.dumps(result, ensure_ascii=False, allow_nan=False))
        return 0
    except UserError as exc:
        result = {"error": str(exc), "status": exc.status}
        if command in {"tts", "video", "mux"}:
            result["job_id"] = body["job_id"]
        print(json.dumps(result, ensure_ascii=False))
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
