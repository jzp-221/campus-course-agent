"""Readiness never treats a wrong service as healthy or prints private errors."""

import asyncio
import importlib.util
import io
from pathlib import Path
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import patch


_path = Path(__file__).resolve().parents[2] / "deploy/demo/health.py"
_spec = importlib.util.spec_from_file_location("isolated_demo_health", _path)
health = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(health)


class DemoReadinessTests(unittest.TestCase):
    def test_api_requires_explicit_ok_status(self):
        with patch.object(health, "urlopen", return_value=io.BytesIO(b'{"status":"ok"}')) as request:
            health.check_api()
        request.assert_called_once_with("http://127.0.0.1:8000/api/demo/health", timeout=5)

    def test_api_wrong_payload_is_not_ready(self):
        for payload in (b'{"message":"other service"}', b'{"status":"failed"}', b'not-json'):
            with self.subTest(payload=payload):
                with patch.object(health, "urlopen", return_value=io.BytesIO(payload)):
                    with self.assertRaises((RuntimeError, ValueError)):
                        health.check_api()

    def test_health_failure_does_not_print_private_exception(self):
        stderr = io.StringIO()
        with patch.object(sys, "argv", ["health", "api"]), patch.object(health, "check_api", side_effect=RuntimeError("password=never-print-this")), patch.object(sys, "stderr", stderr):
            self.assertEqual(health.main(), 1)
        self.assertEqual(stderr.getvalue(), "readiness failed\n")

    def test_unknown_target_is_rejected(self):
        with patch.object(sys, "argv", ["health", "db"]), patch.object(sys, "stderr", io.StringIO()):
            self.assertEqual(health.main(), 1)

    def test_mcp_handshake_checks_tools_and_closes_client(self):
        events = []

        class FakeClient:
            def __init__(self, url):
                events.append(("url", url))

            async def __aenter__(self):
                events.append("open")
                return self

            async def __aexit__(self, *args):
                events.append("close")

            async def list_tools(self):
                events.append("list")
                return [SimpleNamespace(name=name) for name in ("get_my_schedules", "save_study_plan", "search_course_docs")]

        with patch.dict(sys.modules, {"fastmcp": SimpleNamespace(Client=FakeClient)}):
            asyncio.run(health.check_mcp())
        self.assertEqual(events, [("url", "http://127.0.0.1:8001/mcp"), "open", "list", "close"])

    def test_mcp_missing_tool_rejects_after_client_cleanup(self):
        events = []

        class IncompleteClient:
            def __init__(self, url):
                pass

            async def __aenter__(self):
                return self

            async def __aexit__(self, *args):
                events.append("closed")

            async def list_tools(self):
                return [SimpleNamespace(name="get_my_schedules")]

        with patch.dict(sys.modules, {"fastmcp": SimpleNamespace(Client=IncompleteClient)}):
            with self.assertRaisesRegex(RuntimeError, "MCP tool readiness failed"):
                asyncio.run(health.check_mcp())
        self.assertEqual(events, ["closed"])


if __name__ == "__main__":
    unittest.main()
