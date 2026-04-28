当前任务状态 — 起始点快照（便于日后继续）

时间戳（容器时间）：请在需要时用 date 命令核对当前时间。

工作目录与仓库
 - 仓库路径：${HERMES_HOME:-/opt/data}/wechat-route
 - 已在 ~/.hermes/skills 创建软链：~/.hermes/skills/wechat-route -> ${HERMES_HOME:-/opt/data}/wechat-route
- 当前 Git 分支：skill/wechat-route（本地提交存在，远端 push 需宿主机完成）

关键文件与配置
- manager.py：仓库根的管理脚本，提供 start|stop|status|cron|logs
- hermesclaw.py：主服务文件（保持单文件设计）
 - .env：已更新，STATE_FILE=${HERMES_HOME:-/opt/data}/wechat-route/router_state.json, LOG_FILE=${HERMES_HOME:-/opt/data}/wechat-route/hermesclaw.log
- PID/LOG/STATE 存放：仓库根（hermesclaw.pid, hermesclaw.log, router_state.json）
- mention_aliases.json：动态别名文件（支持 MENTION_ALIASES_FILE 覆盖）
 - feature-container-hermes-side.bundle：已存在于 ${HERMES_HOME:-/opt/data}/wechat-route，用于离线推送

已完成的安装/修改里程碑
 - 仓库已从 ${HERMES_HOME:-/opt/data}/hermesclaw 重命名为 ${HERMES_HOME:-/opt/data}/wechat-route 并更新相关路径引用
- manager.py 已写入，并在仓库中（使用相对 BASE 路径）
- SKILL.md 与 README 已更新以反映容器/skill 部署方式
- 已在本地 git 提交："chore(repo): update paths after repo rename to wechat-route"

建议的下步任务（供日后继续）
1) 把 manager.py 链接到 ~/.hermes/scripts 并注册 hermes cron job（名：wechat-route-watchdog），或使用 hermes cron create 注册相对路径脚本
2) 运行一次 manager.py cron 以验证能 spawn hermesclaw，并检查 hermesclaw.pid / hermesclaw.log
3) 为 manager.py 添加 tests/test_manager.py，覆盖 pidfile、端口检测、start/stop 流程
4) 在宿主机上将本地分支通过 bundle 推送到远端（参考 PUSH_INSTRUCTIONS.md）

回滚指引
- 删除 skill：rm -rf ~/.hermes/skills/wechat-route（若为软链则删除软链）
- 停止并移除 cron：hermes cron remove <job_id>；manager.py stop
 - 恢复旧路径：如果需要把 ${HERMES_HOME:-/opt/data}/hermesclaw 恢复名，使用 mv ${HERMES_HOME:-/opt/data}/wechat-route ${HERMES_HOME:-/opt/data}/hermesclaw

