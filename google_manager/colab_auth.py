"""Prepare Colab CLI's Google authorization request in its own environment."""

import json
import re
import subprocess
from importlib.resources import files
from urllib.parse import parse_qsl, urlencode, urlsplit, urlunsplit

from .accounts import UserError


LIFETIME = 600
MAX_CODE = 4096
MAX_PACKET = 16 * 1024
UNAVAILABLE = "无法启动 Colab 官方授权，请重新连接 Google。"
CLI_REDIRECT = "https://sdk.cloud.google.com/applicationdefaultauthcode.html"


def google_authorization_url(url):
    parts = urlsplit(url)
    if (parts.scheme != "https" or parts.netloc != "accounts.google.com" or parts.fragment
        or parts.path not in {"/o/oauth2/auth", "/o/oauth2/v2/auth"}):
        raise ValueError
    params = dict(parse_qsl(parts.query))
    params["prompt"] = "consent select_account"
    return urlunsplit(parts._replace(query=urlencode(params)))


def prepare_authorization(command, cwd):
    if not command:
        raise UserError(UNAVAILABLE, 503)
    try:
        result = subprocess.run(
            [*command, "--authorize"], cwd=cwd, stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE, stderr=subprocess.DEVNULL, timeout=12, check=True,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        if len(result.stdout) > MAX_PACKET:
            raise ValueError
        packet = json.loads(result.stdout)
        url = google_authorization_url(packet["url"])
        client_id, client_secret, verifier = packet["client_id"], packet["client_secret"], packet["verifier"]
        params = dict(parse_qsl(urlsplit(url).query))
        if (
            not isinstance(client_id, str) or len(client_id) > 512
            or not client_id.endswith(".apps.googleusercontent.com") or params.get("client_id") != client_id
            or not isinstance(client_secret, str) or len(client_secret) > 512
            or not isinstance(verifier, str) or not re.fullmatch(r"[A-Za-z0-9._~-]{43,128}", verifier)
            or params.get("redirect_uri") != CLI_REDIRECT or params.get("code_challenge_method") != "S256"
        ):
            raise ValueError
        return url, {"client_id": client_id, "client_secret": client_secret}, verifier
    except (OSError, subprocess.SubprocessError, ValueError, KeyError, TypeError, AttributeError):
        raise UserError(UNAVAILABLE, 503) from None


def run_authorization():
    # Exit after generating the challenge. The App exchanges the code through
    # its usual OAuth transport, without keeping a worker or its pipes open.
    from colab_cli import auth

    config = json.loads(files("colab_cli").joinpath("oauth_config.json").read_text(encoding="utf-8"))
    flow = auth.InstalledAppFlow.from_client_config(config, auth.PUBLIC_SCOPES, autogenerate_code_verifier=True)
    flow.redirect_uri = auth.REMOTE_REDIRECT_URI
    url, _ = flow.authorization_url(prompt="consent select_account", token_usage="remote")
    print(json.dumps({
        "url": url,
        "client_id": flow.client_config["client_id"],
        "client_secret": flow.client_config.get("client_secret", ""),
        "verifier": flow.code_verifier,
    }), flush=True)
