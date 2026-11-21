"""
Tests for the agent-facing CLI surface: global --json output and `doctor`.
"""

from __future__ import annotations

import io
import json
import os
import shutil
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import MagicMock, patch

FIXTURE_NOTEBOOKS = Path(__file__).resolve().parent / "fixtures" / "notebooks"


class TestCliJson(unittest.TestCase):
    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="kf_cli_json_"))
        # StorageEngine() defaults to config.KAGGLE_FLEET_DIR; point it at a temp dir.
        self.patches = [
            patch("kaggle_fleet.config.KAGGLE_FLEET_DIR", self.tmp / "state"),
            patch("kaggle_fleet.config.STAGING_DIR", self.tmp / "state" / "staging"),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        shutil.rmtree(self.tmp, ignore_errors=True)

    def run_cli(self, *argv: str) -> tuple[int, object]:
        from kaggle_fleet import cli

        buf = io.StringIO()
        with redirect_stdout(buf):
            code = cli.main(list(argv))
        return code, json.loads(buf.getvalue())

    def test_account_list_empty_and_after_add(self):
        code, data = self.run_cli("account", "list", "--json")
        self.assertEqual(code, 0)
        self.assertEqual(data, {"active_account_id": None, "usage_window_days": 7, "accounts": []})

        code, data = self.run_cli("--json", "account", "add", "-u", "alice", "-k", "secret")
        self.assertEqual(code, 0)
        self.assertEqual(data["account"]["username"], "alice")
        self.assertEqual(data["account"]["credentials"], {}, "secrets must never be printed")

        code, data = self.run_cli("account", "list", "--json")
        self.assertEqual(len(data["accounts"]), 1)
        self.assertTrue(data["accounts"][0]["active"])
        self.assertEqual(data["accounts"][0]["usage"], {"gpu_hours": 0.0, "active_jobs": 0})

    def test_global_flag_position_is_flexible(self):
        a = self.run_cli("--json", "--notebooks-dir", str(FIXTURE_NOTEBOOKS), "notebook", "list")
        b = self.run_cli("notebook", "list", "--json", "--notebooks-dir", str(FIXTURE_NOTEBOOKS))
        self.assertEqual(a, b)
        self.assertEqual(len(a[1]["notebooks"]), 6)

    def test_notebook_inspect_json(self):
        code, data = self.run_cli("notebook", "inspect", "00_data_prep.ipynb", "--json",
                                  "--notebooks-dir", str(FIXTURE_NOTEBOOKS))
        self.assertEqual(code, 0)
        self.assertEqual(data["notebook"]["slug"], "nb-00-data-prep")
        self.assertEqual(data["notebook"]["title"], "Data Preparation")
        self.assertTrue(data["cell_preview"])

    def test_errors_are_json_with_nonzero_exit(self):
        code, data = self.run_cli("run", "status", "nope", "--json")
        self.assertEqual(code, 1)
        self.assertIn("error", data)
        code, data = self.run_cli("account", "switch", "ghost", "--json")
        self.assertEqual(code, 1)
        self.assertIn("error", data)

    @patch("subprocess.run")
    def test_run_push_json(self, run):
        run.return_value = MagicMock(returncode=0, stdout="Kernel version 1 successfully pushed.", stderr="")
        self.run_cli("--json", "account", "add", "-u", "alice", "-k", "secret")
        code, data = self.run_cli("run", "push", "02_train_closed_end.ipynb", "--json",
                                  "--notebooks-dir", str(FIXTURE_NOTEBOOKS),
                                  "--dataset", "alice/data", "--competition", "titanic")
        self.assertEqual(code, 0)
        self.assertTrue(data["success"])
        self.assertEqual(data["job"]["status"], "RUNNING")
        self.assertEqual(data["job"]["kernel_id"], "alice/nb-02-train-closed-end")
        self.assertEqual(data["job"]["competition_sources"], ["titanic"])

        code, data = self.run_cli("run", "list", "--json")
        self.assertEqual(len(data["jobs"]), 1)

    @patch("subprocess.run")
    def test_run_push_rejected_reports_failure(self, run):
        run.return_value = MagicMock(
            returncode=0, stdout="Kernel push error: Maximum batch GPU session count of 2 reached.",
            stderr="")
        self.run_cli("--json", "account", "add", "-u", "alice", "-k", "secret")
        code, data = self.run_cli("run", "push", "00_data_prep.ipynb", "--json",
                                  "--notebooks-dir", str(FIXTURE_NOTEBOOKS))
        self.assertEqual(code, 1)
        self.assertFalse(data["success"])
        self.assertIn("GPU session count", data["error"])

    def test_doctor_json(self):
        with patch.dict(os.environ, {"KAGGLE_FLEET_KAGGLE_BIN": "definitely-not-a-real-binary"}):
            code, data = self.run_cli("doctor", "--json")
        self.assertEqual(code, 1)
        self.assertFalse(data["healthy"])
        names = {c["check"]: c for c in data["checks"]}
        self.assertFalse(names["kaggle_cli"]["ok"])
        self.assertFalse(names["accounts"]["ok"])


if __name__ == "__main__":
    unittest.main()
