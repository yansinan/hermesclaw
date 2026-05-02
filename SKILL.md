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
- manager.py cron: 用于 cron 守护入口（当前实现等价于执行 start）。
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
- 修改 agents.json 后需重启生效（当前为启动时加载，不是热更新）。
- 如果你使用容器最小镜像，优先依赖 manager.py 的 pid+端口检查，不要假设存在 ss/netstat。
