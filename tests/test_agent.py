import http.client
import io
import json
import os
import tempfile
import threading
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from google_manager import agent
from google_manager.accounts import private_directory, timestamp, write_private_json
from google_manager.agent_service import AGENT_HEADER, RUNTIME_FILE
from google_manager.local_files import owned
from google_manager.manager import Manager
from google_manager.server import CONTACT_ROUTE, PRODUCT_ID, UPDATE_HEADER, LocalServer


class AgentTests(unittest.TestCase):
    def setUp(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        path = Path(temporary.name)
        self.data = path / "workspace"
        self.web = path / "web"
        self.web.mkdir()
        catalog = path / "benefits.json"
        catalog.write_text(json.dumps([
            {"id": "test-available", "title": "测试权益", "claim_url": "https://one.google.com/benefits",
             "plan_values": {"pro": "10", "ultra-5x": "40", "ultra-20x": "100"}},
            {"id": "test-ultra", "title": "Ultra 测试", "plan_values": {"ultra-20x": "2000"}},
            {"id": "test-independent", "title": "单独资格", "independent": True},
        ]))
        self.manager = Manager(self.data, catalog)
        self.addCleanup(self.manager.store.close)
        self.server = LocalServer(self.manager, 0, self.web, update_token="test-update")
        self.addCleanup(self.server.server_close)
        self.origin = f"http://127.0.0.1:{self.server.server_port}"
        discovery = self.server.agent_discovery()
        discovery.__enter__()
        self.addCleanup(discovery.__exit__, None, None, None)
        worker = threading.Thread(target=self.server.serve_forever, kwargs={"poll_interval": 0.01}, daemon=True)
        worker.start()
        self.addCleanup(worker.join, 2)
        self.addCleanup(self.server.shutdown)

    def http(self, method, path, body=None, headers=None):
        if body is not None:
            body = json.dumps(body)
        connection = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=3)
        try:
            connection.request(method, path, body, headers or {})
            response = connection.getresponse()
            return response.status, response.read()
        finally:
            connection.close()

    def post(self, path, body, **headers):
        code, result = self.http("POST", path, body, {
            "Content-Type": "application/json", "Origin": self.origin, **headers,
        })
        return code, json.loads(result)

    def cli(self, *arguments):
        output = io.StringIO()
        with redirect_stdout(output):
            result = agent.main(["--data-dir", str(self.data), *arguments])
        return result, json.loads(output.getvalue())

    def profile(self, alias="A", email="a@example.test"):
        return self.manager.store.create(alias, email)["id"]

    def test_discovery_and_local_state_projection(self):
        public = "a@example.test"
        private = "test-credential"
        account = self.profile(f"Profile {public}", public)
        self.manager.store.bind(account, "local-test", {"test_key": private})
        obs = self.manager.store.one(account)["observation"]
        self.manager.store.update(account, observation={**obs, "status": "current", "checked_at": timestamp(),
                                                        "balance_cu": 12, "hourly_cu": 3})
        descriptor = self.data / RUNTIME_FILE
        if os.name != "nt":
            self.assertEqual(descriptor.stat().st_mode & 0o777, 0o600)
        else:
            with descriptor.open(encoding="utf-8") as handle:
                self.assertTrue(owned(descriptor, os.fstat(handle.fileno()), private=True))
        self.assertEqual(self.post("/api/agent", {"command": "state"})[0], 403)
        self.assertEqual(self.post("/api/agent", {"command": "state"},
                                   **{AGENT_HEADER: self.server.agent_token, "Origin": "https://outside.test"})[0], 403)
        code, result = self.cli("state", "--account", account)
        self.assertEqual(code, 0)
        self.assertEqual(len(result["accounts"]), 1)
        self.assertEqual(result["accounts"][0]["auth_status"], "connected")
        self.assertEqual(result["accounts"][0]["observation"]["balance_cu"], 12)
        for value in (public, private, self.server.agent_token):
            self.assertNotIn(value, json.dumps(result))

    def test_catalog_translates_without_overriding_quota_or_links(self):
        account = self.profile()
        (self.web / "benefits.en.json").write_text(json.dumps({
            "test-available": {"title": "Test benefit", "plan_values": {"pro": "Ten"},
                               "claim_url": "https://outside.test"},
        }))
        result, catalog = self.cli("benefits", "--plan", "pro", "--account", account)
        self.assertEqual(result, 0)
        self.assertEqual([row["id"] for row in catalog["benefits"]], ["test-available", "test-independent"])
        card = catalog["benefits"][0]
        self.assertEqual(card["title"], "Test benefit")
        self.assertEqual(card["claim_url"], "https://one.google.com/benefits")
        self.assertEqual(card["plan_values"], {"pro": "Ten", "ultra-5x": "40", "ultra-20x": "100"})
        result, card = self.cli("benefit", "test-ultra", "--plan", "pro", "--language", "zh-CN")
        self.assertEqual(result, 0)
        self.assertFalse(card["benefit"]["listed_for_plan"])

    def test_account_and_claim_confirmation_is_enforced_on_the_server(self):
        body = {"command": "account-add", "name": "Fixture", "email": "demo@example.test"}
        self.assertEqual(self.post("/api/agent", body, **{AGENT_HEADER: self.server.agent_token})[0], 403)
        self.assertEqual(self.manager.store.all(), [])
        result, record = self.cli("account-add", "--name", "Fixture", "--email", "demo@example.test", "--confirm")
        self.assertEqual(result, 0)
        account = record["account_id"]
        result, record = self.cli("claim-record", account, "test-available", "--status", "claimed", "--confirm")
        self.assertEqual(result, 0)
        self.assertEqual(record["benefit"]["local_record"]["status"], "claimed")
        result, record = self.cli("benefit", "test-available", "--account", self.profile())
        self.assertEqual(result, 0)
        self.assertIsNone(record["benefit"]["local_record"])

    def test_oauth_opens_a_google_address_and_keeps_url_out_of_cli_output(self):
        account = self.profile()
        url = "https://accounts.google.com/o/oauth2/v2/auth?state=fixture-state&login_hint=a%40example.test"
        with patch.object(self.manager, "connect", return_value={"open_url": url}), patch.object(agent, "open_consent") as open_browser:
            result, status = self.cli("connect", account, "--confirm")
        self.assertEqual(result, 0)
        self.assertTrue(status["authorization_started"])
        self.assertNotIn("fixture-state", json.dumps(status))
        open_browser.assert_called_once_with("https://accounts.google.com/o/oauth2/v2/auth?state=fixture-state")

    def test_jobs_are_scoped_and_repeat_uses_the_same_video_seed(self):
        account = self.profile()
        other = self.profile("Different", "b@example.test")
        self.manager.store.bind(account, "fixture-user", {"test_key": "local-fixture"})
        obs = self.manager.store.one(account)["observation"]
        self.manager.store.update(account, observation={**obs, "instances": [
            {"id": "fixture-runtime", "name": "L4 fixture", "gpu": "L4", "region": "local-test"},
        ]})
        job = "1" * 32
        command = ("video", account, "fixture-runtime", "--title", "Fixture video", "--text", "Test prompt",
                   "--job-id", job, "--confirm")
        with patch.object(self.manager.production, "_launch") as launch:
            self.assertEqual(self.cli(*command)[0], 0)
            self.assertEqual(self.cli(*command)[0], 0)
            launch.assert_called_once()
        self.assertEqual(self.cli("job", job, "--account", other)[1]["status"], 404)
        self.assertEqual(self.cli("collect", job, "--account", other)[1]["status"], 404)
        self.assertEqual(self.cli("jobs")[1]["jobs"], [])
        folder = self.manager.production.root / job
        artifact = folder / "output.mp4"
        artifact.write_bytes(b"fixture")
        self.manager.production.update(job, state="done", artifact={"name": "output.mp4", "size": 7})
        done = self.cli("job", job, "--account", account)[1]["job"]
        self.assertEqual(done["artifact"]["path"], str(artifact.resolve()))
        self.assertNotIn("spec", done)
        self.assertNotIn("Test prompt", json.dumps(done))

    def test_update_gate_blocks_mutations_and_can_release_them(self):
        prepare = "/api/update/prepare"
        header = {UPDATE_HEADER: "test-update"}
        self.assertEqual(self.post(prepare, {})[0], 403)
        with self.server.mutation():
            self.assertEqual(self.post(prepare, {}, **header)[0], 409)
        self.assertEqual(self.post(prepare, {}, **header), (200, {"ready": True}))
        self.assertEqual(self.cli("state")[0], 0)
        self.assertEqual(self.cli("account-add", "--name", "Fixture", "--email", "a@example.test",
                                  "--confirm")[1]["status"], 409)
        self.assertEqual(self.post("/api/accounts", {"name": "Fixture", "email": "a@example.test"})[0], 409)
        self.assertEqual(self.http("GET", "/oauth/callback")[0], 409)
        self.assertEqual(self.post("/api/update/cancel", {}, **header), (200, {"ready": False}))
        account = self.profile()
        self.manager.store.update(account, operation_pending={"name": "Fixture pending"})
        self.assertEqual(self.post(prepare, {}, **header)[0], 409)
        self.manager.store.update(account, operation_pending=None)
        job = "2" * 32
        folder = self.manager.production.root / job
        private_directory(folder)
        write_private_json(folder / "job.json", {"id": job, "state": "check"})
        self.assertEqual(self.post(prepare, {}, **header)[0], 409)

    def test_contact_qr_uses_only_private_branding_and_exposes_presence(self):
        public = self.web / "brand"
        public.mkdir()
        fake_png = b"\x89PNG\r\n\x1a\n"
        (public / "wechat-contact.png").write_bytes(fake_png + b"public placeholder")
        status, data = self.http("GET", "/api/health?lang=en")
        self.assertEqual(status, 200)
        health = json.loads(data)
        self.assertEqual(health["product"], "Google Perks Manager")
        self.assertEqual(health["product_id"], PRODUCT_ID)
        self.assertFalse(health["has_contact_qr"])
        self.assertEqual(self.http("GET", CONTACT_ROUTE)[0], 404)
        brand = self.data / "branding"
        private_directory(brand)
        local = fake_png + b"local fixture"
        (brand / "wechat-contact.png").write_bytes(local)
        self.assertEqual(self.http("GET", CONTACT_ROUTE), (200, local))
        self.assertTrue(json.loads(self.http("GET", "/api/health")[1])["has_contact_qr"])


if __name__ == "__main__":
    unittest.main()
