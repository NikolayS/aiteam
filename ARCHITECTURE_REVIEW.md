# Architecture Review

**Reviewer:** Review Agent #4 (Architecture and Design)
**Date:** 2026-03-12
**Scope:** Full codebase — documentation, dashboard server, configuration, deployment model

---

## 1. Single-File Dashboard Server (569 lines)

### Assessment: Appropriate at current scale, but approaching the split threshold

The server cleanly separates concerns via comment blocks into five logical sections: config, helpers, data collectors, HTML template, and HTTP server. At 569 lines this is manageable by a single developer, and the single-file approach eliminates import-path complexity during deployment — a real benefit given the zero-dependency constraint.

### Where it starts to strain

- **The HTML template is 217 lines of inline HTML/CSS/JS** (lines 284-501). This is the largest single block in the file. Any UI change requires editing Python source, restarting the server, and hoping the double-brace escaping (`{{`/`}}`) for `.format()` does not introduce regressions. This has already caused at least one bug (commit `d4e0949`).
- **Data collection is synchronous and blocking.** Every `GET /api` call runs `subprocess.run` for systemd checks, `pgrep`, and `tail` on log files. With 3+ agents and multiple log files, this serializes into potentially 1-2 seconds of blocking per request. The `http.server` module is single-threaded by default, so one slow request blocks all others.
- **Secret scrubbing logic is tightly coupled to the error collector.** The `_SECRET_RE` pattern and `_scrub()` function are a security boundary — they deserve their own test coverage independent of the rest of the file.

### Recommended module boundaries (when splitting becomes necessary)

| Module | Contents | Lines (approx) |
|--------|----------|-----------------|
| `config.py` | `load_config()`, config constants, future schema validation | 40 |
| `collectors.py` | `get_agent_data()`, `get_gateway_status()`, `get_errors()`, `get_usage_data()`, `collect_all()` | 150 |
| `scrub.py` | `_SECRET_RE`, `_scrub()` — isolated for independent testing | 30 |
| `template.py` | `HTML_TEMPLATE` string, or better: read from a `.html` file at startup | 220 |
| `server.py` | `DashboardHandler`, `main()`, imports from above | 80 |

**Recommendation:** Do not split yet. The single file is working. Instead, address two targeted improvements:

1. **Extract the HTML template to an external file** (`dashboard/template.html`), loaded once at startup. This eliminates the double-brace escaping problem entirely and allows editing the UI without touching Python.
2. **Switch to `ThreadingHTTPServer`** (available since Python 3.7) by changing `HTTPServer` to `ThreadingHTTPServer` from `http.server`. One-line change, eliminates request serialization.

---

## 2. Zero External Dependencies Constraint

### Where it helps

- **Deployment is trivial.** Copy the file, run `python3 server.py`. No virtualenv, no pip, no version conflicts, no supply chain attack surface. This is a genuine operational advantage for a system designed to run unattended on VMs managed by AI agents.
- **Security audit surface is minimal.** The only code that needs reviewing is what is in the repo.
- **Matches the deployment model.** A single VM running systemd services does not benefit from a web framework. The stdlib `http.server` is sufficient for an internal dashboard serving one page and one API endpoint.

### Where it hurts

- **No structured logging.** The server suppresses all access logs (`log_message` is a no-op) and has no error logging of its own. The stdlib `logging` module is available and unused — this is a missed opportunity. Structured logging would help with debugging without adding dependencies.
- **No argument parsing.** Config path, port, and bind address are handled via a mix of env vars and JSON config. The stdlib `argparse` module would provide `--config`, `--port`, `--bind` flags with help text, for zero additional cost.
- **No config validation.** A missing `agents` key or a malformed `sessions_dir` path silently produces empty data. The stdlib does not have JSON Schema validation, but basic assertions on required fields at startup would catch configuration errors early.
- **Template rendering is crude.** The `.format()` approach for HTML requires escaping all CSS/JS braces. The stdlib `string.Template` class uses `$variable` syntax and would avoid this entirely, though it is less flexible. Alternatively, loading HTML from a file and using `str.replace()` for the one dynamic value (`{team}`) would be simpler and safer.
- **No HTTPS capability.** The stdlib `ssl` module can wrap the server socket for TLS. While the current design assumes a reverse proxy, providing a `--tls-cert` / `--tls-key` option would allow secure standalone operation for smaller deployments.

### Underused stdlib modules

| Module | Potential use |
|--------|---------------|
| `logging` | Replace silent `log_message` override; add structured error/access logging |
| `argparse` | CLI flags for config path, port, bind, and verbosity |
| `unittest` | Test secret scrubbing, data collection, config validation |
| `ssl` | Optional TLS termination |
| `concurrent.futures.ThreadPoolExecutor` | Non-blocking data collection |
| `pathlib` | Cleaner path handling than `os.path.join` + `os.path.expanduser` |

**Recommendation:** Adopt `logging` and `argparse` immediately — they are zero-cost improvements. Add `pathlib` when any path-handling code is next touched.

---

## 3. JSON Config Format

### Assessment: Sufficient for current needs, but fragile

The config structure is flat and readable. JSON is a reasonable choice for a config file that will be hand-edited by operators on a VM — it is universally understood and parsed by the stdlib.

### Specific concerns

**No validation at load time.** `load_config()` catches `FileNotFoundError` but does not validate the structure. If `agents` is missing or misspelled, the dashboard starts and shows nothing. If `sessions_dir` contains a typo, the dashboard silently reports all agents as "unknown." A 20-line validation function at startup that checks required keys and path existence would catch most configuration errors.

**Environment variable interpolation is inconsistent.** The `expand()` helper runs `os.path.expandvars()` on paths, so `$HOME/.aiteam/...` works in `sessions_dir`. But this is not documented, and the config example uses `~` (tilde) rather than env vars. The `port` and `bind` values support env var overrides via separate `PORT`/`BIND` env vars, but there is no general mechanism. This creates two mental models for configuration.

**No comments support.** JSON does not allow comments. The example config has no way to document individual fields inline. This is a known JSON limitation. If the config grows beyond what it is today, consider supporting JSONC (strip `//` comments before parsing — a 3-line preprocessing step) or switching to TOML (stdlib in Python 3.11+).

**Secrets in config.** The config example does not contain secrets, but the `DASHBOARD_SECRET` is handled via env vars rather than config. This is the right approach, but it should be explicitly documented: "Never put secrets in config.json — use environment variables."

### Recommendations

1. **Add a `validate_config()` function** that runs at startup and exits with a clear error message if required fields are missing or paths do not exist.
2. **Document the env var expansion behavior** in the config example or README.
3. **Keep JSON for now.** The config is small (25 lines). Do not switch formats until there is a concrete pain point.

---

## 4. Multi-Agent Model

### Architecture: Shared VM, single user, separate VNC displays

The current model runs all agents under one Linux user (`agent`) with isolation only at the VNC display level. This is a pragmatic choice for a small team (2-5 agents), but it has clear scalability and isolation limits.

### Scalability limits

**CPU and memory contention.** The recommended VM is 4 vCPU / 16 GiB RAM. A single Claude Code session with a browser open can consume 2-4 GiB of RAM and significant CPU during active work. Three agents running concurrently on a 4-vCPU VM will contend for resources, and the system provides no mechanism to detect or manage this.

**VNC display ceiling.** The display-number scheme (`:1`, `:2`, `:3`) scales to perhaps 10 agents before port management and nginx configuration become unwieldy. However, the real limit is resource contention, not port space.

**No horizontal scaling path.** The documentation describes single-VM deployment only. There is no mechanism to add a second VM and distribute agents across machines. The dashboard's `/api` endpoint could serve as the foundation for a multi-VM aggregation layer, as noted in the dashboard README, but nothing exists today.

### Isolation gaps

| Concern | Current state | Risk |
|---------|---------------|------|
| **Filesystem** | All agents share one home directory | Agent A can read/modify Agent B's code, credentials, and session data. A misbehaving agent could delete another agent's work. |
| **Network** | All agents share one network namespace | Agent A can connect to Agent B's localhost services. No network-level isolation. |
| **Processes** | All agents run as the same user | Agent A can `kill` Agent B's processes. No process-level isolation. |
| **Secrets** | Shared API keys (by design) | One compromised agent exposes all API keys. |
| **Resource limits** | None configured | One runaway agent (e.g., infinite loop, memory leak) can OOM-kill the entire VM. |

### What happens when one agent consumes all resources

Currently: the other agents and the dashboard become unresponsive. The VM may OOM-kill random processes, potentially including the VNC servers or the gateway. There is no alerting mechanism to notify operators.

### Recommendations (priority order)

1. **Add systemd resource limits.** Use `MemoryMax=`, `CPUQuota=`, and `TasksMax=` in systemd unit files for each agent's processes. This prevents one agent from starving others. Zero dependencies, immediate effect.
2. **Add a health watchdog.** A simple script (cron job or systemd timer) that checks process counts, memory usage, and disk space, and writes warnings to a log file that the dashboard can surface.
3. **Document the isolation model explicitly.** Operators should understand that this is a shared-tenancy model and make informed decisions about what work to assign to co-located agents.
4. **For production multi-agent deployments**, consider separate Linux users per agent with filesystem permissions, or container-based isolation via Docker (which is already installed per the software stack).

---

## 5. Missing Components — Priority Order

The system currently has: documentation, a dashboard, and systemd service definitions. It lacks several components that will become necessary as the system matures.

### Proposed priority order

| Priority | Component | Rationale |
|----------|-----------|-----------|
| **P0** | **Config validation** | Prevents silent misconfiguration. A 20-line function. Lowest effort, highest defensive value. Should be done in the next change to the dashboard. |
| **P1** | **Tests for secret scrubbing** | The `_scrub()` function is a security boundary. If a new secret format is not covered, credentials leak to the dashboard UI. Unit tests using stdlib `unittest` would take 1-2 hours and protect against regressions permanently. |
| **P2** | **Structured logging** | The dashboard currently has no observability. When something goes wrong, there is no trail. Add stdlib `logging` with timestamps, levels, and rotation. |
| **P3** | **Resource limits for agents** | Systemd `MemoryMax` / `CPUQuota` in agent service files. Prevents cascading failures. Requires understanding of actual resource usage per agent. |
| **P4** | **Health check / watchdog** | A lightweight script that runs on a timer and checks agent liveness, disk space, and memory. Can write to a file that the dashboard reads. |
| **P5** | **Provisioning automation** | The quickstart guide instructs users to paste prompts into Claude Code for VM setup. A shell script or cloud-init template that automates the full stack (VNC, noVNC, nginx, agent user, systemd services) would reduce setup time from hours to minutes. The cloud-init template in infrastructure.md is a partial solution but covers only OS hardening, not the software stack. |
| **P6** | **CI/CD** | Even minimal CI (lint with `python3 -m py_compile server.py`, run unit tests) would catch syntax errors and scrubbing regressions before they reach production. GitHub Actions with no external dependencies would work. |
| **P7** | **Orchestrator** | A process that manages agent lifecycle (start, stop, schedule work, monitor health) across one or more VMs. This is the most complex missing piece and should not be built until the simpler components (P0-P4) are in place and the operational model is proven. |

### Rationale for this ordering

P0-P2 are defensive improvements that prevent existing functionality from failing silently. They require minimal effort (hours, not days) and have no architectural risk.

P3-P4 address the most likely production failure mode: resource exhaustion on a shared VM.

P5-P6 improve the developer/operator experience but are not blocking for a system that is already running.

P7 (orchestrator) is tempting to build but premature. The system currently has 2-3 agents on one VM. An orchestrator adds complexity that is not justified until there are multiple VMs or more than 5 agents to manage.

---

## 6. Process Management with Systemd

### Assessment: Appropriate and well-suited

Systemd is the right choice for this use case. It provides:

- **Automatic restart on crash** (`Restart=on-failure`, `RestartSec=5` in the dashboard service).
- **Boot-time startup** (`WantedBy=multi-user.target`).
- **Security hardening** (the dashboard service uses `NoNewPrivileges`, `ProtectSystem=strict`, `ProtectHome=read-only`, `PrivateTmp`). This is well-configured and better than most projects at this stage.
- **Structured logging** via journald (`journalctl -u dashboard`), even though the dashboard does not write structured logs itself.
- **Resource control** via cgroup properties (`MemoryMax`, `CPUQuota`) — available but not yet used.

### Lifecycle handling

| Event | Systemd behavior | Assessment |
|-------|-------------------|------------|
| Start | `ExecStart` runs the Python process | Works correctly |
| Stop | SIGTERM sent to the process | `server.serve_forever()` does not handle SIGTERM. It only handles `KeyboardInterrupt` (SIGINT). The process will be killed by SIGTERM's default handler, which is abrupt but acceptable for a stateless HTTP server. |
| Restart | `Restart=on-failure` with 5s delay | Appropriate. Prevents restart loops. |
| Crash | Same as restart | Appropriate. |
| Upgrade | No mechanism | Operator must manually `systemctl restart`. Acceptable at current scale. |

### Concerns

**SIGTERM handling.** The `main()` function catches `KeyboardInterrupt` but not `SIGTERM`. When systemd stops the service, it sends SIGTERM. The Python process will terminate immediately without running cleanup code. For a stateless dashboard this is acceptable, but adding a `signal.signal(signal.SIGTERM, ...)` handler would be more correct and would allow future cleanup logic (e.g., flushing logs).

**No readiness notification.** The service is `Type=simple`, meaning systemd considers it "started" as soon as the process is forked, before the HTTP server is actually listening. Using `Type=notify` with `systemd.daemon.notify("READY=1")` would be more accurate, but this requires the `systemd` Python package (an external dependency) or writing to the notification socket manually. Given the zero-dependency constraint, `Type=simple` is the pragmatic choice.

**VNC/noVNC service dependencies.** The VNC-related services use `After=network.target` but do not declare dependencies on each other. The websockify service (`novnc-agent2.service`) declares `After=vncserver@2.service` but not `Requires=`. If the VNC server fails to start, websockify will start anyway and fail to connect. Adding `Requires=vncserver@2.service` would make this dependency explicit.

### Recommendations

1. Add a SIGTERM handler in `main()` for clean shutdown (low priority, minimal risk).
2. Add `Requires=` dependencies between VNC and websockify services in the documentation templates.
3. Consider adding `StartLimitBurst=5` and `StartLimitIntervalSec=60` to prevent infinite restart loops if the server has a persistent startup failure (e.g., config file missing).

---

## Summary of Top Recommendations

| # | Recommendation | Effort | Impact |
|---|----------------|--------|--------|
| 1 | Add config validation at startup | 1-2 hours | Prevents silent misconfiguration |
| 2 | Extract HTML template to external file | 1 hour | Eliminates brace-escaping bugs, cleaner separation |
| 3 | Switch to `ThreadingHTTPServer` | 5 minutes | Eliminates request serialization |
| 4 | Add `logging` module usage | 1-2 hours | Provides operational observability |
| 5 | Write unit tests for `_scrub()` | 1-2 hours | Protects security boundary |
| 6 | Add systemd resource limits to agent services | 1 hour | Prevents resource starvation |
| 7 | Add `argparse` for CLI flags | 30 minutes | Better UX for operators |
| 8 | Document the isolation model | 1 hour | Sets correct expectations |

Items 1-5 should be addressed before adding new features. Items 6-8 should be addressed before scaling beyond 2-3 agents.
