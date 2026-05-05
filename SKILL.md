---
name: wechat-route
version: 0.2.0
author: Hermes Operators
license: MIT
description: 使用 manager.py 管理 hermesclaw（启动/停止/状态/守护/通知）
---

wechat-route 技能用于在 Hermes 环境中运维 hermesclaw（微信路由代理）。

目标

- 用统一命令管理服务生命周期。
- 用 cron 方式做轻量守护（定时触发 manager.py cron）。
- 支持通过 manager.py msg 给管理员发送带前缀 [Route] 的微信通知。

命令入口

- manager.py start: 启动 hermesclaw。
- manager.py stop: 停止 hermesclaw。
- manager.py status: 查看运行状态（pid 或端口任一成立即判定为 running）。
- manager.py restart: 先停后启。
- manager.py logs [n]: 查看日志尾部，默认 200 行。
- manager.py msg "text": 向管理员发送一条微信消息，自动添加前缀 [Route]。

重要行为

- manager.py 无参数时默认执行 restart。
- manager.py msg 的管理员 UID 解析顺序:
  1) agents.json 中 admin_ilink_uid
  2) 环境变量 ADMIN_ILINK_UID
- manager.py msg 发送参数解析顺序与主流程一致：
  1) 脚本目录 agents.json
  2) AGENTS_CONFIG_FILE
  3) AGENTS_CONFIG
  4) 回退环境变量

配置优先级

hermesclaw 读取 agent 配置的顺序：

1. 脚本目录下 agents.json
2. env.AGENTS_CONFIG_FILE（支持相对路径，基于脚本目录解析）
3. env.AGENTS_CONFIG（内联 JSON）
4. 旧变量回退 HERMES_PROXY_PORT / OPENCLAW_PROXY_PORT 等

iLink 关键配置（推荐放在 agents.json）：

- ilink_base_url
- ilink_token
- admin_ilink_uid（可选，用于 manager.py msg）

路径约定（当前实现）

- PID 默认: logs/hermesclaw.pid
- STATE 默认: logs/router_state.json
- hermesclaw 默认日志: logs/hermesclaw.log
- manager.py logs 读取文件: hermesclaw.log（技能根目录）

admin_ilink_uid 获取方法

- 推荐从 logs/router_state.json 的顶层 key 获取。
- 这些 key 通常就是完整 from_user_id。
- 让管理员先给机器人发一条消息，再读取该文件的 key 并写入 admin_ilink_uid。

常用操作示例

- 启动:
  - python3 manager.py start
- 注册守护（示例）:
  - hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "wechat-route/manager.py cron"
- 发送测试通知:
  - python3 manager.py msg "watchdog started"

最小可用模板（可直接复制）

将下列内容保存为脚本目录下的 agents.json，即可在不依赖 .env 的情况下启动。

```json
{
  "ilink_base_url": "https://ilinkai.weixin.qq.com",
  "ilink_token": "replace-with-your-real-token",
  "admin_ilink_uid": "replace-with-admin-from_user_id",
  "default_route": "hermes",
  "groups": {
    "all": {
      "members": ["hermes", "openclaw"],
      "aliases": ["both", "all", "@all"]
    }
  },
  "agents": [
    {
      "name": "hermes",
      "host": "127.0.0.1",
      "port": 19998,
      "tag": "[Hermes]",
      "enabled": true,
      "aliases": ["hermes", "h", "@hermes"]
    },
    {
      "name": "openclaw",
      "host": "0.0.0.0",
      "port": 19999,
      "tag": "[OpenClaw]",
      "enabled": true,
      "aliases": ["openclaw", "claw", "@claw"]
    }
  ]
}
```

说明

- admin_ilink_uid 可先留空；仅在执行 manager.py msg 时必需。
- 若需要获取 admin_ilink_uid，先让管理员发一条消息，再读取 logs/router_state.json 的顶层 key。
- 配置文件就绪后执行 python3 manager.py start 即可。

运维建议

- cron 或外部调度务必显式传入子命令 cron，不要依赖无参默认行为。

- 明确守护模式（重要）：提供 scripts/strict_watchdog.py（见 references/strict-watchdog.md），用于严格守护。该脚本必须遵循严格检查流程：先运行 manager.py status；仅当 status 返回非运行（非 0 exit / 输出包含 “not running” 或不包含 "running" 文本）时才执行 restart 并更新 ./restart_count.txt 与发送通知。推荐将该脚本作为 cron job 调度，以避免在服务已运行时意外触发重启并增长计数。以本次会话为例，manager.py status 返回格式 "running (pid 1493 )"，脚本的文本匹配应覆盖带 pid 的 running 格式。
  - 强制约定：不要在状态为 running 时运行 manager.py restart（避免误增 restart_count 或造成短时间多次重启）。
  - Cron 推荐调用：python3 manager.py cron 或 scripts/strict_watchdog.py（显式子命令），绝不可使用无参 manager.py 或其他会默认执行 restart 的调用。


- 修改 agents.json 后需重启生效（当前为启动时加载，不是热更新）。

新增条目（2026-05-05）:


- 在 skill 的 references/ 中新增 session-evidence-20260505.md，记录了本次 cron 检查的命令、输出与结论，作为审计证据和排查起点。请在发生自动重启或计数变更后，将同类事件的证据也追加到 references/ 目录。

- 如果你使用容器最小镜像，优先依赖 manager.py 的 pid+端口检查，不要假设存在 ss/netstat。

- 会话记录与补救（新增）：请参阅 references/session-automatic-restart.md，记录了一个因未遵循严格守护流程而在服务已运行时误触发 restart 的实例、命令与回滚建议。该文档包含建议的回滚命令与如何将 strict_watchdog.py 放到 cron 中的例子。
