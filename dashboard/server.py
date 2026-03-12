#!/usr/bin/env python3
"""
AI Team Dashboard — lightweight status dashboard for autonomous AI agents.

Reads agent session data, gateway health, and log errors from local files
and serves a single-page dashboard with a JSON API.

Configuration: ./config.json (copy config.example.json to get started)
"""

import hmac
import json
import logging
import os
import re
import socket
import subprocess
import sys
import time
from collections import defaultdict
from datetime import datetime, timezone
from html import escape as html_escape
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse


# ---------------------------------------------------------------------------
# Logging
# ---------------------------------------------------------------------------

LOG_LEVEL = os.environ.get("DASHBOARD_LOG_LEVEL", "INFO").upper()

logging.basicConfig(
    level=getattr(logging, LOG_LEVEL, logging.INFO),
    format="%(asctime)s %(levelname)s %(name)s %(message)s",
    datefmt="%Y-%m-%dT%H:%M:%S%z",
    stream=sys.stderr,
)
logger = logging.getLogger("dashboard")

# Track startup time for /healthz uptime reporting.
_START_TIME = time.monotonic()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

CONFIG_PATH = os.environ.get(
    "DASHBOARD_CONFIG",
    os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json"),
)


def load_config():
    try:
        with open(CONFIG_PATH) as f:
            return json.load(f)
    except FileNotFoundError:
        logger.critical(
            "Config not found at %s — copy config.example.json to config.json and edit it.",
            CONFIG_PATH,
        )
        sys.exit(1)
    except json.JSONDecodeError as exc:
        logger.critical("Invalid JSON in %s: %s", CONFIG_PATH, exc)
        sys.exit(1)


CFG = load_config()

TEAM_NAME = CFG.get("team_name", "ai-team")
AGENTS = CFG.get("agents", [])
GATEWAY = CFG.get("gateway", {})
THRESHOLDS = CFG.get("thresholds", {})

ACTIVE_THRESHOLD_MS = THRESHOLDS.get("active_minutes", 30) * 60 * 1000
RECENT_THRESHOLD_MS = THRESHOLDS.get("recent_hours", 4) * 3600 * 1000


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def now_ms():
    return int(time.time() * 1000)


def fmt_ago(ms):
    if not ms:
        return "never"
    diff = (now_ms() - ms) / 1000
    if diff < 60:
        return f"{int(diff)}s ago"
    elif diff < 3600:
        return f"{int(diff / 60)}m ago"
    elif diff < 86400:
        return f"{int(diff / 3600)}h ago"
    else:
        return f"{int(diff / 86400)}d ago"


def expand(path):
    return os.path.expanduser(os.path.expandvars(path))


# ---------------------------------------------------------------------------
# Data collectors
# ---------------------------------------------------------------------------

def get_agent_data():
    """Read session metadata for each configured agent."""
    agents = []
    now = now_ms()
    for agent in AGENTS:
        name = agent["name"]
        sessions_dir = expand(agent.get("sessions_dir", ""))
        sessions_file = os.path.join(sessions_dir, "sessions.json")
        try:
            with open(sessions_file) as f:
                sessions = json.load(f)
            timestamps = [
                v.get("updatedAt")
                for v in sessions.values()
                if v.get("updatedAt")
            ]
            latest_ms = max(timestamps) if timestamps else 0
            session_count = len(sessions)
        except FileNotFoundError:
            logger.warning("Sessions file not found for agent %s: %s", name, sessions_file)
            latest_ms = 0
            session_count = 0
        except (json.JSONDecodeError, AttributeError) as exc:
            logger.error("Failed to parse sessions file for agent %s: %s", name, exc)
            latest_ms = 0
            session_count = 0
        except Exception:
            logger.exception("Unexpected error reading sessions for agent %s", name)
            latest_ms = 0
            session_count = 0

        age = now - latest_ms if latest_ms else None
        if age is None:
            status = "unknown"
        elif age < ACTIVE_THRESHOLD_MS:
            status = "active"
        elif age < RECENT_THRESHOLD_MS:
            status = "recent"
        else:
            status = "idle"

        agents.append({
            "name": name,
            "status": status,
            "last_active_ms": latest_ms,
            "last_active_ago": fmt_ago(latest_ms) if latest_ms else "never",
            "session_count": session_count,
        })
    return agents


def get_gateway_status():
    """Check gateway process health via systemd and port probe."""
    service_name = GATEWAY.get("service_name", "")
    process_name = GATEWAY.get("process_name", "")
    gw_port = GATEWAY.get("port", 0)

    status = "unknown"

    # Try systemd (user-level then system-level)
    if service_name:
        for args in [
            ["systemctl", "--user", "is-active", service_name],
            ["systemctl", "is-active", service_name],
        ]:
            try:
                result = subprocess.run(
                    args, capture_output=True, text=True, timeout=3,
                    env={**os.environ, "XDG_RUNTIME_DIR": f"/run/user/{os.getuid()}"},
                )
                s = result.stdout.strip()
                if s:
                    status = s
                    break
            except subprocess.TimeoutExpired:
                logger.warning("Timeout checking systemd service %s via %s", service_name, args[1])
            except Exception:
                logger.debug("Failed to check systemd service %s via %s", service_name, args[1], exc_info=True)

    # PID lookup
    pid = None
    if process_name:
        try:
            result = subprocess.run(
                ["pgrep", "-f", process_name],
                capture_output=True, text=True, timeout=3,
            )
            pid = result.stdout.strip().split("\n")[0] if result.stdout.strip() else None
        except Exception:
            logger.debug("Failed to pgrep for %s", process_name, exc_info=True)

    # Port probe fallback
    if gw_port and (status == "unknown" or status == ""):
        s = socket.socket()
        try:
            s.settimeout(1)
            s.connect(("127.0.0.1", gw_port))
            status = "active (port reachable)"
            pid = pid or "?"
        except Exception:
            logger.debug("Gateway port %d unreachable", gw_port)
            status = "unreachable"
        finally:
            s.close()

    return {
        "status": status,
        "pid": pid,
        "healthy": status.startswith("active"),
    }


# Regex pattern matching common secret/token formats.
# Each prefix is followed by a run of token-like characters.
_SECRET_RE = re.compile(
    r"(?:"
    r"sk-ant-[a-zA-Z0-9_\-]+"       # Anthropic API keys
    r"|sk-[a-zA-Z0-9_\-]{20,}"      # OpenAI-style keys
    r"|xoxb-[a-zA-Z0-9\-]+"         # Slack bot tokens
    r"|xoxp-[a-zA-Z0-9\-]+"         # Slack user tokens
    r"|xapp-[a-zA-Z0-9\-]+"         # Slack app tokens
    r"|ghp_[a-zA-Z0-9]{36,}"        # GitHub PATs
    r"|github_pat_[a-zA-Z0-9_]{20,}"  # GitHub fine-grained PATs
    r"|glpat-[a-zA-Z0-9\-_]{20,}"   # GitLab PATs
    r"|Bearer\s+[a-zA-Z0-9_\-./]+"  # Bearer tokens
    r"|token=[a-zA-Z0-9_\-./]+"     # token= query params
    r"|key=[a-zA-Z0-9_\-./]+"       # key= query params
    r")"
)


def _scrub(line):
    """Replace all secret-like substrings in a log line."""
    return _SECRET_RE.sub("***REDACTED***", line)


def get_errors():
    """Scan configured gateway log files for recent error lines."""
    errors = []
    log_files = GATEWAY.get("log_files", [])
    for lf in log_files:
        lf = expand(lf)
        try:
            result = subprocess.run(
                ["tail", "-n", "50", lf],
                capture_output=True, text=True, timeout=3,
            )
            for line in result.stdout.strip().splitlines():
                if any(kw in line.lower() for kw in [
                    "error", "fail", "exception", "crash", "timeout", "critical",
                ]):
                    errors.append({
                        "source": os.path.basename(lf),
                        "line": _scrub(line.strip()),
                    })
        except FileNotFoundError:
            logger.warning("Log file not found: %s", lf)
        except Exception:
            logger.exception("Error reading log file %s", lf)
    return errors[-20:]


def get_usage_data():
    """Compute recent daily activity across all agents."""
    daily = defaultdict(lambda: {"sessions": set(), "agents": set()})

    for agent in AGENTS:
        name = agent["name"]
        sessions_dir = expand(agent.get("sessions_dir", ""))
        sessions_file = os.path.join(sessions_dir, "sessions.json")
        try:
            with open(sessions_file) as f:
                sessions = json.load(f)
            for key, val in sessions.items():
                ua = val.get("updatedAt")
                if ua:
                    dt = datetime.fromtimestamp(ua / 1000, tz=timezone.utc)
                    date = dt.strftime("%Y-%m-%d")
                    daily[date]["sessions"].add(key)
                    daily[date]["agents"].add(name)
        except FileNotFoundError:
            pass  # Already logged in get_agent_data; avoid duplicate warnings.
        except Exception:
            logger.exception("Error computing usage data for agent %s", name)

    sorted_dates = sorted(daily.keys())[-14:]
    recent_activity = []
    for date in sorted_dates:
        recent_activity.append({
            "date": date,
            "sessionCount": len(daily[date]["sessions"]),
            "activeAgents": sorted(daily[date]["agents"]),
        })

    return {"recent_activity": recent_activity}


def collect_all():
    return {
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "generated_at_ms": now_ms(),
        "team": TEAM_NAME,
        "agents": get_agent_data(),
        "gateway": get_gateway_status(),
        "usage": get_usage_data(),
        "errors": get_errors(),
    }


# ---------------------------------------------------------------------------
# HTML template
# ---------------------------------------------------------------------------

HTML_TEMPLATE = """<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>{team} Dashboard</title>
<meta http-equiv="refresh" content="30">
<style>
  :root {{
    --bg: #0f1117;
    --card: #1a1d27;
    --border: #2a2d3a;
    --text: #e0e0e0;
    --muted: #888;
    --green: #2ecc71;
    --red: #e74c3c;
    --yellow: #f39c12;
    --blue: #3498db;
    --accent: #7c6af7;
  }}
  * {{ box-sizing: border-box; margin: 0; padding: 0; }}
  body {{
    background: var(--bg); color: var(--text);
    font-family: 'Inter', system-ui, sans-serif; font-size: 14px;
    padding: 24px; max-width: 1200px; margin: 0 auto;
  }}
  h1 {{ font-size: 20px; font-weight: 600; margin-bottom: 4px; }}
  .subtitle {{ color: var(--muted); font-size: 12px; margin-bottom: 28px; }}
  .grid {{ display: grid; gap: 16px; }}
  .grid-agents {{ grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); }}
  .grid-2 {{ grid-template-columns: repeat(auto-fit, minmax(320px, 1fr)); }}
  .card {{
    background: var(--card); border: 1px solid var(--border);
    border-radius: 10px; padding: 18px;
  }}
  .card-title {{
    font-size: 11px; font-weight: 600; letter-spacing: 0.08em;
    text-transform: uppercase; color: var(--muted); margin-bottom: 14px;
  }}
  .section-title {{
    font-size: 13px; font-weight: 600; color: var(--muted);
    margin: 28px 0 12px; text-transform: uppercase; letter-spacing: 0.05em;
  }}

  .agent-name {{ font-size: 18px; font-weight: 700; margin-bottom: 8px; }}
  .badge {{
    display: inline-flex; align-items: center; gap: 5px;
    padding: 3px 10px; border-radius: 99px; font-size: 11px; font-weight: 600;
  }}
  .badge-green  {{ background: rgba(46,204,113,0.15); color: var(--green); }}
  .badge-yellow {{ background: rgba(243,156,18,0.15);  color: var(--yellow); }}
  .badge-gray   {{ background: rgba(136,136,136,0.12); color: #aaa; }}
  .badge-red    {{ background: rgba(231,76,60,0.15);   color: var(--red); }}
  .dot {{ width: 6px; height: 6px; border-radius: 50%; background: currentColor; }}
  .agent-meta {{ margin-top: 10px; color: var(--muted); font-size: 12px; line-height: 1.7; }}

  .kv {{
    display: flex; justify-content: space-between; align-items: center;
    padding: 6px 0; border-bottom: 1px solid var(--border);
  }}
  .kv:last-child {{ border-bottom: none; }}
  .kv-label {{ color: var(--muted); font-size: 12px; }}
  .kv-value {{ font-weight: 500; font-size: 13px; }}
  .kv-value.ok {{ color: var(--green); }}
  .kv-value.warn {{ color: var(--yellow); }}
  .kv-value.err {{ color: var(--red); }}

  table {{ width: 100%; border-collapse: collapse; }}
  th {{
    text-align: left; font-size: 11px; color: var(--muted);
    padding: 4px 8px; font-weight: 500; border-bottom: 1px solid var(--border);
  }}
  td {{
    padding: 5px 8px; font-size: 12px;
    border-bottom: 1px solid rgba(42,45,58,0.5);
  }}
  tr:last-child td {{ border-bottom: none; }}

  .error-list {{ max-height: 280px; overflow-y: auto; }}
  .error-item {{
    padding: 6px 10px; border-left: 3px solid var(--red);
    background: rgba(231,76,60,0.06); border-radius: 0 4px 4px 0;
    margin-bottom: 6px; font-family: monospace; font-size: 11px;
    word-break: break-all; color: #ccc;
  }}
  .error-source {{ font-size: 10px; color: var(--muted); margin-bottom: 2px; }}
  .no-errors {{ color: var(--green); font-size: 13px; }}

  .refresh-note {{
    color: var(--muted); font-size: 11px; margin-top: 24px; text-align: right;
  }}
</style>
</head>
<body>

<h1>{team} Dashboard</h1>
<p class="subtitle" id="gentime">Loading...</p>

<p class="section-title">Agents</p>
<div class="grid grid-agents" id="agents"></div>

<p class="section-title">Gateway</p>
<div class="card" id="gateway"></div>

<p class="section-title">Activity (last 14 days)</p>
<div class="card">
  <table id="activity-table">
    <thead><tr><th>Date</th><th>Sessions</th><th>Active agents</th></tr></thead>
    <tbody id="activity-body"></tbody>
  </table>
</div>

<p class="section-title">Recent Errors</p>
<div class="card">
  <div class="error-list" id="errors"></div>
</div>

<p class="refresh-note">Auto-refreshes every 30s &middot; <span id="refresh-at"></span></p>

<script>
function el(tag, cls, text) {{
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = String(text);
  return e;
}}

async function load() {{
  const res = await fetch(window.location.pathname.replace(/\\/+$/, '') + '/api');
  const d = await res.json();

  document.getElementById('gentime').textContent =
    'Generated ' + new Date(d.generated_at).toLocaleTimeString();

  // --- Agents ---
  const statusCfg = {{
    active:  {{ cls: 'badge-green',  label: 'Active' }},
    recent:  {{ cls: 'badge-yellow', label: 'Recent' }},
    idle:    {{ cls: 'badge-gray',   label: 'Idle' }},
    unknown: {{ cls: 'badge-gray',   label: 'Unknown' }},
  }};
  const agentsEl = document.getElementById('agents');
  agentsEl.textContent = '';
  d.agents.forEach(a => {{
    const cfg = statusCfg[a.status] || statusCfg.unknown;
    const card = el('div', 'card');
    card.appendChild(el('div', 'card-title', 'Agent'));
    card.appendChild(el('div', 'agent-name', a.name));
    const badge = el('span', 'badge ' + cfg.cls);
    badge.appendChild(el('span', 'dot'));
    badge.appendChild(document.createTextNode(' ' + cfg.label));
    card.appendChild(badge);
    const meta = el('div', 'agent-meta');
    meta.appendChild(document.createTextNode('Last active: ' + a.last_active_ago));
    meta.appendChild(document.createElement('br'));
    meta.appendChild(document.createTextNode('Sessions: ' + a.session_count));
    card.appendChild(meta);
    agentsEl.appendChild(card);
  }});

  // --- Gateway ---
  const gw = d.gateway;
  const gwEl = document.getElementById('gateway');
  gwEl.textContent = '';
  gwEl.appendChild(el('div', 'card-title', 'Gateway'));
  const kvStatus = el('div', 'kv');
  kvStatus.appendChild(el('span', 'kv-label', 'Status'));
  kvStatus.appendChild(el('span', 'kv-value ' + (gw.healthy ? 'ok' : 'err'), gw.status));
  gwEl.appendChild(kvStatus);
  const kvPid = el('div', 'kv');
  kvPid.appendChild(el('span', 'kv-label', 'PID'));
  kvPid.appendChild(el('span', 'kv-value', gw.pid || 'n/a'));
  gwEl.appendChild(kvPid);

  // --- Activity ---
  const rows = (d.usage.recent_activity || []).slice().reverse();
  const tbody = document.getElementById('activity-body');
  tbody.textContent = '';
  if (rows.length === 0) {{
    const tr = document.createElement('tr');
    const td = el('td', null, 'No session data yet');
    td.colSpan = 3;
    td.style.color = '#888';
    tr.appendChild(td);
    tbody.appendChild(tr);
  }} else {{
    rows.forEach(r => {{
      const tr = document.createElement('tr');
      tr.appendChild(el('td', null, r.date));
      tr.appendChild(el('td', null, r.sessionCount));
      tr.appendChild(el('td', null, (r.activeAgents || []).join(', ')));
      tbody.appendChild(tr);
    }});
  }}

  // --- Errors ---
  const errEl = document.getElementById('errors');
  errEl.textContent = '';
  if (!d.errors || d.errors.length === 0) {{
    errEl.appendChild(el('span', 'no-errors', 'No recent errors'));
  }} else {{
    d.errors.forEach(e => {{
      const item = el('div', 'error-item');
      item.appendChild(el('div', 'error-source', e.source));
      item.appendChild(document.createTextNode(e.line));
      errEl.appendChild(item);
    }});
  }}

  document.getElementById('refresh-at').textContent =
    'Next refresh at ' + new Date(Date.now() + 30000).toLocaleTimeString();
}}

load().catch(e => console.error('Dashboard load error:', e));
</script>
</body>
</html>
"""


# ---------------------------------------------------------------------------
# HTTP server
# ---------------------------------------------------------------------------

class DashboardHandler(BaseHTTPRequestHandler):
    """Serves the dashboard HTML and JSON API."""

    def log_message(self, fmt, *args):
        """Route HTTP access logs through the stdlib logger at DEBUG level."""
        logger.debug("HTTP %s", fmt % args)

    def _check_secret(self):
        """Optional shared-secret auth (header only — never via query string)."""
        secret = os.environ.get("DASHBOARD_SECRET", "")
        if not secret:
            return True
        provided = self.headers.get("X-Dashboard-Secret", "")
        if not provided:
            return False
        return hmac.compare_digest(provided, secret)

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        if not self._check_secret():
            logger.warning(
                "Rejected request with invalid/missing secret from %s",
                self.client_address[0],
            )
            self._send(403, "text/plain", "Forbidden")
            return

        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/healthz":
            uptime_s = int(time.monotonic() - _START_TIME)
            health = {
                "status": "ok",
                "uptime_seconds": uptime_s,
                "config_path": CONFIG_PATH,
                "agents_configured": len(AGENTS),
            }
            self._send(200, "application/json", json.dumps(health))
        elif path == "/api":
            data = collect_all()
            self._send(200, "application/json", json.dumps(data, indent=2))
        elif path in ("/", ""):
            html = HTML_TEMPLATE.format(team=TEAM_NAME)
            self._send(200, "text/html; charset=utf-8", html)
        else:
            self.send_response(302)
            self.send_header("Location", "/")
            self.end_headers()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------

def main():
    port = int(os.environ.get("PORT", CFG.get("port", 8765)))
    bind = os.environ.get("BIND", CFG.get("bind", "127.0.0.1"))
    server = HTTPServer((bind, port), DashboardHandler)
    logger.info("Dashboard running on http://%s:%d/", bind, port)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        logger.info("Shutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
