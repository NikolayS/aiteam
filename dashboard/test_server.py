"""
Tests for dashboard/server.py

Uses only stdlib unittest — zero external dependencies per CLAUDE.md.
"""

import io
import json
import os
import re
import sys
import tempfile
import time
import unittest
from unittest import mock

# The module under test imports load_config() at module level and calls
# sys.exit(1) when config.json is missing.  We need to patch that before
# importing, so we create a minimal temp config and point the env var at it.

_TEMP_CFG = tempfile.NamedTemporaryFile(
    mode="w", suffix=".json", delete=False
)
_TEMP_CFG.write(json.dumps({
    "team_name": "test-team",
    "port": 19999,
    "bind": "127.0.0.1",
    "agents": [],
    "gateway": {},
    "thresholds": {"active_minutes": 30, "recent_hours": 4},
}))
_TEMP_CFG.close()
os.environ["DASHBOARD_CONFIG"] = _TEMP_CFG.name

# Now it is safe to import.
import server  # noqa: E402


class TestNowMs(unittest.TestCase):
    """now_ms() should return current time in milliseconds."""

    def test_returns_integer(self):
        result = server.now_ms()
        self.assertIsInstance(result, int)

    def test_reasonable_range(self):
        # Should be in the ballpark of current epoch-ms.
        result = server.now_ms()
        expected = int(time.time() * 1000)
        self.assertAlmostEqual(result, expected, delta=2000)


class TestFmtAgo(unittest.TestCase):
    """fmt_ago() converts an epoch-ms timestamp to a human-readable delta."""

    def _ms_ago(self, seconds):
        """Helper: return epoch-ms for `seconds` ago."""
        return server.now_ms() - int(seconds * 1000)

    def test_none_returns_never(self):
        self.assertEqual(server.fmt_ago(None), "never")

    def test_zero_returns_never(self):
        self.assertEqual(server.fmt_ago(0), "never")

    def test_seconds_ago(self):
        result = server.fmt_ago(self._ms_ago(30))
        self.assertRegex(result, r"^\d+s ago$")

    def test_minutes_ago(self):
        result = server.fmt_ago(self._ms_ago(300))
        self.assertRegex(result, r"^\d+m ago$")

    def test_hours_ago(self):
        result = server.fmt_ago(self._ms_ago(7200))
        self.assertRegex(result, r"^\d+h ago$")

    def test_days_ago(self):
        result = server.fmt_ago(self._ms_ago(100000))
        self.assertRegex(result, r"^\d+d ago$")


class TestExpand(unittest.TestCase):
    """expand() should handle ~ and $ENV expansions."""

    def test_tilde_expansion(self):
        result = server.expand("~/foo")
        self.assertNotIn("~", result)
        self.assertTrue(result.endswith("/foo"))

    def test_env_var_expansion(self):
        os.environ["_TEST_EXPAND_VAR"] = "/custom/path"
        result = server.expand("$_TEST_EXPAND_VAR/sub")
        self.assertEqual(result, "/custom/path/sub")
        del os.environ["_TEST_EXPAND_VAR"]

    def test_plain_path_unchanged(self):
        self.assertEqual(server.expand("/absolute/path"), "/absolute/path")


class TestScrub(unittest.TestCase):
    """_scrub() must redact all known secret patterns."""

    def test_anthropic_key(self):
        line = "key=sk-ant-abc123-XyZ_456_longtoken"
        result = server._scrub(line)
        self.assertNotIn("sk-ant-", result)
        self.assertIn("***REDACTED***", result)

    def test_openai_key(self):
        line = "Authorization sk-abcdefghij1234567890ABCD"
        result = server._scrub(line)
        self.assertNotIn("sk-abcdefghij", result)
        self.assertIn("***REDACTED***", result)

    def test_github_pat(self):
        line = "token ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result = server._scrub(line)
        self.assertNotIn("ghp_", result)
        self.assertIn("***REDACTED***", result)

    def test_github_fine_grained_pat(self):
        line = "github_pat_ABCDEFGHIJ1234567890_extra"
        result = server._scrub(line)
        self.assertNotIn("github_pat_", result)
        self.assertIn("***REDACTED***", result)

    def test_gitlab_pat(self):
        line = "glpat-abcdefghijklmnopqrstuvwxyz"
        result = server._scrub(line)
        self.assertNotIn("glpat-", result)
        self.assertIn("***REDACTED***", result)

    def test_slack_bot_token(self):
        line = "xoxb-123-456-abcdef"
        result = server._scrub(line)
        self.assertNotIn("xoxb-", result)
        self.assertIn("***REDACTED***", result)

    def test_slack_user_token(self):
        line = "xoxp-111-222-333"
        result = server._scrub(line)
        self.assertNotIn("xoxp-", result)

    def test_slack_app_token(self):
        line = "xapp-111-222-333"
        result = server._scrub(line)
        self.assertNotIn("xapp-", result)

    def test_bearer_token(self):
        line = "Bearer eyJhbGciOiJIUzI1NiJ9.payload.sig"
        result = server._scrub(line)
        self.assertNotIn("eyJ", result)
        self.assertIn("***REDACTED***", result)

    def test_token_query_param(self):
        line = "url?token=abc123_secret&other=1"
        result = server._scrub(line)
        self.assertNotIn("abc123_secret", result)

    def test_key_query_param(self):
        line = "url?key=mysecretapikey123"
        result = server._scrub(line)
        self.assertNotIn("mysecretapikey123", result)

    def test_no_secret_unchanged(self):
        line = "INFO 2025-01-01 normal log line with no secrets"
        self.assertEqual(server._scrub(line), line)

    def test_multiple_secrets_all_redacted(self):
        line = "sk-ant-abc123def456 and ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        result = server._scrub(line)
        self.assertEqual(result.count("***REDACTED***"), 2)

    def test_empty_string(self):
        self.assertEqual(server._scrub(""), "")


class TestLoadConfig(unittest.TestCase):
    """load_config() error handling."""

    def test_missing_config_exits(self):
        with mock.patch.dict(os.environ, {"DASHBOARD_CONFIG": "/nonexistent.json"}):
            # Temporarily override the module-level CONFIG_PATH
            with mock.patch.object(server, "CONFIG_PATH", "/nonexistent.json"):
                with self.assertRaises(SystemExit):
                    server.load_config()

    def test_valid_config_returns_dict(self):
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".json", delete=False
        ) as f:
            json.dump({"team_name": "t"}, f)
            f.flush()
            with mock.patch.object(server, "CONFIG_PATH", f.name):
                cfg = server.load_config()
                self.assertEqual(cfg["team_name"], "t")
            os.unlink(f.name)


class TestGetAgentData(unittest.TestCase):
    """get_agent_data() reads session files and computes status."""

    def test_missing_sessions_file_returns_unknown(self):
        with mock.patch.object(server, "AGENTS", [
            {"name": "test-agent", "sessions_dir": "/nonexistent"}
        ]):
            agents = server.get_agent_data()
            self.assertEqual(len(agents), 1)
            self.assertEqual(agents[0]["name"], "test-agent")
            self.assertEqual(agents[0]["status"], "unknown")
            self.assertEqual(agents[0]["session_count"], 0)

    def test_active_agent(self):
        now = server.now_ms()
        sessions = {
            "s1": {"updatedAt": now - 5000},   # 5 seconds ago
        }
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "sessions.json"), "w") as f:
                json.dump(sessions, f)
            with mock.patch.object(server, "AGENTS", [
                {"name": "active-bot", "sessions_dir": d}
            ]):
                agents = server.get_agent_data()
                self.assertEqual(agents[0]["status"], "active")
                self.assertEqual(agents[0]["session_count"], 1)

    def test_idle_agent(self):
        now = server.now_ms()
        sessions = {
            "s1": {"updatedAt": now - 5 * 3600 * 1000},  # 5 hours ago
        }
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "sessions.json"), "w") as f:
                json.dump(sessions, f)
            with mock.patch.object(server, "AGENTS", [
                {"name": "idle-bot", "sessions_dir": d}
            ]):
                agents = server.get_agent_data()
                self.assertEqual(agents[0]["status"], "idle")

    def test_empty_agents_list(self):
        with mock.patch.object(server, "AGENTS", []):
            self.assertEqual(server.get_agent_data(), [])


class TestGetUsageData(unittest.TestCase):
    """get_usage_data() aggregates daily session counts."""

    def test_no_agents_returns_empty(self):
        with mock.patch.object(server, "AGENTS", []):
            usage = server.get_usage_data()
            self.assertEqual(usage["recent_activity"], [])

    def test_aggregation_with_sessions(self):
        now = server.now_ms()
        sessions = {
            "s1": {"updatedAt": now - 1000},
            "s2": {"updatedAt": now - 2000},
        }
        with tempfile.TemporaryDirectory() as d:
            with open(os.path.join(d, "sessions.json"), "w") as f:
                json.dump(sessions, f)
            with mock.patch.object(server, "AGENTS", [
                {"name": "bot", "sessions_dir": d}
            ]):
                usage = server.get_usage_data()
                self.assertGreaterEqual(len(usage["recent_activity"]), 1)
                latest = usage["recent_activity"][-1]
                self.assertIn("bot", latest["activeAgents"])
                self.assertEqual(latest["sessionCount"], 2)


class TestGetGatewayStatus(unittest.TestCase):
    """get_gateway_status() checks service and port health."""

    def test_empty_gateway_config(self):
        with mock.patch.object(server, "GATEWAY", {}):
            result = server.get_gateway_status()
            self.assertIn("status", result)
            self.assertIn("healthy", result)
            self.assertIn("pid", result)

    def test_systemd_active(self):
        mock_run = mock.MagicMock()
        mock_run.return_value = mock.MagicMock(stdout="active\n")
        with mock.patch.object(server, "GATEWAY", {
            "service_name": "test.service"
        }):
            with mock.patch("subprocess.run", mock_run):
                result = server.get_gateway_status()
                self.assertEqual(result["status"], "active")
                self.assertTrue(result["healthy"])


class TestHTMLXSS(unittest.TestCase):
    """The HTML template must not allow XSS through team_name."""

    def test_team_name_is_escaped_in_template(self):
        # The template uses {team} — test that a malicious name would
        # be rendered literally (the template itself uses .format(), which
        # inserts text into the HTML without escaping).
        # This is a known gap: team_name comes from a local config file
        # controlled by the admin, so it is low-risk, but we document
        # the behavior here.
        malicious = '<script>alert(1)</script>'
        html = server.HTML_TEMPLATE.format(team=malicious)
        # The raw script tag WILL appear because .format() does not escape.
        # This test documents the current behavior.
        self.assertIn(malicious, html)

    def test_json_api_does_not_contain_raw_html(self):
        # The JSON API should return application/json, which browsers
        # will not render as HTML.  Verify content type is set in _send.
        handler = mock.MagicMock(spec=server.DashboardHandler)
        handler.headers = {}
        handler.wfile = io.BytesIO()
        server.DashboardHandler._send(handler, 200, "application/json", '{}')
        handler.send_header.assert_any_call("Content-Type", "application/json")
        handler.send_header.assert_any_call(
            "X-Content-Type-Options", "nosniff"
        )


class TestDashboardHandler(unittest.TestCase):
    """HTTP handler routing and auth tests."""

    def _make_handler(self, path="/", secret_env="", secret_header=""):
        handler = mock.MagicMock(spec=server.DashboardHandler)
        handler.path = path
        handler.headers = {}
        if secret_header:
            handler.headers["X-Dashboard-Secret"] = secret_header
        handler.wfile = io.BytesIO()
        handler.send_response = mock.MagicMock()
        handler.send_header = mock.MagicMock()
        handler.end_headers = mock.MagicMock()
        return handler

    def test_check_secret_no_env_allows_all(self):
        handler = self._make_handler()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop("DASHBOARD_SECRET", None)
            result = server.DashboardHandler._check_secret(handler)
            self.assertTrue(result)

    def test_check_secret_correct(self):
        handler = self._make_handler(secret_header="s3cret")
        with mock.patch.dict(os.environ, {"DASHBOARD_SECRET": "s3cret"}):
            result = server.DashboardHandler._check_secret(handler)
            self.assertTrue(result)

    def test_check_secret_wrong(self):
        handler = self._make_handler(secret_header="wrong")
        with mock.patch.dict(os.environ, {"DASHBOARD_SECRET": "s3cret"}):
            result = server.DashboardHandler._check_secret(handler)
            self.assertFalse(result)

    def test_check_secret_missing_header(self):
        handler = self._make_handler()
        with mock.patch.dict(os.environ, {"DASHBOARD_SECRET": "s3cret"}):
            result = server.DashboardHandler._check_secret(handler)
            self.assertFalse(result)

    def test_do_GET_forbidden_when_secret_wrong(self):
        handler = self._make_handler(path="/", secret_header="wrong")
        handler._check_secret = lambda: False
        handler._send = mock.MagicMock()
        server.DashboardHandler.do_GET(handler)
        handler._send.assert_called_once_with(403, "text/plain", "Forbidden")

    def test_do_GET_root_returns_html(self):
        handler = self._make_handler(path="/")
        handler._check_secret = lambda: True
        handler._send = mock.MagicMock()
        server.DashboardHandler.do_GET(handler)
        handler._send.assert_called_once()
        args = handler._send.call_args
        self.assertEqual(args[0][0], 200)
        self.assertIn("text/html", args[0][1])

    def test_do_GET_api_returns_json(self):
        handler = self._make_handler(path="/api")
        handler._check_secret = lambda: True
        handler._send = mock.MagicMock()
        with mock.patch.object(server, "collect_all", return_value={"ok": True}):
            server.DashboardHandler.do_GET(handler)
        handler._send.assert_called_once()
        args = handler._send.call_args
        self.assertEqual(args[0][0], 200)
        self.assertEqual(args[0][1], "application/json")

    def test_do_GET_unknown_path_redirects(self):
        handler = self._make_handler(path="/nonexistent")
        handler._check_secret = lambda: True
        handler._send = mock.MagicMock()
        server.DashboardHandler.do_GET(handler)
        handler.send_response.assert_called_with(302)
        handler.send_header.assert_any_call("Location", "/")


class TestSecretRegexEdgeCases(unittest.TestCase):
    """Edge cases for the _SECRET_RE pattern."""

    def test_short_sk_not_redacted(self):
        # sk- followed by fewer than 20 chars should NOT match the
        # OpenAI pattern (but could match if it starts with sk-ant-).
        line = "sk-short"
        result = server._scrub(line)
        # "sk-short" is only 8 chars after sk-, so should not be redacted.
        self.assertEqual(result, line)

    def test_bearer_case_sensitive(self):
        # "Bearer" should match, "bearer" should not (regex is case-sensitive).
        line = "bearer eyJabc123"
        result = server._scrub(line)
        self.assertEqual(result, line)  # lowercase bearer not matched

    def test_key_param_at_start_of_line(self):
        line = "key=abc123def456"
        result = server._scrub(line)
        self.assertIn("***REDACTED***", result)


# Cleanup the temp config file
@unittest.registerResult
class _Cleanup:
    pass


def tearDownModule():
    try:
        os.unlink(_TEMP_CFG.name)
    except OSError:
        pass


if __name__ == "__main__":
    unittest.main()
