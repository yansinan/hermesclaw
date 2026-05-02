---
name: wechat-route
version: 0.1.0
author: automated
license: MIT
description: Hermes skill to manage and watchdog hermesclaw (wechat-route).
---

wechat-route skill: start and watch hermesclaw from Hermes. Place this directory under ~/.hermes/skills/wechat-route and use hermes cron to register the 'cron' entrypoint.

Usage examples:
  # install (manual)
  cp -r skill/wechat-route ~/.hermes/skills/

  # register cron (run once):
  hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "wechat-route/manager.py cron"

Commands provided by manager.py:
  start, stop, status, cron, logs

Environment variables:
  HERMES_PROXY_PORT, OPENCLAW_PROXY_PORT (defaults 19998/19999)
  PYTHON to override interpreter

Watchdog notes (added by automated cron run):

Operational findings (session 2026-04-29):

- CONFIRMED (read-only audit): a Hermes cron job named "微信路由代理定时保持" is registered and active (Schedule: every 15m). Verified with `/opt/hermes/.venv/bin/hermes cron list` on the host. See evidence: terminal output of `hermes cron list` and job id 77896962ce17.
- CONFIRMED (CLI incompatibility): this Hermes binary rejects `--schedule` and `--json` in some cron-related calls; attempts to create cron with `--schedule` failed (see /opt/data/hooks/wechat-route/hook.log entries showing "hermes: error: unrecognized arguments: --schedule"). Adjust automations to use the Hermes CLI form supported by your installation or create cron entries manually then verify with `hermes cron list`.
- CONFIRMED (manager.py behaviour): the manager script's doc comment says the no-arg default is 'cron' but the implementation currently sets no-arg to 'restart' (source: /opt/data/skills/local/wechat-route/manager.py lines ~169-188). This mismatch is a real operational risk: wrappers or cron that invoke the script without an explicit subcommand can trigger an unintended restart. Recommendation: ensure cron/hooks call `manager.py cron` explicitly, or change the script default to 'cron' via a code patch and add unit tests to prevent regressions.
- CONFIRMED (hermesclaw runtime): hermesclaw is running (pid file /opt/data/skills/local/wechat-route/hermesclaw.pid contains PID 53) and hermesclaw.log contains multiple start/stop/restart traces (evidence for intermittent restarts). Path: /opt/data/skills/local/wechat-route/hermesclaw.log.

These confirmations were added after a read-only operational audit and are intended to be guidance for operators. Keep sensitive values masked in documentation; see /opt/data/.env for environment variable names (sensitive values replaced with [REDACTED] in reports).
- During interactive troubleshooting we exercised manager.py and the hook handler and discovered three recurring operational issues that are worth documenting and encoding into the skill's README and watchdog behaviour:
  1) .env shell-style placeholders not expanded: the skill's .env files use shell-style expressions such as ${HERMES_HOME:-/opt/data} for STATE_FILE and LOG_FILE. The hermesclaw runtime does not reliably perform shell expansion in all start contexts; when HERMES_HOME is not present in the process environment this resolves to /opt/data and causes FileNotFoundError and failed writes. Evidence: /opt/data/skills/local/wechat-route/.env and hermesclaw.log tracebacks observed during the session.
     - Recommendation (safe): change STATE_FILE and LOG_FILE to absolute paths under the skill directory (example: LOG_FILE=/opt/data/skills/local/wechat-route/hermesclaw.log) or ensure HERMES_HOME is exported before manager start. This is a minimal, reversible change.
     - Recommendation (robust): add a small helper to manager.py or to hermesclaw.py to perform safe expansion of ${VAR:-default} (a tested expand_colon_dash Python helper is available in debugging notes). This requires a small code change and tests.
  2) hermes CLI invocation as a module can fail: some environments attempted to run hermes with `python -m hermes` which failed with "/usr/bin/python3: No module named hermes.__main__; 'hermes' is a package and cannot be directly executed". Evidence: gateway startup logs and hook cron creation failure messages captured during debugging.
     - Recommendation: handler scripts (and the skill) should prefer using an absolute hermes executable path when available (e.g., ~/.local/bin/hermes, HERMES_HOME/.venv/bin/hermes, /opt/hermes/.venv/bin/hermes) instead of relying on `python -m hermes`. Optionally provide a small hermes-wrapper script under the skill dir to guarantee consistent invocation for cron jobs.
  3) manager.py default command vs cron usage: the manager.py script's no-arg behavior historically defaulted to 'restart' which can cause unexpected restarts if wrappers or cron invoke the script without explicit subcommands. Evidence: SKILL.md and manager.py main() show the default behaviour; watchdog observations and references/observed-default-behaviour.md discuss this.
     - Recommendation: Prefer making the default no-arg command 'cron' (the safer watchdog entrypoint), or ensure cron entries explicitly call "manager.py cron". The skill documentation should call this out and provide an example hermes cron create command that includes the explicit 'cron' subcommand.

Saved changes and rationale:
- The skill metadata and README are updated here to include the operational findings above so future operators and automated jobs can reference them before deploying a cron or changing environment variables. The findings are non-trivial: they required running manager.py, inspecting logs, detecting differences between process env and .env placeholders, and updating handler lookup behaviour; they are therefore worth preserving.

Original watchdog notes:
- Observed behavior and safe watchdog pattern:
  - manager.py status checks both pidfile and listening ports; it returns running if either is present. (Source: manager.py lines 59-79 and 140-148)
  - Default no-argument command falls back to 'restart' (not 'cron'). (Source: manager.py lines 169-188)
  - restart() performs stop() then start(). (Source: manager.py lines 151-155)
  - start() prefers PYTHON env var then python3; it writes a pidfile and detects ports to confirm startup. (Source: manager.py lines 82-110)

- Operational recommendations (saved for operators and automated watchdogs):
  1) Provide an explicit notification recipient config (e.g., ADMIN_ILINK_UID) and a safe send wrapper (scripts/send_admin_notification.py) that reads env and calls hermesclaw.send_text_ilink. Avoid ad-hoc python -c executions from cron without approval. (See references/cron-observations.md lines 20-28)
  2) Persist restart counters under skill directory (restart_count.txt) or configurable HERMES_HOME; rotate/truncate if it grows. The watchdog can increment this file each successful restart. (Observed path: /opt/data/skills/local/wechat-route/restart_count.txt)
  3) Document that automated runs must not perform external network sends unless ADMIN_ILINK_UID is configured and operator consent is given. (references/cron-observations.md lines 34-38)
  4) Prefer making manager.py default command 'cron' for hermes cron entrypoint or explicitly document that empty invocation performs 'restart'. (manager.py lines 169-188)

- What was saved by this edit:
  - The above watchdog notes and recommendations are included directly in the skill metadata so future operators and automated jobs can reference them.
  - Sources for the observations are included as in-file references; see manager.py and references/cron-observations.md in the skill directory.

Supporting files to add (suggested, not created by this edit):
  - scripts/send_admin_notification.py  # safe wrapper that reads ADMIN_ILINK_UID and ILINK_TOKEN and calls hermesclaw.send_text_ilink
  - scripts/notify_config_example.env     # documents ADMIN_ILINK_UID and ADMIN_ILINK_BASE_URL

Notes:
- This edit does not change runtime code; it documents operational findings and recommendations discovered by automated cron runs.
- For code changes (e.g., adding the safe notification wrapper), create the scripts/ files and add them in a follow-up patch.
