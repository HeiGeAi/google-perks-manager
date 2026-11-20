"""The authenticated local Agent command contract and its data projections."""

import json
import re
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .accounts import UserError


RUNTIME_FILE = ".agent-runtime.json"
AGENT_HEADER = "X-Google-Manager-Agent"
PLAN_TIERS = ("pro", "ultra-5x", "ultra-20x")
READ_COMMANDS = {"state", "benefits", "benefit", "jobs", "job"}
CONFIRM_COMMANDS = {
    "account-add", "connect", "import-cli", "claim-record", "instance-start", "instance-stop", "tts", "video", "deploy",
}
COMMANDS = READ_COMMANDS | CONFIRM_COMMANDS | {"refresh", "mux", "collect"}
ID = re.compile(r"[a-f0-9]{32}")
BENEFIT_ID = re.compile(r"[a-z0-9][a-z0-9-]{0,99}")
EMAIL = re.compile(r"[^\s<>()\[\],;:@]+@[^\s<>()\[\],;:@]+\.[^\s<>()\[\],;:@]+")


def redact_email(value):
    return EMAIL.sub("[email]", value) if isinstance(value, str) else value


def read_command(body):
    command = body.get("command")
    if not isinstance(command, str) or command not in COMMANDS:
        raise UserError("Unknown Agent command. Run the Agent guide for available commands.", 404)
    if command in CONFIRM_COMMANDS and body.get("confirmed") is not True:
        raise UserError("This Agent command requires explicit confirmation.", 403)
    return command


def agent_account(account):
    observation = account["observation"]
    instances = [
        {**item, "name": redact_email(item["name"])}
        for item in observation["instances"]
    ]
    return {
        "id": account["id"],
        "name": redact_email(account["name"]),
        "auth_status": account["auth_status"],
        "has_auth_error": bool(account["auth_error"]),
        "operation_pending": account["operation_pending"],
        "observation": {
            **{key: observation.get(key) for key in ("status", "checked_at", "balance_cu", "hourly_cu", "eligible_gpus")},
            "instances": instances,
            "source": "Colab",
            "has_error": bool(observation.get("error")),
        },
    }


def translated_benefits(manager, language):
    if language != "en":
        return {}
    try:
        path = manager.catalog_path.parent / "web" / "benefits.en.json"
        translations = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}
    return translations if isinstance(translations, dict) else {}


def benefit_cards(manager, plan, account, benefit_id=None, *, language=None):
    if plan is not None and plan not in PLAN_TIERS:
        raise UserError("Choose pro, ultra-5x or ultra-20x for the plan filter.")
    rows = []
    translations = translated_benefits(manager, language)
    for benefit in manager.benefits():
        if benefit_id is not None and benefit["id"] != benefit_id:
            continue
        tiers = benefit.get("plan_values") or {}
        listed = bool(benefit.get("independent") or plan in tiers or not tiers) if plan else None
        if benefit_id is None and listed is False:
            continue
        patch = translations.get(benefit["id"], {})
        if not isinstance(patch, dict):
            patch = {}
        translated_fields = {
            name: value for name, value in patch.items()
            if name in {"title", "summary", "eligibility", "action_label", "comparison", "steps", "plan_unit"}
        }
        translated_values = patch.get("plan_values", {})
        if not isinstance(translated_values, dict):
            translated_values = {}
        record = account["benefit_status"].get(benefit["id"]) if account else None
        rows.append({
            **benefit,
            **translated_fields,
            **({"plan_values": {key: translated_values.get(key, value) for key, value in tiers.items()}} if tiers else {}),
            "listed_for_plan": listed,
            "local_record": {key: record.get(key) for key in ("status", "checked_at", "source")} if record else None,
        })
    if benefit_id is not None:
        if not rows:
            raise UserError("Benefit not found.", 404)
        return {"plan_filter": plan, "benefit": rows[0]}
    return {"plan_filter": plan, "benefits": rows}


def agent_job(production, row):
    fields = ("id", "title", "kind", "mode", "account_id", "instance_id", "state", "phase", "created_at", "updated_at")
    result = {key: redact_email(row.get(key)) for key in fields}
    if row["kind"] == "deploy":
        return {**result, "project": row["project"], "hardware": row.get("hardware")}
    if row["state"] == "done":
        try:
            _, path = production.artifact_path(row["id"])
            path = path.resolve()
            if not path.is_relative_to(production.root.resolve()):
                raise UserError("The saved artifact is outside the local library.", 404)
            artifact_keys = ("name", "size", "sha256", "engine", "seconds", "sample_rate", "width", "height")
            result["artifact"] = {
                **{key: row["artifact"][key] for key in artifact_keys if key in row["artifact"]},
                "path": str(path),
            }
        except UserError:
            result["artifact_missing"] = True
    return result


def lookup_job(production, job_id, account_id, *, collect=False):
    if not isinstance(job_id, str) or not ID.fullmatch(job_id):
        raise UserError("Choose a job ID from the local job list.")
    row = production.get(job_id)
    if row["mode"] == "colab" and row["account_id"] != account_id:
        if collect or not production.has_local_output(row):
            raise UserError("Choose the account that owns this job.", 404)
    return row


def without_login_hint(url):
    parts = urlsplit(url)
    if parts.scheme != "https" or parts.netloc != "accounts.google.com":
        raise UserError("The Google authorization address could not be verified.", 502)
    query = urlencode([(key, value) for key, value in parse_qsl(parts.query) if key != "login_hint"])
    return urlunsplit(parts._replace(query=query))


def execute(manager, dispatch, command, body):
    account_id = body.get("account")
    if account_id is not None:
        if not isinstance(account_id, str) or not ID.fullmatch(account_id):
            raise UserError("Choose an account ID from the local account list.")
        account = manager.store.one(account_id)
    else:
        account = None

    production = manager.production
    account_path = f"/api/accounts/{account_id}"
    if command in {"state", "refresh"}:
        if command == "refresh":
            if not account:
                raise UserError("Choose an account to refresh.")
            dispatch(account_path + "/refresh", {})
        state = manager.state()
        accounts = [agent_account(row) for row in state["accounts"] if account_id is None or row["id"] == account_id]
        capabilities = ("supported_gpus", "supported_hardware", "high_ram_hardware", "deployment_projects", "h3_regions")
        return 200, {"accounts": accounts, **{key: state[key] for key in capabilities}}

    if command in {"benefits", "benefit"}:
        benefit_id = body.get("benefit") if command == "benefit" else None
        if command == "benefit" and (not isinstance(benefit_id, str) or not BENEFIT_ID.fullmatch(benefit_id)):
            raise UserError("Choose a benefit ID from the catalog.")
        return 200, benefit_cards(manager, body.get("plan"), account, benefit_id, language=body.get("language"))

    if command == "jobs":
        return 200, {"jobs": [agent_job(production, row) for row in production.list(account_id)]}
    if command in {"job", "collect"}:
        row = lookup_job(production, body.get("job_id"), account_id, collect=command == "collect")
        code = 200
        if command == "collect":
            code, payload = dispatch(f"/api/production/jobs/{row['id']}/refresh", {})
            row = payload["job"]
        return code, {"job": agent_job(production, row)}

    if command == "account-add":
        return dispatch("/api/accounts", {"name": body.get("name"), "email": body.get("email")})

    if command == "mux":
        keys = ("video_job", "video_path", "audio_job", "audio_path")
        job = {"id": body.get("job_id"), "kind": "mux", "mode": "mac", "title": body.get("title"),
               **{key: body.get(key) for key in keys}}
        code, payload = dispatch("/api/production/jobs", job)
        return code, {"job": agent_job(production, payload["job"])}

    if not account:
        raise UserError("Choose an account ID from the local account list.")
    if command == "connect":
        code, result = dispatch(account_path + "/connect", {"client_config_path": body.get("client_config"), "desktop_only": True})
        return code, {"account_id": account_id, "open_url": without_login_hint(result["open_url"])}
    if command == "import-cli":
        code, _ = dispatch(account_path + "/import-cli", {"token_file": body.get("token_file")})
        return code, {"account_id": account_id, "auth_status": "connected"}
    if command == "claim-record":
        benefit_id = body.get("benefit")
        if not isinstance(benefit_id, str) or not BENEFIT_ID.fullmatch(benefit_id):
            raise UserError("Choose a benefit ID from the catalog.")
        code, _ = dispatch(account_path + "/benefits/" + benefit_id, {
            "status": body.get("status"), "note": body.get("note", ""),
        })
        account = manager.store.one(account_id)
        return code, benefit_cards(manager, None, account, benefit_id)
    if command == "instance-start":
        return dispatch(account_path + "/instances", {"gpu": body.get("gpu"), "name": body.get("name"),
                                                       "high_mem": body.get("high_mem", False), "confirmed": True})
    if command == "instance-stop":
        return dispatch(account_path + "/instances/stop", {"instance_id": body.get("instance"), "confirmed": True})

    job = {"id": body.get("job_id"), "kind": command, "mode": "colab", "title": body.get("title"),
           "text": body.get("text"), "account_id": account_id, "instance_id": body.get("instance"), "confirmed": True}
    if command == "tts":
        job["instruction"] = body.get("instruction", "")
    elif command == "deploy":
        job.update(project=body.get("project"), legacy_nodes=body.get("legacy_nodes", False))
    else:
        job.update({key: body.get(key) for key in ("ratio", "seconds", "seed")})
        job["engine"] = body.get("engine", "Wan2.2")
    code, payload = dispatch("/api/production/jobs", job)
    return code, {"job": agent_job(production, payload["job"])}
