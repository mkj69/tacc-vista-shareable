#!/usr/bin/env python3
"""Safety tests for the dashboard's guarded job-cancellation path."""

from __future__ import annotations

import importlib.util
import io
import json
import tempfile
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


class SubmissionHelperTests(unittest.TestCase):
    def setUp(self) -> None:
        DASHBOARD._submission_results.clear()

    def submission(self, path: str) -> dict[str, object]:
        return {
            "request_id": "request_0123456789abcdef",
            "script_path": path,
            "partition": "gh",
            "time_limit": "01:30:00",
            "nodes": "2",
            "ntasks": "2",
            "cpus_per_task": "8",
            "job_name": "visual-submit-test",
            "account": "test-account",
            "qos": "test-qos",
        }

    def test_structured_submission_builds_argument_list(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sbatch") as script:
            script.write("#!/bin/bash\ntrue\n")
            script.flush()
            payload = self.submission(script.name)
            output = "validation banner\n12345"
            with mock.patch.object(DASHBOARD, "run_cmd", return_value=output) as run_cmd:
                result = DASHBOARD.request_job_submit("test-user", payload)

            run_cmd.assert_called_once_with(
                [
                    "sbatch", "--parsable", "--partition=gh", "--time=01:30:00", "--nodes=2",
                    "--ntasks=2", "--cpus-per-task=8", "--job-name=visual-submit-test",
                    "--account=test-account", "--qos=test-qos", str(Path(script.name).resolve()),
                ],
                timeout=60,
            )
        self.assertEqual(result["job_id"], "12345")
        self.assertFalse(result["duplicate"])

    def test_same_request_id_is_idempotent(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sbatch") as script:
            payload = self.submission(script.name)
            with mock.patch.object(DASHBOARD, "run_cmd", return_value="Submitted batch job 12345") as run_cmd:
                first = DASHBOARD.request_job_submit("test-user", payload)
                second = DASHBOARD.request_job_submit("test-user", payload)
        run_cmd.assert_called_once()
        self.assertFalse(first["duplicate"])
        self.assertTrue(second["duplicate"])
        self.assertEqual(second["job_id"], "12345")

    def test_invalid_submission_never_reaches_sbatch(self) -> None:
        payload = self.submission("relative/job.sbatch")
        with mock.patch.object(DASHBOARD, "run_cmd") as run_cmd:
            with self.assertRaises(ValueError):
                DASHBOARD.request_job_submit("test-user", payload)
        run_cmd.assert_not_called()

    def test_partition_walltime_limit_is_enforced(self) -> None:
        with tempfile.NamedTemporaryFile(mode="w", suffix=".sbatch") as script:
            payload = self.submission(script.name)
            payload["partition"] = "gh-dev"
            payload["time_limit"] = "02:00:01"
            with mock.patch.object(DASHBOARD, "run_cmd") as run_cmd:
                with self.assertRaises(ValueError):
                    DASHBOARD.request_job_submit("test-user", payload)
        run_cmd.assert_not_called()

    def test_html_contains_two_step_submission_form(self) -> None:
        html = DASHBOARD.dashboard_html()
        self.assertIn('id="submit-form"', html)
        self.assertIn('id="submit-review"', html)
        self.assertIn("fetch('/api/submit'", html)


class LiveSamplerAccountTests(unittest.TestCase):
    def test_gpu_sampler_charges_parent_job_account(self) -> None:
        gpu_output = (
            "node-a|0, 50, 20, 1024, 97871, 40, 200.0, 1800, 2600, 5, 16, "
            "NVIDIA GH200 120GB"
        )
        process_output = "node-a|123, python, 1024"
        with mock.patch.object(DASHBOARD.shutil, "which", return_value="/usr/bin/srun"), mock.patch.object(
            DASHBOARD, "run_cmd", side_effect=[gpu_output, process_output]
        ) as run_cmd:
            data, warning = DASHBOARD.query_job_gpu(
                "12345", "gh", "project-test", 1, "node-a"
            )

        self.assertIsNone(warning)
        self.assertEqual(data["metrics"][0]["node"], "node-a")
        self.assertEqual(len(run_cmd.call_args_list), 2)
        for call in run_cmd.call_args_list:
            self.assertIn("--account=project-test", call.args[0])

    def test_node_sampler_charges_parent_job_account(self) -> None:
        output = "node-a|1000|500|1000000|500000|1.0|0.5|0.2"
        with mock.patch.object(DASHBOARD.shutil, "which", return_value="/usr/bin/srun"), mock.patch.object(
            DASHBOARD, "run_cmd", return_value=output
        ) as run_cmd:
            data, warning = DASHBOARD.query_job_node_resources(
                "12345", "gh", "project-test", 1, "node-a"
            )

        self.assertIsNone(warning)
        self.assertEqual(data["nodes"][0]["node"], "node-a")
        self.assertIn("--account=project-test", run_cmd.call_args.args[0])


class CancellationEndpointTests(unittest.TestCase):
    def post(self, payload: dict[str, str], token: str, path: str = "/api/cancel") -> tuple[int, dict[str, str]]:
        body = json.dumps(payload).encode("utf-8")
        handler = DASHBOARD.DashboardHandler.__new__(DASHBOARD.DashboardHandler)
        handler.path = path
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

    def test_valid_submit_post_reaches_mock_only(self) -> None:
        result = {"job_id": "12345", "message": "submitted"}
        payload = {"request_id": "request_0123456789abcdef", "script_path": "/tmp/test.sbatch"}
        with mock.patch.object(DASHBOARD.getpass, "getuser", return_value="test-user"), mock.patch.object(
            DASHBOARD, "request_job_submit", return_value=result
        ) as submit:
            status, body = self.post(payload, DASHBOARD.DASHBOARD_CSRF_TOKEN, path="/api/submit")
        self.assertEqual(status, 202)
        self.assertEqual(body["job_id"], "12345")
        submit.assert_called_once_with("test-user", payload)


if __name__ == "__main__":
    unittest.main()
