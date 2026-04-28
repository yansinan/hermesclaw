# HermesClaw (wechat-route)

简短说明

HermesClaw 是一个在容器中把 Hermes Agent 与 OpenClaw 同时运行在同一微信账号的轻量代理。该仓库已整理为 Hermes skill（wechat-route），便于放入 `~/.hermes/skills/wechat-route` 直接启用并通过 `hermes cron` 做守护。

快速上手（容器/无 systemd 场景）

1) 将仓库放到 Hermes skills：
   - 推荐软链（快速回滚）：
     mkdir -p ~/.hermes/skills && ln -s /opt/data/wechat-route ~/.hermes/skills/wechat-route

2) 确认可执行并检查脚本：
   - chmod +x ~/.hermes/skills/wechat-route/manager.py
   - manager.py 提供命令：start | stop | status | cron | logs

3) 注册 hermes cron（示例）：
   - hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "wechat-route/manager.py cron"
   - 或使用绝对路径：hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "~/.hermes/skills/wechat-route/manager.py cron"

Note: Minimal container images may lack ss/netstat. The cron watcher uses PID file + port check by attempting a TCP connect. This skill now includes a small helper script scripts/check_port.py for reliable portable port checks; manager.py cron can call it if needed.

4) 立即测试：
   - ~/.hermes/skills/wechat-route/manager.py cron
   - 检查仓库根下的 hermesclaw.pid 与 hermesclaw.log 确认是否已启动

重要说明

- 容器内通常没有 systemd：请使用上文的 hermes cron 或外部容器管理器来守护进程。不要依赖 systemd 单元。
- 路径与文件：PID/LOG/STATE 默认放在仓库根：hermesclaw.pid, hermesclaw.log, router_state.json；可通过环境变量覆盖（ HERMES_PROXY_PORT, OPENCLAW_PROXY_PORT）。
- 若要把改动推回远端：容器内通常没有 Git 凭证，请使用已生成的 bundle（/opt/data/wechat-route/feature-container-hermes-side.bundle）在有凭证的宿主机上完成推送。

故障排查要点（快速）

- start 无法启动：查看 hermesclaw.log；确认端口 19998/19999 未被占用，.env 配置正确。
- 重复启动：manager.py 会检测 pidfile 与本地回环端口以避免重复 spawn。
- 停止失败：先使用 manager.py stop，再用 ps / kill 检查残留进程。

贡献与开发

- 保持 hermesclaw.py 单文件设计（CONTRIBUTING.md 指南）。
- 单元测试位于 tests/，建议在覆盖 manager 的逻辑后再提交变更。

如果你要我现在：
A) 把 manager.py 软链到 ~/.hermes/scripts 并注册 cron 并测试，或
B) 先不动（我已把 README 整理）

欢迎选择下一步。