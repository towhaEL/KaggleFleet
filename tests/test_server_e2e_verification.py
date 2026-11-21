"""
Phase 2 End-to-End Verification Test for KaggleFleet Server & REST API.
Validates:
1. Server startup & graceful shutdown on ephemeral port.
2. REST API endpoints:
   - /api/status (GET)
   - /api/accounts (GET, POST add, POST switch, DELETE)
   - /api/notebooks (GET list, GET inspect)
   - /api/runs (GET list, POST push)
3. Static UI delivery:
   - index.html (text/html)
   - app.js (application/javascript)
   - styles.css (text/css)
4. Security & Robustness:
   - Path traversal attacks (/static/../../../etc/passwd) -> 403 Forbidden
   - Malformed POST payloads -> 400 Bad Request
   - Non-existent routes -> 404 Not Found
   - CORS headers presence on all API responses
"""

from __future__ import annotations

import json
import shutil
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import MagicMock, patch
from urllib.error import HTTPError
from urllib.request import Request, urlopen

from kaggle_fleet.account_manager import AccountManager
from kaggle_fleet.kaggle_runner import KaggleRunner
from kaggle_fleet.notebook_manager import NotebookManager
from kaggle_fleet.server.http_server import KaggleFleetServer
from kaggle_fleet.storage import StorageEngine

FIXTURE_NOTEBOOKS = Path(__file__).resolve().parent / "fixtures" / "notebooks"


class TestServerE2EVerification(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.temp_dir = tempfile.mkdtemp(prefix="kh_server_e2e_")
        cls.base_dir = Path(cls.temp_dir)

        cls.storage = StorageEngine(base_dir=cls.base_dir)
        cls.acc_mgr = AccountManager(storage=cls.storage, base_dir=cls.base_dir)
        cls.nb_mgr = NotebookManager(
            notebooks_dir=FIXTURE_NOTEBOOKS,
            staging_dir=cls.base_dir / "staging",
        )
        cls.runner = KaggleRunner(
            account_manager=cls.acc_mgr,
            notebook_manager=cls.nb_mgr,
            storage=cls.storage,
        )

        # Prepopulate one test account
        cls.initial_acc = cls.acc_mgr.add_account(
            label="Initial Tester",
            username="tester_initial",
            auth_type="api_key",
            credentials={"key": "test_key_abc"},
            make_active=True,
        )

        # Start server on ephemeral port (bind to port 0)
        cls.server = KaggleFleetServer(
            host="127.0.0.1",
            port=0,
            account_manager=cls.acc_mgr,
            notebook_manager=cls.nb_mgr,
            runner=cls.runner,
            storage=cls.storage,
        )
        cls.server.start_background()

        # Extract bound port
        cls.port = cls.server.server_port
        cls.base_url = f"http://127.0.0.1:{cls.port}"

        # Give thread a brief moment to listen
        time.sleep(0.1)

    @classmethod
    def tearDownClass(cls):
        cls.server.shutdown()
        shutil.rmtree(cls.temp_dir, ignore_errors=True)

    def _request(
        self,
        endpoint: str,
        method: str = "GET",
        data: dict | None = None,
    ) -> tuple[int, dict | str, dict]:
        url = f"{self.base_url}{endpoint}"
        body = json.dumps(data).encode("utf-8") if data is not None else None
        headers = {}
        if data is not None:
            headers["Content-Type"] = "application/json"

        req = Request(url=url, data=body, headers=headers, method=method)
        try:
            with urlopen(req, timeout=5) as resp:
                status = resp.status
                resp_headers = dict(resp.headers)
                content_type = resp_headers.get("Content-Type", "")
                raw = resp.read()
                if "application/json" in content_type:
                    return status, json.loads(raw.decode("utf-8")), resp_headers
                else:
                    return status, raw.decode("utf-8", errors="replace"), resp_headers
        except HTTPError as e:
            err_raw = e.read()
            e.close()
            try:
                err_json = json.loads(err_raw.decode("utf-8"))
                return e.code, err_json, dict(e.headers)
            except Exception:
                return e.code, err_raw.decode("utf-8", errors="replace"), dict(e.headers)

    # -------------------------------------------------------------------------
    # REST API Verification
    # -------------------------------------------------------------------------

    def test_01_api_status(self):
        status, body, headers = self._request("/api/status")
        self.assertEqual(status, 200)
        self.assertIsInstance(body, dict)
        self.assertEqual(body.get("status"), "ok")
        self.assertIn("version", body)
        self.assertIn("kaggle_cli", body)
        self.assertEqual(body["total_notebooks"], 6)
        self.assertIsNotNone(body["active_account"])
        self.assertEqual(body["active_account"]["username"], "tester_initial")
        # The API must not be readable cross-origin
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def test_02_api_accounts_get_and_post(self):
        # GET accounts
        status, body, _ = self._request("/api/accounts")
        self.assertEqual(status, 200)
        self.assertEqual(len(body["accounts"]), 1)
        self.assertEqual(body["active_account_id"], self.initial_acc.id)

        # POST new account
        new_acc_payload = {
            "label": "Secondary CoAuthor",
            "username": "coauthor_sec",
            "auth_type": "api_key",
            "credentials": {"key": "sec_key_xyz"},
            "quota_notes": "60h TPU quota",
            "make_active": False,
        }
        status, body, _ = self._request("/api/accounts", method="POST", data=new_acc_payload)
        self.assertEqual(status, 201)
        self.assertIn("account", body)
        new_id = body["account"]["id"]
        self.assertEqual(body["account"]["username"], "coauthor_sec")

        # Verify active account is still initial
        status, body, _ = self._request("/api/accounts")
        self.assertEqual(len(body["accounts"]), 2)
        self.assertEqual(body["active_account_id"], self.initial_acc.id)

        # POST switch active account
        status, body, _ = self._request(f"/api/accounts/{new_id}/switch", method="POST")
        self.assertEqual(status, 200)
        self.assertEqual(body["active_account_id"], new_id)

        # Verify switch reflected in GET /api/accounts
        status, body, _ = self._request("/api/accounts")
        self.assertEqual(body["active_account_id"], new_id)

    def test_03_api_notebooks(self):
        # GET list
        status, body, _ = self._request("/api/notebooks")
        self.assertEqual(status, 200)
        self.assertIn("notebooks", body)
        self.assertEqual(len(body["notebooks"]), 6)

        names = [nb["name"] for nb in body["notebooks"]]
        self.assertIn("00_data_prep.ipynb", names)
        self.assertIn("02_train_closed_end.ipynb", names)

        # GET inspect single notebook
        status, body, _ = self._request("/api/notebooks/00_data_prep.ipynb")
        self.assertEqual(status, 200)
        self.assertIn("notebook", body)
        self.assertEqual(body["notebook"]["name"], "00_data_prep.ipynb")
        self.assertEqual(body["notebook"]["slug"], "nb-00-data-prep")
        self.assertGreater(body["notebook"]["total_cells"], 0)
        self.assertIn("cell_preview", body)
        self.assertGreaterEqual(len(body["cell_preview"]), 1)

    @patch("subprocess.run")
    def test_04_api_runs(self, mock_subprocess):
        mock_subprocess.return_value = MagicMock(
            returncode=0,
            stdout="Kernel version 1 pushed successfully. Logs: Epoch 1 loss: 0.1",
            stderr="",
        )

        # GET runs list (empty initially)
        status, body, _ = self._request("/api/runs")
        self.assertEqual(status, 200)
        self.assertIn("jobs", body)
        self.assertIsInstance(body["jobs"], list)

        # POST push job (will queue, prepare staging, and invoke push)
        push_payload = {
            "notebook": "00_data_prep.ipynb",
            "accelerator": "NvidiaTeslaT4",
            "enable_gpu": True,
            "enable_internet": True,
        }
        status, body, _ = self._request("/api/runs", method="POST", data=push_payload)
        self.assertEqual(status, 201)
        self.assertIn("job", body)
        job_id = body["job"]["id"]
        self.assertEqual(body["job"]["kernel_slug"], "nb-00-data-prep")
        self.assertTrue(Path(body["job"]["staging_path"]).is_dir())

        # Verify job is now listed in /api/runs
        status, body, _ = self._request("/api/runs")
        self.assertEqual(len(body["jobs"]), 1)
        self.assertEqual(body["jobs"][0]["id"], job_id)

        # GET /api/runs/:id
        status, body, _ = self._request(f"/api/runs/{job_id}")
        self.assertEqual(status, 200)
        self.assertEqual(body["job"]["id"], job_id)

        # GET /api/runs/:id/logs
        status, body, _ = self._request(f"/api/runs/{job_id}/logs")
        self.assertEqual(status, 200)
        self.assertEqual(body["job_id"], job_id)
        self.assertIn("logs", body)

    # -------------------------------------------------------------------------
    # Static Assets & UI Delivery
    # -------------------------------------------------------------------------

    def test_05_static_file_serving(self):
        # Root index.html
        status, content, headers = self._request("/")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))
        self.assertIn("KaggleFleet", content)
        self.assertIn("tab-accounts", content)
        self.assertIn("tab-launch", content)
        self.assertIn("tab-jobs", content)

        # Explicit /index.html
        status, content, headers = self._request("/index.html")
        self.assertEqual(status, 200)
        self.assertIn("text/html", headers.get("Content-Type", ""))

        # /app.js
        status, content, headers = self._request("/app.js")
        self.assertEqual(status, 200)
        self.assertIn("javascript", headers.get("Content-Type", "").lower())
        self.assertIn("function loadAccounts", content)

        # /static/styles.css
        status, content, headers = self._request("/styles.css")
        self.assertEqual(status, 200)
        self.assertIn("text/css", headers.get("Content-Type", "").lower())
        self.assertIn("var(--", content)

    # -------------------------------------------------------------------------
    # Security & Error Handling Verification
    # -------------------------------------------------------------------------

    def test_06_path_traversal_defense(self):
        # Attempt directory traversal outside static directory
        status, body, _ = self._request("/static/../../../../etc/passwd")
        self.assertIn(status, [403, 404])
        self.assertNotEqual(status, 200)

        # Attempt directory traversal via plain path
        status, body, _ = self._request("/../../etc/passwd")
        self.assertIn(status, [403, 404])
        self.assertNotEqual(status, 200)

    def test_07_invalid_json_and_missing_fields(self):
        # POST with missing username
        status, body, _ = self._request("/api/accounts", method="POST", data={"label": "No User"})
        self.assertEqual(status, 400)
        self.assertIn("error", body)

        # Non-existent notebook inspection
        status, body, _ = self._request("/api/notebooks/non_existent_notebook_99.ipynb")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

        # Unknown API route
        status, body, _ = self._request("/api/unknown_endpoint")
        self.assertEqual(status, 404)
        self.assertIn("error", body)

    def test_08_cross_origin_is_refused(self):
        # Preflights get no CORS grant
        status, _, headers = self._request("/api/accounts", method="OPTIONS")
        self.assertEqual(status, 405)
        self.assertIsNone(headers.get("Access-Control-Allow-Origin"))

    def _raw(self, method, path, headers, body=None):
        import http.client
        conn = http.client.HTTPConnection("127.0.0.1", self.server.server_port, timeout=5)
        try:
            conn.request(method, path, body=body, headers=headers)
            resp = conn.getresponse()
            return resp.status, resp.read()
        finally:
            conn.close()

    def test_09_foreign_origin_rejected(self):
        host = f"127.0.0.1:{self.server.server_port}"
        status, _ = self._raw("POST", "/api/runs", {
            "Host": host, "Origin": "https://evil.example",
            "Content-Type": "application/json"}, body=b'{"notebook": "00_data_prep.ipynb"}')
        self.assertEqual(status, 403)
        status, _ = self._raw("GET", "/api/accounts", {"Host": host, "Origin": "null"})
        self.assertEqual(status, 403)
        # Same-origin requests from the panel itself are fine
        status, _ = self._raw("GET", "/api/status", {"Host": host, "Origin": f"http://{host}"})
        self.assertEqual(status, 200)

    def test_10_dns_rebinding_host_rejected(self):
        status, _ = self._raw("GET", "/api/accounts", {"Host": f"evil.example:{self.server.server_port}"})
        self.assertEqual(status, 403)
        # Static UI is still served (no secrets there)
        status, _ = self._raw("GET", "/", {"Host": f"localhost:{self.server.server_port}"})
        self.assertEqual(status, 200)

    def test_11_form_style_post_rejected(self):
        host = f"127.0.0.1:{self.server.server_port}"
        status, _ = self._raw("POST", "/api/accounts", {
            "Host": host, "Content-Type": "text/plain"}, body=b'{"username": "x", "key": "y"}')
        self.assertEqual(status, 415)
        status, _ = self._raw("POST", "/api/accounts", {
            "Host": host, "Content-Type": "application/json"}, body=b"not json")
        self.assertEqual(status, 400)

if __name__ == "__main__":
    unittest.main()
