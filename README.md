# HermesClaw (wechat-route)

简短说明

HermesClaw 是一个在容器中把 Hermes Agent 与 OpenClaw 同时运行在同一微信账号的轻量代理。该仓库已整理为 Hermes skill（wechat-route），便于放入 `~/.hermes/skills/wechat-route` 直接启用并通过 `hermes cron` 做守护。

快速上手（容器/无 systemd 场景）

1) 将仓库放到 Hermes skills：
   - 推荐软链（快速回滚）：
     mkdir -p ~/.hermes/skills && ln -s /opt/data/wechat-route ~/.hermes/skills/wechat-route

2) 确认可执行并检查脚本：
   - chmod +x ~/.hermes/skills/wechat-route/manager.py
  - manager.py 提供命令：start | stop | status | restart | cron | logs | msg

3) 注册 hermes cron（示例）：
   - hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "wechat-route/manager.py cron"
   - 或使用绝对路径：hermes cron create --schedule "every 1m" --name "wechat-route-watchdog" --script "~/.hermes/skills/wechat-route/manager.py cron"

Note: Minimal container images may lack ss/netstat. The cron watcher uses PID file + port check by attempting a TCP connect. This skill now includes a small helper script scripts/check_port.py for reliable portable port checks; manager.py cron can call it if needed.

4) 立即测试：
  - ~/.hermes/skills/wechat-route/manager.py cron
  - 检查 logs/hermesclaw.pid 与 hermesclaw.log 确认是否已启动

重要说明

- 容器内通常没有 systemd：请使用上文的 hermes cron 或外部容器管理器来守护进程。不要依赖 systemd 单元。
- 路径与文件：PID 默认位于 logs/hermesclaw.pid；STATE/LOG 默认位于 logs/router_state.json 与 logs/hermesclaw.log（可由 STATE_FILE/LOG_FILE 覆盖）。
- 现已支持多 agent 配置：优先读取脚本目录下 agents.json，其次 AGENTS_CONFIG_FILE（JSON），再其次 AGENTS_CONFIG（内联 JSON），最后兼容旧变量 HERMES_PROXY_PORT / OPENCLAW_PROXY_PORT 与 HERMES_ENABLED / OPENCLAW_ENABLED。
- /both 语义：优先走配置 groups.all；若未配置则自动广播到全部启用 agent。
- 若要把改动推回远端：容器内通常没有 Git 凭证，请使用已生成的 bundle（/opt/data/wechat-route/feature-container-hermes-side.bundle）在有凭证的宿主机上完成推送。

agents.json 配置说明

推荐在 .env 中设置：
- AGENTS_CONFIG_FILE=agents.json

完整示例（与当前实现一致）

```json
{
  "ilink_base_url": "https://ilinkai.weixin.qq.com",
  "ilink_token": "replace-with-real-token",
  "admin_ilink_uid": "o9xxxxxxxxxxxxxxxxxxxx",
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
      "tag": "[Hermes Agent]",
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

字段含义

- ilink_base_url：iLink 服务地址。
- ilink_token：iLink 令牌。
- admin_ilink_uid：管理通知接收者 UID（manager.py msg 使用）。

- default_route：用户首次对话或未设置路由时的默认目标 agent 名。
- groups：路由组定义。
  - groups.<group>.members：组内 agent 名列表。
  - groups.<group>.aliases：该组的别名（用于 @ 提及解析），例如 all 组可配置 both/all/@all。
- agents：agent 列表。
  - name：唯一标识（必须唯一）。
  - host：本地代理监听地址。
  - port：本地代理监听端口（启用的 agent 之间不能冲突）。
  - enabled：是否启用。
  - aliases：该 agent 的 @ 提及别名列表。
  - tag：消息前缀标签。

  admin_ilink_uid 如何获取

  - 推荐从路由状态文件读取：logs/router_state.json。
  - 该文件的顶层 key 通常就是完整 from_user_id（即可直接作为 admin_ilink_uid）。
  - 先让目标用户给机器人发一条消息，再查看 logs/router_state.json 的 key。

tag 字段作用（重点）

- tag 只在“多目标路由”场景下加到文本前面（例如用户当前路由为 /both 或 all）。
- 典型用途：在同一会话里区分是哪一路 agent 返回的内容。
- 若希望不加前缀，可把 tag 设为空字符串；若需要明显区分，建议使用短标签如 [Hermes]、[OpenClaw]。

兼容性说明

- 仍兼容旧的顶层 mention_aliases 配置，但新配置建议使用 agents[].aliases 与 groups.*.aliases。
- 若 groups 未显式提供 all，系统会自动把所有启用 agent 组成 all 组。

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