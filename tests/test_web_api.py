"""Web panel API: credential redaction, GPU usage estimate, run-time cap."""

import json
import shutil
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

from kaggle_fleet import config
from kaggle_fleet.account_manager import AccountManager
from kaggle_fleet.kaggle_runner import KaggleRunner, quota_info, runtime_from_logs
from kaggle_fleet.models import JobRecord, JobStatus
from kaggle_fleet.notebook_manager import NotebookManager
from kaggle_fleet.server.api_handlers import ApiDispatcher
from kaggle_fleet.storage import StorageEngine


class TestWebApi(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp())
        nb_dir = self.tmp / "nbs"
        nb_dir.mkdir()
        (nb_dir / "demo.ipynb").write_text(json.dumps(
            {"cells": [], "metadata": {}, "nbformat": 4, "nbformat_minor": 5}))
        self.storage = StorageEngine(base_dir=self.tmp / "hub")
        self.mgr = AccountManager(storage=self.storage)
        self.runner = KaggleRunner(
            account_manager=self.mgr,
            notebook_manager=NotebookManager(notebooks_dir=nb_dir, staging_dir=self.tmp / "st"),
            storage=self.storage)
        self.api = ApiDispatcher(account_manager=self.mgr, notebook_manager=self.runner.notebook_manager,
                                 runner=self.runner, storage=self.storage)
        self.acc = self.mgr.add_account("A", "alpha", "oauth_token",
                                        {"refresh_token": "SECRET", "username": "alpha"}, make_active=True)

    def tearDown(self):
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_credentials_reach_the_browser(self):
        responses = [
            self.api.dispatch("GET", "/api/accounts"),
            self.api.dispatch("GET", f"/api/accounts/{self.acc.id}"),
            self.api.dispatch("POST", f"/api/accounts/{self.acc.id}/switch"),
            self.api.dispatch("GET", "/api/status"),
            self.api.dispatch("POST", "/api/accounts",
                              body_data={"username": "beta", "api_key": "KEYSECRET"}),
        ]
        for status, body in responses:
            self.assertLess(status, 300, body)
            dumped = json.dumps(body)
            self.assertNotIn("SECRET", dumped)

    def test_gpu_usage_counts_gpu_runs_in_quota_week_only(self):
        # Wednesday noon; with the default Saturday 00:00 UTC reset the quota
        # week started 108 h earlier (Sat 14 Nov 00:00).
        now = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        def iso(h):
            return (now - timedelta(hours=h)).isoformat()

        def job(i, status, start, end=None, acc="NvidiaTeslaT4", gpu=True, runtime=None):
            return JobRecord(id=i, account_id=self.acc.id, username="alpha", kernel_slug=i,
                             kernel_id=f"alpha/{i}", title=i, notebook_path="", staging_path="",
                             accelerator=acc, enable_gpu=gpu, status=status,
                             started_at=iso(start), completed_at=iso(end) if end is not None else None,
                             runtime_seconds=runtime)

        for j in (job("done", JobStatus.COMPLETE, 5, 3),          # 2h
                  job("live", JobStatus.RUNNING, 1),               # 1h, still running
                  job("queued", JobStatus.QUEUED, 2),              # not using quota yet
                  job("cpu", JobStatus.COMPLETE, 5, 1, acc="None", gpu=False),
                  job("tpu", JobStatus.COMPLETE, 5, 2, acc="TpuV5E8"),            # 3h TPU
                  job("lastweek", JobStatus.COMPLETE, 115, 111),    # before the reset
                  job("edge", JobStatus.COMPLETE, 109, 107),        # 1h after the reset
                  # Hub was offline when it finished: logs say 30 min, not 40 h.
                  job("logged", JobStatus.COMPLETE, 50, 10, runtime=1800),
                  # Never got logs, observed 40 h later: capped at the 12 h limit.
                  job("stale", JobStatus.COMPLETE, 70, 30),
                  # Superseded push that picked up a later run's 1 h log time:
                  # it was seen failing after 6 min, so 0.1 h counts.
                  job("repushed", JobStatus.ERROR, 20, 19.9, runtime=3600)):
            self.runner.save_job(j)

        u = self.runner.gpu_usage_by_account(now=now)[self.acc.id]
        self.assertAlmostEqual(u["gpu_hours"], 2 + 1 + 1 + 0.5 + 12 + 0.1, delta=0.01)
        self.assertAlmostEqual(u["tpu_hours"], 3.0, delta=0.01)
        self.assertEqual(u["active_jobs"], 2)

    def test_quota_window_and_reset_override(self):
        wed = datetime(2026, 11, 18, 12, 0, tzinfo=timezone.utc)
        start, reset = config.quota_window(wed)
        self.assertEqual(start, datetime(2026, 11, 14, 0, 0, tzinfo=timezone.utc))
        self.assertEqual(reset, datetime(2026, 11, 21, 0, 0, tzinfo=timezone.utc))
        with patch.dict("os.environ", {"KAGGLE_FLEET_QUOTA_RESET": "wed 17:30"}):
            start, reset = config.quota_window(wed)
        self.assertEqual(start, datetime(2026, 11, 11, 17, 30, tzinfo=timezone.utc))
        self.assertEqual(reset, datetime(2026, 11, 18, 17, 30, tzinfo=timezone.utc))
        with patch.dict("os.environ", {"KAGGLE_FLEET_QUOTA_RESET": "garbage"}):
            self.assertEqual(config.quota_reset_schedule(), (5, 0, 0))
        info = quota_info(wed)
        self.assertEqual(info["quota_reset_at"], "2026-11-21T00:00:00+00:00")
        self.assertEqual(info["quota_resets_in_seconds"], 60 * 3600)

    @patch("subprocess.run")
    def test_refresh_skips_jobs_replaced_by_a_later_push(self, run):
        for i, created in (("old", "2026-11-18T00:00:00+00:00"), ("new", "2026-11-18T01:00:00+00:00")):
            self.runner.save_job(JobRecord(
                id=i, account_id=self.acc.id, username="alpha", kernel_slug="same",
                kernel_id="alpha/same", title=i, notebook_path="", staging_path="",
                status=JobStatus.COMPLETE, created_at=created, started_at=created,
                completed_at=created.replace("T0", "T1")))
        run.return_value = MagicMock(returncode=0, stdout='[{"time":600.0}]', stderr="")
        with patch("kaggle_fleet.config.quota_window",
                   return_value=(datetime(2026, 11, 14, tzinfo=timezone.utc),
                                 datetime(2026, 11, 21, tzinfo=timezone.utc))):
            self.assertEqual(self.runner.refresh_runtimes(), 1)
        self.assertIsNone(self.runner.get_job("old").runtime_seconds)
        self.assertEqual(self.runner.get_job("new").runtime_seconds, 600.0)

    def test_runtime_from_logs(self):
        logs = ('[{"stream_name":"stdout","time":3.5,"data":"a"}\n'
                ',{"stream_name":"stderr","time":1942.25,"data":"b"}\n'
                ',{"stream_name":"stdout","time": 1900.0,"data":"c"}]')
        self.assertEqual(runtime_from_logs(logs), 1942.25)
        self.assertIsNone(runtime_from_logs("no timestamps here"))

    @patch("subprocess.run")
    def test_completion_records_runtime_from_logs(self, run):
        self.runner.save_job(JobRecord(
            id="j1", account_id=self.acc.id, username="alpha", kernel_slug="j1",
            kernel_id="alpha/j1", title="j1", notebook_path="", staging_path="",
            status=JobStatus.RUNNING, started_at="2026-11-18T00:00:00+00:00"))

        def fake(cmd, *a, **kw):
            if cmd[1:3] == ["kernels", "status"]:
                return MagicMock(returncode=0, stdout='has status "KernelWorkerStatus.COMPLETE"', stderr="")
            return MagicMock(returncode=0, stdout='[{"stream_name":"stdout","time":600.0,"data":"x"}]',
                             stderr="")
        run.side_effect = fake
        job = self.runner.check_status("j1")
        self.assertEqual(job.status, JobStatus.COMPLETE)
        self.assertEqual(job.runtime_seconds, 600.0)

    @patch("subprocess.run")
    def test_launch_passes_runtime_cap_and_pushes_once(self, run):
        run.return_value = MagicMock(returncode=0, stdout="pushed", stderr="")
        with patch.object(self.runner, "run_job_async") as watcher:
            status, body = self.api.dispatch("POST", "/api/runs", body_data={
                "notebook": "demo.ipynb", "account_id": self.acc.id, "max_runtime_hours": "2.5"})
        self.assertEqual(status, 201, body)
        pushes = [c for c in run.call_args_list if c[0][0][1:3] == ["kernels", "push"]]
        self.assertEqual(len(pushes), 1)
        cmd = pushes[0][0][0]
        self.assertEqual(cmd[cmd.index("-t") + 1], "9000")
        watcher.assert_called_once()
        self.assertFalse(watcher.call_args.kwargs.get("push", False))


if __name__ == "__main__":
    unittest.main()
