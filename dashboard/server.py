#!/usr/bin/env python3
"""
AI Team Dashboard — lightweight status dashboard for autonomous AI agents.

Reads agent session data, gateway health, and log errors from local files
and serves a single-page dashboard with a JSON API.

Configuration: ./config.json (copy config.example.json to get started)
"""

import json
import os
import socket
import subprocess
import sys
import time
from datetime import datetime, timezone
from http.server import HTTPServer, BaseHTTPRequestHandler
from urllib.parse import urlparse, parse_qs


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
        print(f"Config not found at {CONFIG_PATH}", file=sys.stderr)
        print("Copy config.example.json to config.json and edit it.", file=sys.stderr)
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
        except Exception:
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
            except Exception:
                pass

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
            pass

    # Port probe fallback
    if gw_port and (status == "unknown" or status == ""):
        try:
            s = socket.socket()
            s.settimeout(1)
            s.connect(("127.0.0.1", gw_port))
            s.close()
            status = "active (port reachable)"
            pid = pid or "?"
        except Exception:
            status = "unreachable"

    return {
        "status": status,
        "pid": pid,
        "healthy": status.startswith("active"),
    }


# Simple patterns that should never appear in dashboard output
_SECRET_PATTERNS = ["sk-", "xoxb-", "xoxp-", "Bearer ", "token=", "key="]


def _scrub(line):
    """Strip substrings that look like secrets from a log line."""
    for pat in _SECRET_PATTERNS:
        idx = line.find(pat)
        if idx != -1:
            # Mask everything from the pattern start to the next whitespace
            end = len(line)
            for ch in (" ", "\t", '"', "'", ",", "}", "]"):
                pos = line.find(ch, idx + len(pat))
                if pos != -1:
                    end = min(end, pos)
            line = line[:idx] + "***REDACTED***" + line[end:]
    return line


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
        except Exception:
            pass
    return errors[-20:]


def get_usage_data():
    """Compute recent daily activity across all agents."""
    from collections import defaultdict

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
        except Exception:
            pass

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
  document.getElementById('agents').innerHTML = d.agents.map(a => {{
    const cfg = statusCfg[a.status] || statusCfg.unknown;
    return `
    <div class="card">
      <div class="card-title">Agent</div>
      <div class="agent-name">${{a.name}}</div>
      <span class="badge ${{cfg.cls}}">
        <span class="dot"></span>
        ${{cfg.label}}
      </span>
      <div class="agent-meta">
        Last active: ${{a.last_active_ago}}<br>
        Sessions: ${{a.session_count}}
      </div>
    </div>`;
  }}).join('');

  // --- Gateway ---
  const gw = d.gateway;
  document.getElementById('gateway').innerHTML = `
    <div class="card-title">Gateway</div>
    <div class="kv"><span class="kv-label">Status</span>
      <span class="kv-value ${{gw.healthy ? 'ok' : 'err'}}">${{gw.status}}</span>
    </div>
    <div class="kv"><span class="kv-label">PID</span>
      <span class="kv-value">${{gw.pid || 'n/a'}}</span>
    </div>
  `;

  // --- Activity ---
  const rows = (d.usage.recent_activity || []).slice().reverse();
  const tbody = document.getElementById('activity-body');
  tbody.innerHTML = rows.length
    ? rows.map(r => `<tr>
        <td>${{r.date}}</td>
        <td>${{r.sessionCount}}</td>
        <td>${{(r.activeAgents || []).join(', ')}}</td>
      </tr>`).join('')
    : '<tr><td colspan="3" style="color:#888">No session data yet</td></tr>';

  // --- Errors ---
  const errEl = document.getElementById('errors');
  if (!d.errors || d.errors.length === 0) {{
    errEl.innerHTML = '<span class="no-errors">No recent errors</span>';
  }} else {{
    errEl.innerHTML = d.errors.map(e => `
      <div class="error-item">
        <div class="error-source">${{e.source}}</div>
        ${{e.line}}
      </div>
    `).join('');
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

    # Suppress default access logs
    def log_message(self, fmt, *args):
        pass

    def _check_secret(self):
        """Optional shared-secret auth. Returns True if OK."""
        secret = os.environ.get("DASHBOARD_SECRET", "")
        if not secret:
            return True
        qs = parse_qs(urlparse(self.path).query)
        if qs.get("secret", [None])[0] == secret:
            return True
        if self.headers.get("X-Dashboard-Secret") == secret:
            return True
        return False

    def _send(self, code, content_type, body):
        self.send_response(code)
        self.send_header("Content-Type", content_type)
        self.send_header("X-Content-Type-Options", "nosniff")
        self.end_headers()
        self.wfile.write(body if isinstance(body, bytes) else body.encode())

    def do_GET(self):
        if not self._check_secret():
            self._send(403, "text/plain", "Forbidden")
            return

        path = urlparse(self.path).path.rstrip("/") or "/"

        if path == "/api":
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
    print(f"Dashboard running on http://{bind}:{port}/")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nShutting down.")
        server.server_close()


if __name__ == "__main__":
    main()
