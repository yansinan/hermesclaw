name: wechat-route
version: 0.1.0
author: automated
license: MIT

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

