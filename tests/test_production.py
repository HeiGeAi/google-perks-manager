import json
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google_manager.accounts import UserError, write_private_json
from google_manager.colab import Colab
from google_manager.manager import Manager
from google_manager.oauth import COLAB_SCOPE, EMAIL_SCOPE


class ProductionTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.manager = Manager(self.directory.name, Path("benefits.json"))
        self.addCleanup(lambda: self.manager.store.close())
        self.production = self.manager.production

    def cloud_profile(self, name, email):
        store = self.manager.store
        profile = store.create(name, email)
        account_id = profile["id"]
        store.bind(account_id, name, {"access_token": "fixture"})
        store.update(account_id, observation={**profile["observation"], "instances": [{"id": "machine", "gpu": "G4"}]})
        return account_id

    def test_pending_submission_is_idempotent_and_scoped(self):
        a = self.cloud_profile("A", "first@example.test")
        b = self.cloud_profile("B", "second@example.test")
        body = {"id": "1" * 32, "title": "短片", "kind": "video", "mode": "colab",
                "account_id": a, "instance_id": "machine", "text": "一只纸船", "seed": 42, "confirmed": True}
        with patch.object(self.production, "_launch") as launch:
            self.production.start(body)
            self.production.start(body)
            launch.assert_called_once()
            with self.assertRaisesRegex(UserError, "已经用过"):
                self.production.start({**body, "account_id": b})
        self.assertEqual(len(self.production.list(a)), 1)
        self.assertEqual(self.production.list(b), [])
        with self.assertRaisesRegex(UserError, "未取回"):
            self.production.allow_release(a, "machine", False)
        self.production.allow_release(b, "machine", False)

    def test_restart_recovery_reads_remote_state_without_resubmitting(self):
        account = self.cloud_profile("A", "first@example.test")
        with patch.object(self.production, "_launch"):
            row = self.production.start(
                {"id": "2" * 32, "title": "配音", "kind": "tts", "mode": "colab",
                 "account_id": account, "instance_id": "machine", "text": "早上好", "confirmed": True}
            )
        self.manager.store.close()
        self.manager = Manager(self.directory.name, Path("benefits.json"))
        self.production = self.manager.production
        recovered = self.production.get(row["id"])
        self.assertEqual(recovered["state"], "check")
        with patch.object(self.production, "_launch") as launch:
            self.production.refresh(row["id"])
            launch.assert_called_once_with(recovered, submit=False)

    def test_import_uses_google_identity_and_keeps_source_file(self):
        source = Path(self.directory.name) / "cli.json"
        write_private_json(source, {
            "client_id": "fixture.apps.googleusercontent.com", "client_secret": "public-client-fixture",
            "refresh_token": "original-fixture", "scopes": [COLAB_SCOPE, EMAIL_SCOPE],
        })
        initial = source.read_bytes()
        profile = self.manager.store.create("A", "first@example.test")
        with patch("google_manager.oauth.google_json", side_effect=[
            {"access_token": "fixture-access", "expires_in": 3600},
            {"email": "first@example.test", "email_verified": True, "sub": "google-account"},
        ]):
            self.manager.import_cli(profile["id"], str(source))
        self.assertEqual(self.manager.store.one(profile["id"])["subject"], "google-account")
        self.assertEqual(source.read_bytes(), initial)

    def test_successful_observation_can_include_a_usage_warning(self):
        client = Colab(python="/fixture/python")
        for warning in (None, "已读取实例，算力余额待核对。"):
            observed = {"instances": [], "balance_cu": None, "error": warning}
            reply = SimpleNamespace(returncode=0, stdout=json.dumps(observed))
            with patch("google_manager.colab.subprocess.run", return_value=reply):
                self.assertEqual(client.observe("fixture"), observed)

    def test_new_audio_job_requires_colab(self):
        body = {"kind": "tts", "mode": "mac", "text": "你好"}
        with self.assertRaisesRegex(UserError, "Colab GPU"):
            self.production.spec(body)
        spec = self.production.spec({**body, "mode": "colab"})
        self.assertEqual(spec["engine"], "Breeze TTS 2")


if __name__ == "__main__":
    unittest.main()
