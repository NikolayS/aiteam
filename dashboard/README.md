# AI Team Dashboard

A lightweight Python dashboard for monitoring autonomous AI agents on a VM.
Zero dependencies beyond the Python 3.10+ standard library.

![Dark theme with agent status cards, gateway health, activity log, and error feed](screenshot-placeholder.png)

## What it shows

| Section | Description |
|---------|-------------|
| **Agent status** | Per-agent cards: Active / Recent / Idle / Unknown, last-active time, session count |
| **Gateway health** | Systemd service status, PID, port-reachability fallback |
| **Activity log** | Last 14 days of session activity across all agents |
| **Recent errors** | Error/exception lines from gateway logs (secrets auto-scrubbed) |

Auto-refreshes every 30 seconds.

## Quick start

```bash
cd dashboard/

# 1. Create your config
cp config.example.json config.json
#    Edit config.json — set your team name, agent names, paths, etc.

# 2. Run
python3 server.py
#    → http://127.0.0.1:8765/
```

## Configuration

All settings live in `config.json` (or override path via `DASHBOARD_CONFIG` env var).

| Key | Description |
|-----|-------------|
| `team_name` | Display name shown in the dashboard header |
| `port` | HTTP listen port (default: `8765`, overridable via `PORT` env) |
| `bind` | Bind address (default: `127.0.0.1`, overridable via `BIND` env) |
| `agents` | Array of `{ "name": "...", "sessions_dir": "..." }` |
| `gateway.service_name` | Systemd unit name to check (optional) |
| `gateway.process_name` | Process name for `pgrep` fallback (optional) |
| `gateway.port` | Port to probe as last-resort health check (optional) |
| `gateway.log_files` | Array of log file paths to scan for errors |
| `thresholds.active_minutes` | Minutes since last activity to count as "Active" (default: 30) |
| `thresholds.recent_hours` | Hours since last activity to count as "Recent" (default: 4) |

### Session data format

The dashboard reads `sessions.json` files from each agent's `sessions_dir`. Expected format:

```json
{
  "session-id-1": { "updatedAt": 1741262400000 },
  "session-id-2": { "updatedAt": 1741262500000 }
}
```

`updatedAt` is a Unix timestamp in milliseconds.

## Deployment

### As a systemd service

```bash
# Edit dashboard.service — update paths for your system
sudo cp dashboard.service /etc/systemd/system/aiteam-dashboard.service
sudo systemctl daemon-reload
sudo systemctl enable --now aiteam-dashboard
```

### Behind nginx (with NoVNC tab bar)

The full nginx config is at `nginx/novnc.conf`. It serves both the dashboard
and NoVNC screen viewer, with a shared navigation bar injected via `sub_filter`:

- `/dashboard` — proxies to the dashboard backend on port 8765
- `/` — proxies to NoVNC (websockify) on port 6081, with a nav bar injected into the HTML

```bash
sudo cp nginx/novnc.conf /etc/nginx/sites-available/novnc
sudo ln -sf /etc/nginx/sites-available/novnc /etc/nginx/sites-enabled/novnc
sudo nginx -t && sudo systemctl reload nginx
```

When running behind a proxy at a subpath, the dashboard auto-discovers its
base path from `window.location.pathname` for API calls.

## API

### `GET /api`

Returns a JSON snapshot of all dashboard data.

```json
{
  "generated_at": "2026-03-11T12:00:00+00:00",
  "generated_at_ms": 1741694400000,
  "team": "my-ai-team",
  "agents": [
    {
      "name": "alice",
      "status": "active",
      "last_active_ms": 1741694300000,
      "last_active_ago": "1m ago",
      "session_count": 42
    }
  ],
  "gateway": { "status": "active", "pid": "12345", "healthy": true },
  "usage": {
    "recent_activity": [
      { "date": "2026-03-11", "sessionCount": 5, "activeAgents": ["alice", "bob"] }
    ]
  },
  "errors": []
}
```

## Security

### Defaults

- Binds to `127.0.0.1` — not internet-reachable without a reverse proxy
- No secrets are rendered — only derived status (active/idle, session counts)
- Log lines are scrubbed for API keys, tokens, and Bearer headers before display
- `X-Content-Type-Options: nosniff` on all responses

### Optional shared-secret auth

Set `DASHBOARD_SECRET` env var to require a secret on every request (header only — never via query string, to avoid leaking in browser history and logs):

```bash
export DASHBOARD_SECRET=my-secret
python3 server.py
# Access: curl -H "X-Dashboard-Secret: my-secret" http://127.0.0.1:8765/
```

### Recommended production setup

- Put behind an authentication proxy (Cloudflare Access, OAuth2 Proxy, Tailscale, etc.)
- Use HTTPS (terminate TLS at the proxy)
- Run as a non-root user with `ProtectHome=read-only` (see `dashboard.service`)

## Extending

The dashboard is intentionally minimal. Common extensions:

- **Billing/account panel** — add a data collector that reads your LLM provider credentials
- **Custom metrics** — add new sections to `collect_all()` and corresponding JS in the HTML template
- **Webhook alerts** — post to Slack/Telegram when an agent goes idle or errors spike
- **Multi-VM fleet view** — aggregate `/api` responses from multiple VMs into a central dashboard

## License

MIT — same as the parent project.
