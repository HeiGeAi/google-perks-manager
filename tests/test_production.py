import hashlib
import json
import tempfile
import unittest
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

from google_manager.accounts import UserError, write_private_json
from google_manager.colab import Colab
from google_manager.drive_archive import WORKSPACE
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

    def test_h3_deployment_requires_compatible_hardware_and_verified_region(self):
        account_id = self.cloud_profile("A", "first@example.test")
        observation = self.manager.store.one(account_id)["observation"]
        allocation = observation["instances"][0]
        body = {"id": "3" * 32, "kind": "deploy", "mode": "colab", "project": "h3",
                "account_id": account_id, "instance_id": "machine", "legacy_nodes": True, "confirmed": True}
        with patch.object(self.production, "_launch") as launch:
            for gpu, region, error in (("G4", None, "机房"), ("G4", "us-east1", "机房"), ("CPU", "asia-southeast1", "算力")):
                with self.subTest(gpu=gpu, region=region):
                    allocation.update(gpu=gpu, region=region)
                    self.manager.store.update(account_id, observation=observation)
                    with self.assertRaisesRegex(UserError, error):
                        self.production.start(body)
            launch.assert_not_called()
            allocation.update(gpu="G4", region="asia-southeast1")
            self.manager.store.update(account_id, observation=observation)
            self.production.start(body)
            self.production.start(body)
            launch.assert_called_once()
        job = self.production.get(body["id"])
        self.assertEqual(job["project"], "h3")
        self.assertIs(job["spec"]["legacy_nodes"], True)

    def test_h3_video_preserves_original_clip_and_aligned_longer_durations(self):
        body = {"kind": "video", "mode": "colab", "text": "云层", "ratio": "portrait", "seed": 10}
        for seconds, frames in ((5, 124), (10, 243), (15, 362)):
            with self.subTest(seconds=seconds):
                spec = self.production.spec({**body, "engine": "MiniMax H3", "seconds": seconds})
                self.assertEqual((spec["engine"], spec["ratio"], spec["frames"]), ("MiniMax H3", "portrait", frames))
        with self.assertRaisesRegex(UserError, "片长"):
            self.production.spec({**body, "engine": "MiniMax H3", "seconds": 3})
        self.assertEqual(self.production.spec(body)["frames"], 121)

    def local_colab_output(self, account):
        with patch.object(self.production, "_launch"):
            job = self.production.start(
                {"id": "6" * 32, "title": "课程素材", "kind": "tts", "mode": "colab",
                 "account_id": account, "instance_id": "machine", "text": "本机素材", "confirmed": True}
            )
        source = self.production.root / job["id"] / "output.wav"
        data = b"downloaded output fixture"
        source.write_bytes(data)
        self.production.update(job["id"], state="done", artifact={
            "name": source.name, "size": len(data), "sha256": hashlib.sha256(data).hexdigest(),
        })
        return job["id"], source

    def test_saved_colab_output_is_available_to_other_accounts(self):
        a = self.cloud_profile("A", "first@example.test")
        b = self.cloud_profile("B", "second@example.test")
        job_id, source = self.local_colab_output(a)
        for account in (None, a, b):
            self.assertEqual([job["id"] for job in self.production.list(account)], [job_id])
        self.assertEqual(self.production.mux_source({"audio_job": job_id, "source_account": b}, "audio", {"tts"}),
                         {"audio_job": job_id})
        source.unlink()
        self.assertEqual(self.production.list(b), [])

    def test_drive_archive_creates_project_and_reuses_verified_copy(self):
        a = self.cloud_profile("A", "first@example.test")
        b = self.cloud_profile("B", "second@example.test")
        job_id, source = self.local_colab_output(a)
        original = source.read_bytes()
        drive = self.manager.drive
        with tempfile.TemporaryDirectory() as sync:
            drive.configure(b, sync)
            self.assertEqual(drive.state(b)["projects"], [])
            self.assertFalse(drive.state(a)["ready"])
            with self.assertRaisesRegex(UserError, "其他账号"):
                drive.configure(a, sync)
            with self.assertRaisesRegex(UserError, "有效的项目"):
                drive.archive(job_id, b, "../outside")
            with ThreadPoolExecutor(max_workers=2) as threads:
                result = list(threads.map(lambda _: drive.archive(job_id, b, "项目甲"), range(2)))
            self.assertEqual(sorted(item["reused"] for item in result), [False, True])
            folder = Path(sync) / WORKSPACE / "项目甲"
            copied = folder / result[0]["filename"]
            self.assertEqual(list(folder.iterdir()), [copied])
            self.assertEqual(copied.read_bytes(), original)
            self.assertEqual(source.read_bytes(), original)
            self.assertEqual(drive.state(b)["last_project"], "项目甲")
            self.assertEqual(self.production.get(job_id)["drive_archive"]["account_id"], b)
            copied.write_bytes(b"other output")
            with self.assertRaisesRegex(UserError, "同名文件"):
                drive.archive(job_id, b, "项目甲")
            self.assertEqual(copied.read_bytes(), b"other output")
            source.write_bytes(b"x" * len(original))
            with self.assertRaisesRegex(UserError, "校验值已变化"):
                drive.archive(job_id, b, "项目乙")
            self.assertEqual(list((Path(sync) / WORKSPACE / "项目乙").iterdir()), [])


if __name__ == "__main__":
    unittest.main()
