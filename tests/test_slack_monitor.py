#!/usr/bin/env python3

import importlib.machinery
import importlib.util
from pathlib import Path
import unittest

MODULE_PATH = Path(__file__).resolve().parents[1] / "scripts/local/vista-slack-monitor"
LOADER = importlib.machinery.SourceFileLoader("vista_slack_monitor", str(MODULE_PATH))
SPEC = importlib.util.spec_from_loader(LOADER.name, LOADER)
assert SPEC
monitor = importlib.util.module_from_spec(SPEC)
LOADER.exec_module(monitor)


class MonitorTests(unittest.TestCase):
    def test_parse_squeue_redacts_reason(self):
        jobs = monitor.parse_squeue(
            "123|PENDING|0:00|02:00:00|1|2026-09-19T00:00:00|"
            "2026-09-19T01:00:00|Priority\n"
        )
        self.assertEqual(jobs["123"]["state"], "PENDING")
        self.assertEqual(jobs["123"]["reason"], "Priority / 等待调度优先级")

    def test_first_snapshot_is_silent_baseline(self):
        state = monitor.initial_state()
        jobs = {
            "123": {
                "state": "PENDING",
                "elapsed": "0:00",
                "limit": "02:00:00",
                "nodes": "1",
                "start": "N/A",
                "reason": "等待调度优先级",
            }
        }
        self.assertEqual(monitor.process_snapshot(state, jobs, now=1), [])
        self.assertEqual(state["jobs"]["123"]["state"], "PENDING")

    def test_state_transition_notifies_without_real_job_id(self):
        state = monitor.initial_state()
        pending = {
            "123": {
                "state": "PENDING",
                "elapsed": "0:00",
                "limit": "02:00:00",
                "nodes": "1",
                "start": "N/A",
                "reason": "等待调度优先级",
            }
        }
        monitor.process_snapshot(state, pending, now=1)
        running = {
            "123": {
                **pending["123"],
                "state": "RUNNING",
                "elapsed": "0:01",
                "reason": "none",
            }
        }
        messages = monitor.process_snapshot(state, running, now=2)
        self.assertEqual(len(messages), 1)
        self.assertIn("A01", messages[0])
        self.assertIn("RUNNING", messages[0])
        self.assertNotIn("123", messages[0])

    def test_terminal_transition_notifies(self):
        state = monitor.initial_state()
        running = {
            "456": {
                "state": "RUNNING",
                "elapsed": "0:05",
                "limit": "01:00:00",
                "nodes": "1",
                "start": "2026-09-19T01:00:00",
                "reason": "none",
            }
        }
        monitor.process_snapshot(state, running, now=1)
        completed = {
            "456": {
                **running["456"],
                "state": "COMPLETED",
                "elapsed": "0:10",
            }
        }
        messages = monitor.process_snapshot(state, {}, completed, now=2)
        self.assertEqual(len(messages), 1)
        self.assertIn("COMPLETED", messages[0])


if __name__ == "__main__":
    unittest.main()
