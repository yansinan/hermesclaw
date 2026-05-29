# wechat-route — 微信 iLink 消息路由代理

在 Docker 中运行，自动把微信消息按 agent 配置路由到对应后端代理端口。纯 Python stdlib，镜像 **56MB**。

## 快速开始

```bash
# 1. 配置 agents.json（ilink_token 必填）
vim agents.json

# 2. 启动（host 网络，直接暴露端口）
docker compose up -d

# 3. 查看日志
docker compose logs -f
```

容器异常退出会自动重启，最多重试 10 次后停止。

## agents.json 配置

```json
{
  "ilink_base_url": "https://ilinkai.weixin.qq.com",
  "ilink_token": "your-token-here",
  "default_route": "hermes",
  "agents": [
    {
      "name": "hermes",
      "host": "127.0.0.1",
      "port": 19998,
      "enabled": true,
      "aliases": ["hermes", "h"]
    },
    {
      "name": "openclaw",
      "host": "127.0.0.1",
      "port": 19999,
      "enabled": true,
      "aliases": ["openclaw", "claw"]
    }
  ]
}
```

| 字段 | 说明 |
|---|---|
| `ilink_token` | iLink 令牌 |
| `admin_ilink_uid` | 管理通知接收者 UID（从 `logs/router_state.json` 获取） |
| `default_route` | 默认目标 agent |
| `agents[].host` | 后端代理地址 |
| `agents[].port` | 后端代理端口 |
| `agents[].aliases` | @ 提及别名 |
| `agents[].tag` | 多目标路由时的消息前缀 |

admin_ilink_uid 获取：先让管理员发一条消息，查看 `logs/router_state.json` 的顶层 key。

## Docker 部署

- **端口**：host 网络模式，每个 agent 直接监听配置的端口
- **日志**：`logs/wechat-route.log`（挂载到宿主机 `./logs/`）
- **状态**：`logs/router_state.json`
- **重启**：`restart: on-failure:10`（崩溃自动重启，最多 10 次）

## 本地开发

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install pytest
pytest -q tests/
```

无微信环境测试：mock `send_text_ilink`，直接调用 `proc_msg(...)`。

## 报告安全问题

**aaronwong1999@icloud.com**