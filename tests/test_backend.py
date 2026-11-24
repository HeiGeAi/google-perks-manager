import base64
import hashlib
import http.client
import io
import json
import os
import sys
import tempfile
import threading
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock, patch
from urllib.error import HTTPError, URLError
from urllib.parse import parse_qs, urlsplit

from paramiko import Transport

from google_manager.accounts import UserError
from google_manager.colab import Colab, ProviderError
from google_manager.colab_auth import CLI_REDIRECT
from google_manager.colab_worker import observe, region_from_provider
from google_manager.editor_tunnel import EditorListener, WebSocketStream
from google_manager.local_files import owned
from google_manager.manager import ACK_VISIBILITY_SECONDS, Manager
from google_manager.oauth import COLAB_SCOPE, EMAIL_SCOPE, TOKEN_URL, USERINFO_URL, GoogleOAuth
from google_manager.server import LocalServer


class FixtureOAuth:
    def connecting(self, _account_id):
        return False

    def access(self, account, store):
        saved = store.credential(account["id"])
        if not saved:
            raise UserError("请先连接这个 Google 账号。", 401)
        return saved["test_token"]


class FixtureColab:
    def __init__(self):
        self.instances = {"fixture-a": [], "fixture-b": [{"id": "gpu-other", "gpu": "L4", "region": "us-east1"}]}
        self.started = []
        self.stopped = []
        self.failed_once = False
        self.observe_error = False
        self.hide_start = False

    def observe(self, token):
        if self.observe_error:
            raise ProviderError("模拟刷新失败。")
        return {
            "instances": self.instances[token].copy(),
            "balance_cu": 25 if token == "fixture-a" else 47,
            "hourly_cu": 0 if not self.instances[token] else 4.5,
            "eligible_gpus": [],
        }

    def start(self, token, gpu, nonce, *, high_mem=False):
        self.started.append((token, gpu, nonce))
        if self.failed_once:
            self.failed_once = False
            raise ProviderError("模拟分配回包超时。", 504, uncertain=True)
        if not self.hide_start:
            self.instances[token].append({"id": "gpu-created", "gpu": gpu, "region": "asia-southeast1"})
        return "gpu-created"

    def stop(self, token, instance_id):
        if not any(item["id"] == instance_id for item in self.instances[token]):
            raise ProviderError("模拟实例不在该账号。", 404)
        self.stopped.append((token, instance_id))
        self.instances[token] = [item for item in self.instances[token] if item["id"] != instance_id]


class BackendTests(unittest.TestCase):
    def setUp(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        self.data = Path(tmp.name)
        self.catalog = self.data / "benefits.json"
        self.catalog.write_text(json.dumps([{"id": "drive", "steps": ["核对账号"], "action_label": "打开领取页"}]))
        self.provider = FixtureColab()
        self.manager = Manager(self.data / "profiles", self.catalog, oauth=FixtureOAuth(), colab=self.provider)
        self.addCleanup(self.manager.store.close)

    def accounts(self):
        a = self.manager.store.create("家里", "a@example.com")
        b = self.manager.store.create("工作", "b@example.com")
        self.manager.store.bind(a["id"], "id-a", {"test_token": "fixture-a"})
        self.manager.store.bind(b["id"], "id-b", {"test_token": "fixture-b"})
        return a["id"], b["id"]

    def test_balances_and_stops_are_bound_to_account(self):
        a, b = self.accounts()
        self.assertEqual(self.manager.refresh(a)["balance_cu"], 25)
        self.assertEqual(self.manager.refresh(b)["balance_cu"], 47)
        with self.assertRaises(UserError):
            self.manager.stop(a, {"instance_id": "gpu-other", "confirmed": True})
        with self.assertRaises(UserError):
            self.manager.stop(b, {"instance_id": "gpu-other", "confirmed": "true"})
        self.assertEqual(self.provider.stopped, [])
        operation = self.manager.stop(b, {"instance_id": "gpu-other", "confirmed": True})
        self.assertEqual(operation["status"], "confirmed")
        self.assertEqual(self.provider.stopped, [("fixture-b", "gpu-other")])
        self.assertEqual(self.manager.store.one(a)["observation"]["balance_cu"], 25)

    def test_stop_does_not_depend_on_balance_refresh(self):
        _, b = self.accounts()
        self.provider.observe_error = True
        operation = self.manager.stop(b, {"instance_id": "gpu-other", "confirmed": True})
        self.assertEqual(self.provider.stopped, [("fixture-b", "gpu-other")])
        self.assertEqual(operation["status"], "pending")
        self.assertIsNone(self.manager.store.one(b)["observation"]["hourly_cu"])

    def test_stop_confirms_release_while_editor_transport_is_still_running(self):
        _, account = self.accounts()
        connection = Mock(connected=True)
        connection.shutdown.side_effect = lambda: setattr(connection, "connected", False)
        transport = Transport(WebSocketStream(connection, "fixture.example.test"))
        listener = EditorListener(transport, 18898)
        self.addCleanup(listener.close)
        self.manager.editor_tunnels.listeners[(account, "gpu-other")] = listener
        transport.active = True

        with patch.object(transport, "is_alive", return_value=True):
            operation = self.manager.stop(account, {"instance_id": "gpu-other", "confirmed": True})

        self.assertEqual(operation["status"], "confirmed")
        self.assertEqual(self.manager.store.one(account)["observation"]["instances"], [])
        self.assertFalse(self.manager.editor_tunnels.listeners)
        self.assertFalse(connection.connected)

    def test_uncertain_start_reuses_request_and_guards_duplicate(self):
        a, _ = self.accounts()
        request = {"name": "一号机", "gpu": "T4", "confirmed": True}
        self.provider.failed_once = True
        with self.assertRaises(ProviderError):
            self.manager.start(a, request)
        self.assertTrue(self.manager.state()["accounts"][0]["operation_pending"])
        with self.assertRaises(UserError):
            self.manager.start(a, {**request, "name": "另一台"})
        self.assertEqual(len(self.provider.started), 1)
        result = self.manager.start(a, request)
        self.assertEqual(result["status"], "confirmed")
        self.assertEqual(self.provider.started[0][2], self.provider.started[1][2])
        with self.assertRaises(UserError):
            self.manager.start(a, request)
        self.assertEqual(len(self.provider.started), 2)

    def test_refresh_recovers_allocation_from_original_nonce_without_another_start(self):
        a, b = self.accounts()
        request = {"name": "H3 高内存", "gpu": "A100", "high_mem": True, "confirmed": True}
        self.provider.failed_once = True
        with self.assertRaises(ProviderError):
            self.manager.start(a, request)
        original_nonce = self.provider.started[0][2]
        self.provider.instances["fixture-a"] = [{"id": "gpu-late", "gpu": "A100", "region": "asia-southeast1"}]
        with patch.object(self.provider, "inspect_start", create=True, side_effect=ProviderError("核验暂不可用")):
            observation = self.manager.refresh(a)
        self.assertEqual(observation["status"], "current")
        self.assertTrue(self.manager.store.one(a)["operation_pending"])
        with patch.object(self.provider, "inspect_start", create=True, return_value="gpu-late") as inspect:
            self.manager.refresh(b)
            observation = self.manager.refresh(a)
        inspect.assert_called_once_with("fixture-a", "A100", original_nonce, high_mem=True)
        self.assertEqual(observation["recovered_launch"], {"name": "H3 高内存", "instance_id": "gpu-late"})
        self.assertEqual(observation["instances"][0]["name"], "H3 高内存")
        self.assertIsNone(self.manager.store.one(a)["operation_pending"])
        self.assertEqual(len(self.provider.started), 1)

    def test_gpu_change_reconciles_prior_request_first(self):
        a, _ = self.accounts()
        first = {"name": "验收机", "gpu": "G4", "confirmed": True}
        self.provider.failed_once = True
        with self.assertRaises(ProviderError):
            self.manager.start(a, first)
        old_nonce = self.provider.started[0][2]
        with patch.object(self.provider, "inspect_start", create=True, return_value=None) as inspect:
            result = self.manager.start(a, {**first, "gpu": "L4"})
        inspect.assert_called_once_with("fixture-a", "G4", old_nonce)
        self.assertEqual(result["status"], "confirmed")
        self.assertNotEqual(self.provider.started[-1][2], old_nonce)

    def test_gpu_change_keeps_previous_allocation_when_acknowledged(self):
        a, _ = self.accounts()
        first = {"name": "验收机", "gpu": "G4", "confirmed": True}
        self.provider.failed_once = True
        with self.assertRaises(ProviderError):
            self.manager.start(a, first)
        with patch.object(self.provider, "inspect_start", create=True, return_value="old-gpu"):
            with self.assertRaisesRegex(UserError, "已给上一请求分配"):
                self.manager.start(a, {**first, "gpu": "L4"})
        self.assertEqual(len(self.provider.started), 1)
        self.assertEqual(self.manager.store.one(a)["operation_pending"]["instance_id"], "old-gpu")

    def test_confirmed_ack_waits_and_releases_after_fresh_absence(self):
        a, _ = self.accounts()
        request = {"name": "一号机", "gpu": "T4", "confirmed": True}
        self.provider.hide_start = True
        first = self.manager.start(a, request)
        self.assertEqual(first["status"], "pending")
        self.assertEqual(self.manager.start(a, request)["instance_id"], first["instance_id"])
        self.assertEqual(len(self.provider.started), 1)

        pending = self.manager.store.one(a)["operation_pending"]
        acknowledged_at = datetime.now(timezone.utc) - timedelta(seconds=ACK_VISIBILITY_SECONDS + 1)
        self.manager.store.update(a, operation_pending={**pending, "acknowledged_at": acknowledged_at.isoformat()})
        self.provider.observe_error = True
        with self.assertRaises(ProviderError):
            self.manager.refresh(a)
        self.assertTrue(self.manager.state()["accounts"][0]["operation_pending"])
        self.provider.observe_error = False
        with self.assertRaises(UserError) as expired:
            self.manager.start(a, request)
        self.assertEqual(expired.exception.status, 409)
        self.assertEqual(len(self.provider.started), 1)
        self.assertFalse(self.manager.state()["accounts"][0]["operation_pending"])

        self.provider.hide_start = False
        self.assertEqual(self.manager.start(a, request)["status"], "confirmed")
        self.assertNotEqual(self.provider.started[0][2], self.provider.started[1][2])

    def test_benefit_state_is_separate_and_private(self):
        a, b = self.accounts()
        self.manager.update_benefit(a, "drive", {"status": "claimed", "note": "已核对"})
        state = self.manager.state()
        self.assertEqual(state["benefits"][0]["steps"], ["核对账号"])
        self.assertEqual(self.manager.store.one(a)["benefit_status"]["drive"]["source"], "user")
        self.assertEqual(self.manager.store.one(b)["benefit_status"], {})
        auth_file = self.manager.store.profiles / a / "oauth.json"
        if os.name != "nt":
            self.assertEqual(auth_file.stat().st_mode & 0o777, 0o600)
        else:
            self.assertTrue(owned(auth_file, auth_file.stat(), private=True))
        self.assertNotIn("fixture-a", json.dumps(state))
        self.assertEqual(state["accounts"][0]["observation"]["status"], "unverified")

    def test_local_http_and_cross_origin_guard(self):
        web = self.data / "web"
        web.mkdir()
        (web / "index.html").write_text("<title>谷歌管理器</title>", encoding="utf-8")
        with LocalServer(self.manager, 0, web) as server:
            thread = threading.Thread(target=server.serve_forever, daemon=True)
            thread.start()
            try:
                connection = http.client.HTTPConnection("127.0.0.1", server.server_port, timeout=3)
                self.addCleanup(connection.close)
                origin = f"http://127.0.0.1:{server.server_port}"
                connection.request("GET", "/api/health")
                self.assertEqual(json.loads(connection.getresponse().read())["status"], "ok")
                connection.request("GET", "/")
                self.assertIn("谷歌管理器", connection.getresponse().read().decode())
                connection.request("GET", "/../benefits.json")
                self.assertEqual(connection.getresponse().status, 404)
                body = json.dumps({"name": "测试账号", "email": "another@example.com"})
                for headers in [
                    {"Origin": "https://example.com", "Content-Type": "application/json"},
                    {"Content-Type": "application/json"},
                    {"Host": "example.com", "Origin": origin, "Content-Type": "application/json"},
                ]:
                    connection.request("POST", "/api/accounts", body, headers)
                    refused = connection.getresponse()
                    self.assertEqual(refused.status, 403)
                    refused.read()
                connection.request("POST", "/api/accounts", body, {"Origin": origin, "Content-Type": "application/json"})
                created = connection.getresponse()
                self.assertEqual(created.status, 201)
                self.assertEqual(json.loads(created.read())["accounts"][0]["auth_status"], "disconnected")
            finally:
                server.shutdown()
                thread.join()


class OAuthTests(unittest.TestCase):
    client = {"client_id": "fixture.apps.googleusercontent.com", "client_secret": "fixture"}
    verifier = "v" * 64
    cli_url = "https://accounts.google.com/o/oauth2/auth"

    def test_login_uses_pkce_and_consumes_state_once(self):
        oauth = GoogleOAuth()
        client = {"client_id": "fixture.apps.googleusercontent.com", "client_secret": "fixture"}
        account = {"email": "a@example.com", "id": "a", "subject": None}
        with patch("google_manager.oauth.read_desktop_client", return_value=client):
            url = oauth.begin(account, "/fixture", "http://127.0.0.1:18782/oauth/callback")["open_url"]
        query = parse_qs(urlsplit(url).query)
        self.assertEqual(urlsplit(url).hostname, "accounts.google.com")
        self.assertEqual(query["login_hint"], ["a@example.com"])
        self.assertIn(COLAB_SCOPE, query["scope"][0])
        self.assertIn(EMAIL_SCOPE, query["scope"][0])
        self.assertEqual(query["code_challenge_method"], ["S256"])
        login = oauth.consume(query["state"][0])
        expected = base64.urlsafe_b64encode(hashlib.sha256(login.verifier.encode()).digest()).rstrip(b"=").decode()
        self.assertEqual(query["code_challenge"], [expected])
        self.assertNotIn("client_secret", query)
        with self.assertRaises(UserError):
            oauth.consume(query["state"][0])
        with patch("google_manager.oauth.google_json", side_effect=[
            {"access_token": "fixture", "refresh_token": "fixture-r", "expires_in": 3600},
            {"email": "different@example.com", "sub": "different", "email_verified": True},
        ]):
            with self.assertRaises(UserError):
                oauth.exchange(account, login, "fixture-code")

    def test_release_can_be_confirmed_when_fee_query_fails(self):
        class Client:
            def list_assignments(self):
                return []

            def get_consumption_user_info(self):
                raise ProviderError("模拟无法读取费率。")

        result = observe(Client())
        self.assertEqual(result["instances"], [])
        self.assertIsNone(result["hourly_cu"])
        self.assertTrue(result["error"])

    def test_unbind_invalidates_callback_before_state_consumption(self):
        oauth = GoogleOAuth()
        account = {"id": "a", "email": "a@example.com"}
        with patch("google_manager.oauth.read_desktop_client", return_value={"client_id": "fixture"}):
            opened = oauth.begin(account, "fixture", "http://127.0.0.1:18782/oauth/callback")["open_url"]
        state = parse_qs(urlsplit(opened).query)["state"][0]
        self.assertEqual(oauth.account_for_state(state), "a")
        oauth.cancel("a")
        with self.assertRaises(UserError):
            oauth.consume(state)

    def test_cli_code_verifies_the_selected_profile_before_saving(self):
        tmp = tempfile.TemporaryDirectory()
        self.addCleanup(tmp.cleanup)
        provider = Mock()
        provider.authorize.return_value = (self.cli_url, self.client, self.verifier)
        payload = {"access_token": "fixture-access", "refresh_token": "fixture-refresh",
                   "expires_in": 3600, "scope": f"openid {COLAB_SCOPE} {EMAIL_SCOPE}"}
        manager = Manager(Path(tmp.name) / "data", Path(tmp.name) / "missing-catalog", colab=provider)
        self.addCleanup(manager.store.close)
        self.addCleanup(manager.oauth.close)
        existing = manager.store.create("Already connected", "existing@example.com")["id"]
        target = manager.store.create("New profile", "new@example.com")["id"]
        manager.store.bind(existing, "existing-provider-id", {"access_token": "existing-token"})
        connect = manager.connect(target, {}, "http://127.0.0.1:18782/oauth/callback")
        self.assertEqual(connect["flow"], "cli")
        provider.authorize.assert_called_once_with()
        with patch("google_manager.oauth.google_json", side_effect=[
            payload, {"email": "existing@example.com", "sub": "existing-provider-id", "email_verified": True},
        ]) as google:
            with self.assertRaises(UserError):
                manager.finish_cli_login(existing, connect["attempt"], "fixture-code")
            google.assert_not_called()
            with self.assertRaises(UserError):
                manager.finish_cli_login(target, connect["attempt"], "fixture-code")
        self.assertFalse(manager.store.has_credential(target))

        connect = manager.connect(target, {}, "http://127.0.0.1:18782/oauth/callback")
        with patch("google_manager.oauth.google_json", side_effect=[
            payload, {"email": "new@example.com", "sub": "new-provider-id", "email_verified": True},
        ]) as google:
            manager.finish_cli_login(target, connect["attempt"], "fixture-code")
        self.assertEqual(google.call_args_list[0].args, (TOKEN_URL,))
        self.assertEqual(google.call_args_list[0].kwargs["form"], {
            **self.client, "code": "fixture-code", "code_verifier": self.verifier,
            "grant_type": "authorization_code", "redirect_uri": CLI_REDIRECT,
        })
        self.assertEqual(google.call_args_list[1].args, (USERINFO_URL,))
        self.assertEqual(manager.store.credential(existing)["access_token"], "existing-token")
        self.assertEqual(manager.store.credential(target)["access_token"], "fixture-access")
        self.assertNotIn("fixture-access", json.dumps(manager.state()))
        with self.assertRaises(UserError):
            manager.finish_cli_login(target, connect["attempt"], "fixture-code")

    def test_cancelling_an_old_attempt_leaves_a_new_cli_login_alive(self):
        oauth = GoogleOAuth()
        account = {"id": "a", "email": "a@example.com"}
        provider = Mock()
        provider.authorize.return_value = (self.cli_url, self.client, self.verifier)
        older = oauth.begin_cli(account, provider)
        newer = oauth.begin_cli(account, provider)
        self.assertNotEqual(older["attempt"], newer["attempt"])
        oauth.cancel("a", older["attempt"])
        self.assertTrue(oauth.connecting("a"))
        oauth.cancel("a", newer["attempt"])
        self.assertFalse(oauth.connecting("a"))

    def test_cli_worker_prepares_a_matching_pkce_challenge_and_exits(self):
        url, client, verifier = Colab(python=sys.executable).authorize()
        parts = urlsplit(url)
        params = parse_qs(parts.query)
        expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=")
        self.assertEqual(parts.hostname, "accounts.google.com")
        self.assertEqual(params["redirect_uri"], [CLI_REDIRECT])
        self.assertEqual(params["code_challenge_method"], ["S256"])
        self.assertTrue(params["code_challenge"] == [expected.decode("ascii")])
        self.assertTrue(params["client_id"] == [client["client_id"]])
        self.assertEqual(params["token_usage"], ["remote"])
        self.assertNotIn("client_secret", params)
        self.assertNotIn("code_verifier", params)

    def test_cli_timeout_and_rejected_code_allow_a_fresh_attempt(self):
        oauth = GoogleOAuth()
        account = {"id": "a", "email": "a@example.com", "subject": None}
        provider = Mock()
        provider.authorize.return_value = (self.cli_url, self.client, self.verifier)
        first = oauth.begin_cli(account, provider)["attempt"]
        rejected = HTTPError(TOKEN_URL, 400, "Bad Request", {},
                             io.BytesIO(b'{"error":"invalid_grant","error_description":"private diagnostic"}'))
        with patch("google_manager.oauth.build_opener") as build_opener:
            request = build_opener.return_value.open
            request.side_effect = [URLError(TimeoutError("private diagnostic")), rejected]
            with self.assertRaises(UserError) as failed:
                oauth.finish_cli(account, first, "fixture-code")
            self.assertEqual(failed.exception.status, 504)
            self.assertIn("超时", str(failed.exception))
            self.assertNotIn("private diagnostic", str(failed.exception))
            with self.assertRaises(UserError) as expired:
                oauth.finish_cli(account, first, "fixture-code")
            self.assertEqual(expired.exception.status, 409)
            request.assert_called_once()
            second = oauth.begin_cli(account, provider)["attempt"]
            self.assertNotEqual(first, second)
            with self.assertRaises(UserError) as denied:
                oauth.finish_cli(account, second, "fixture-code")
            self.assertEqual(denied.exception.status, 401)
            self.assertIn("拒绝", str(denied.exception))
            self.assertNotIn("private diagnostic", str(denied.exception))
            self.assertEqual(request.call_count, 2)
        self.assertFalse(oauth.connecting("a"))

    def test_region_only_from_https_provider_host(self):
        self.assertEqual(region_from_provider("https://gpu.asia-southeast1-a.prod.r.colab.dev"), "asia-southeast1")
        self.assertIsNone(region_from_provider("https://gpu.us-east1-a.prod.r.colab.dev.example.com"))
        self.assertIsNone(region_from_provider("http://gpu.us-east1-a.prod.r.colab.dev"))
        self.assertIsNone(region_from_provider("https://example.com/us-east1"))

    def test_cli_worker_does_not_pass_grant_in_arguments(self):
        mock = SimpleNamespace(returncode=0, stdout='{"instances":[]}')
        with patch("google_manager.colab.subprocess.run", return_value=mock) as run:
            self.assertEqual(Colab(python="/fixture/python").observe("fixture-only"), {"instances": []})
        self.assertNotIn("fixture-only", run.call_args.args[0])
        self.assertEqual(json.loads(run.call_args.kwargs["input"])["access_token"], "fixture-only")


if __name__ == "__main__":
    unittest.main()
