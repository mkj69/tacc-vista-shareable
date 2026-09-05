#!/usr/bin/env python3
"""Safety tests for the dashboard's guarded job-cancellation path."""

from __future__ import annotations

import importlib.util
import io
import json
import unittest
from pathlib import Path
from unittest import mock


MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts" / "dashboard" / "vista_job_dashboard.py"
SPEC = importlib.util.spec_from_file_location("vista_job_dashboard", MODULE_PATH)
assert SPEC and SPEC.loader
DASHBOARD = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(DASHBOARD)


class CancellationHelperTests(unittest.TestCase):
    def test_exact_current_user_job_is_cancelled(self) -> None:
        active = [{"jobid": "12345", "state": "PENDING"}]
        with mock.patch.object(DASHBOARD, "list_jobs", return_value=active), mock.patch.object(
            DASHBOARD, "run_cmd", return_value=""
        ) as run_cmd:
            result = DASHBOARD.request_job_cancel("test-user", "12345")

        run_cmd.assert_called_once_with(["scancel", "--user", "test-user", "12345"], timeout=15)
        self.assertEqual(result["job_id"], "12345")
        self.assertEqual(result["previous_state"], "PENDING")

    def test_invalid_job_id_never_reaches_slurm(self) -> None:
        with mock.patch.object(DASHBOARD, "list_jobs") as list_jobs, mock.patch.object(
            DASHBOARD, "run_cmd"
        ) as run_cmd:
            with self.assertRaises(ValueError):
                DASHBOARD.request_job_cancel("test-user", "12345; touch /tmp/nope")
        list_jobs.assert_not_called()
        run_cmd.assert_not_called()

    def test_missing_or_foreign_job_never_reaches_slurm(self) -> None:
        with mock.patch.object(DASHBOARD, "list_jobs", return_value=[]), mock.patch.object(
            DASHBOARD, "run_cmd"
        ) as run_cmd:
            with self.assertRaises(LookupError):
                DASHBOARD.request_job_cancel("test-user", "12345")
        run_cmd.assert_not_called()

    def test_html_contains_guarded_cancel_controls(self) -> None:
        html = DASHBOARD.dashboard_html()
        self.assertIn('class="button button-danger chart-link cancel-job"', html)
        self.assertIn("'X-Vista-CSRF': csrfToken", html)
        self.assertIn(DASHBOARD.DASHBOARD_CSRF_TOKEN, html)
        self.assertNotIn("__DASHBOARD_CSRF_TOKEN__", html)


class CancellationEndpointTests(unittest.TestCase):
    def post(self, payload: dict[str, str], token: str) -> tuple[int, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        handler = DASHBOARD.DashboardHandler.__new__(DASHBOARD.DashboardHandler)
        handler.path = "/api/cancel"
        handler.headers = {
            "Content-Type": "application/json",
            "Content-Length": str(len(body)),
            "X-Vista-CSRF": token,
        }
        handler.rfile = io.BytesIO(body)
        response: dict[str, object] = {}

        def capture(result: dict[str, str], status: int = 200) -> None:
            response["status"] = status
            response["body"] = result

        handler.send_json = capture
        DASHBOARD.DashboardHandler.do_POST(handler)
        return int(response["status"]), response["body"]

    def test_valid_post_reaches_mock_only(self) -> None:
        result = {"job_id": "12345", "message": "requested"}
        with mock.patch.object(DASHBOARD.getpass, "getuser", return_value="test-user"), mock.patch.object(
            DASHBOARD, "request_job_cancel", return_value=result
        ) as cancel:
            status, body = self.post(
                {"job_id": "12345", "confirmation": "12345"},
                DASHBOARD.DASHBOARD_CSRF_TOKEN,
            )
        self.assertEqual(status, 202)
        self.assertEqual(body["job_id"], "12345")
        cancel.assert_called_once_with("test-user", "12345")

    def test_wrong_csrf_token_is_rejected(self) -> None:
        with mock.patch.object(DASHBOARD, "request_job_cancel") as cancel:
            status, _ = self.post({"job_id": "12345", "confirmation": "12345"}, "wrong")
        self.assertEqual(status, 403)
        cancel.assert_not_called()

    def test_mismatched_confirmation_is_rejected(self) -> None:
        with mock.patch.object(DASHBOARD, "request_job_cancel") as cancel:
            status, _ = self.post(
                {"job_id": "12345", "confirmation": "54321"},
                DASHBOARD.DASHBOARD_CSRF_TOKEN,
            )
        self.assertEqual(status, 400)
        cancel.assert_not_called()


if __name__ == "__main__":
    unittest.main()
