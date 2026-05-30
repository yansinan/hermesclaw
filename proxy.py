#!/usr/bin/env python3
"""proxy.py — 路径路由反向代理，统一 wechat-route 多 agent 端口。

读 agents.json 自动生成路由映射，按 Path 前缀分发到各 agent 后端端口。
不改 router.py 一行代码，纯 Python stdlib。

Agent 连接示例（以 hermes 为例，配 base_url=http://wechat-route:19990/hermes）：
  /hermes/ilink/bot/getupdates  →  localhost:19998/ilink/bot/getupdates
  /hermes/ilink/bot/sendmessage →  localhost:19998/ilink/bot/sendmessage

环境变量：
  PROXY_PORT=19990   监听端口（默认 19990）
  AGENTS_FILE=        agents.json 路径（默认脚本目录下的 agents.json）
"""

import json
import logging
import os
import secrets
import sys
import threading
import time
import urllib.request
from http.client import HTTPConnection
from http.server import ThreadingHTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from urllib.parse import urlparse

log = logging.getLogger("wechat-route-proxy")

# iLink 路径白名单（与 router.py PROXY_ALLOWLIST 保持一致）
PROXY_ALLOWLIST = frozenset([
    "ilink/bot/getupdates",
    "ilink/bot/sendmessage",
    "ilink/bot/getuploadurl",
    "ilink/bot/sendtyping",
    "ilink/bot/getconfig",
    "ilink/bot/get_bot_qrcode",
    "ilink/bot/get_qrcode_status",
])


def load_routes(agents_file=None):
    """读 agents.json，返回 (routes, meta)。

    routes: {prefix: {"host": str, "port": int, "tag": str, "name": str}}
    meta:   {ilink_base_url, ilink_token, admin_ilink_uid}
    """
    if agents_file is None:
        agents_file = Path(__file__).parent / "agents.json"
    fp = Path(agents_file)
    if not fp.exists():
        log.error("agents.json not found: %s", fp)
        sys.exit(1)

    raw = json.loads(fp.read_text(encoding="utf-8"))
    config = raw.get("agents", [])
    if not config:
        log.error("No agents in agents.json")
        sys.exit(1)

    routes = {}
    for agent in config:
        if not agent.get("enabled", True):
            log.info("  Skip %s (disabled)", agent.get("name", "?"))
            continue
        name = agent["name"]
        host = agent.get("host", "127.0.0.1")
        port = int(agent.get("port", 0))
        if port <= 0:
            log.warning("  Skip %s: invalid port", name)
            continue
        prefix = f"/{name}"
        routes[prefix] = {
            "host": host,
            "port": port,
            "tag": agent.get("tag", f"[{name}]"),
            "name": name,
        }
        log.info("  %s -> %s:%d", prefix, host, port)

    if not routes:
        log.error("No enabled agents found")
        sys.exit(1)

    meta = {
        "ilink_base_url": str(raw.get("ilink_base_url", "")).strip(),
        "ilink_token": str(raw.get("ilink_token", "")).strip(),
        "admin_ilink_uid": str(raw.get("admin_ilink_uid", "")).strip(),
    }
    return routes, meta


def _ilink_send_text(base_url, token, to_user, text, timeout=10):
    """直接通过 urllib 发送 iLink 文本消息（通知用）。"""
    if not base_url or not token or not to_user:
        return
    body = json.dumps({
        "msg": {
            "from_user_id": "",
            "to_user_id": to_user,
            "client_id": "hc-proxy-" + secrets.token_hex(8),
            "message_type": 2,
            "message_state": 2,
            "item_list": [{"type": 1, "text_item": {"text": text}}],
        },
        "base_info": {"channel_version": "2.1.7"},
    }).encode("utf-8")
    url = base_url.rstrip("/") + "/ilink/bot/sendmessage"
    headers = {
        "Content-Type": "application/json",
        "AuthorizationType": "ilink_bot_token",
        "Authorization": f"Bearer {token}",
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
        log.info("iLink notification sent to %s (HTTP %d)", to_user[:16], resp.getcode())
    except Exception as e:
        log.warning("iLink notification failed (to=%s): %s", to_user[:16], e)


class AgentTracker:
    """追踪 Agent 在线状态（通过代理前缀路由心跳），状态切换时推送 iLink 通知。"""

    def __init__(self, base_url, token, admin_uid, timeout=120, check_interval=30):
        self.base_url = base_url
        self.token = token
        self.admin_uid = admin_uid
        self.timeout = timeout
        self.check_interval = check_interval
        self._lock = threading.Lock()
        self._agents = {}
        log.info("AgentTracker: created (admin=%s, timeout=%ds)", (admin_uid or "none")[:16], timeout)

    def record_activity(self, name, tag=""):
        """记录 agent 心跳，状态切换时发通知。"""
        now = time.time()
        with self._lock:
            prev = self._agents.get(name, {})
            prev_state = prev.get("state", "unknown")
            since_last = now - prev.get("last_seen", 0) if prev else 0
            log.info("AgentTracker: heartbeat from %s (prev_state=%s, %.1fs since last)", name, prev_state, since_last)
            self._agents[name] = {"last_seen": now, "state": "online", "tag": tag or name}
            if prev_state == "offline":
                self._notify_locked(name, "重新上线", now)
            elif prev_state == "unknown":
                self._notify_locked(name, "上线", now)

    def check_timeouts(self):
        """检查超时 agent，标记 offline 并通知。"""
        now = time.time()
        with self._lock:
            log.info("AgentTracker: checking %d agents for timeouts...", len(self._agents))
            for name, info in list(self._agents.items()):
                if info["state"] != "online":
                    continue
                elapsed = now - info.get("last_seen", 0)
                if elapsed > self.timeout:
                    log.info("AgentTracker: %s timeout (%.0fs since last heartbeat)", name, elapsed)
                    info["state"] = "offline"
                    self._notify_locked(name, "掉线", now)

    def _notify_locked(self, name, event, ts):
        if not self.admin_uid or not self.token:
            return
        tag = self._agents.get(name, {}).get("tag", name) or name
        ts_str = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(ts))
        text = f"{tag} {event} at {ts_str}"
        log.info("AgentTracker notification: %s", text)
        log.info("AgentTracker: sending notification to admin_uid=%s via %s", self.admin_uid[:16], self.base_url)
        try:
            _ilink_send_text(self.base_url, self.token, self.admin_uid, text)
        except Exception as e:
            log.error("AgentTracker: send notification failed: %s", e)

    def start_monitor(self):
        """启动后台监控线程。"""
        def _loop():
            while True:
                time.sleep(self.check_interval)
                try:
                    self.check_timeouts()
                except Exception as e:
                    log.error("AgentTracker monitor: %s", e)
        t = threading.Thread(target=_loop, daemon=True)
        t.start()
        log.info("AgentTracker monitor started (timeout=%ds, check=%ds)", self.timeout, self.check_interval)


def make_proxy_handler(routes, tracker=None):
    """Factory: 返回绑定路由表的 ProxyHandler 类。

    *tracker* — 可选 AgentTracker，在 getupdates 时记录心跳。
    """

    class PathRouteProxyHandler(BaseHTTPRequestHandler):
        # 避免 keep-alive 连接堆积
        close_connection = True

        def do_GET(self):
            self._proxy()

        def do_POST(self):
            self._proxy()

        def _proxy(self):
            path = urlparse(self.path).path
            log.info("Request: %s %s", self.command, path)

            # 按前缀长度降序匹配，优先匹配长前缀（如 browser.hermes > hermes）
            matched = None
            for prefix in sorted(routes.keys(), key=len, reverse=True):
                if path == prefix or path.startswith(prefix + "/"):
                    matched = prefix
                    break

            if not matched:
                log.warning("No route for path: %s", path)
                self._write_json(404, {"ret": -1, "errmsg": "no route for this path"})
                return

            info = routes[matched]
            rest = path[len(matched) + 1:]  # 去前缀和开头的 /
            log.info("Matched prefix=%s agent=%s rest=%s", matched, info["name"], rest or "(root)")

            # 检查 endpoint 是否在白名单内
            if rest and rest not in PROXY_ALLOWLIST:
                log.warning("Endpoint blocked: %s (agent=%s)", rest, info["name"])
                self._write_json(404, {"ret": -1, "errmsg": "endpoint not allowed"})
                return

            # 记录 getupdates 心跳（agent 在线证明）
            if rest == "ilink/bot/getupdates" and tracker:
                tracker.record_activity(info["name"], info.get("tag", f"[{info['name']}]"))

            host = info["host"]
            # 0.0.0.0 是绑定地址，不能作为连接目标
            if host == "0.0.0.0":
                host = "127.0.0.1"
            port = info["port"]
            self._forward(host, port, path[len(matched):] or "/")

        def _forward(self, host, port, target_path):
            """转发请求到后端，保留 query string。"""
            parsed = urlparse(self.path)
            qs = ("?" + parsed.query) if parsed.query else ""
            forward_path = target_path + qs
            log.info("Forward: %s -> %s:%d%s", self.command, host, port, forward_path)

            length = int(self.headers.get("Content-Length", 0))
            body = self.rfile.read(length) if length else b""

            try:
                conn = HTTPConnection(host, port, timeout=60)
                conn.request(
                    self.command,
                    forward_path,
                    body=body,
                    headers={k: v for k, v in self.headers.items()
                             if k.lower() not in ("host", "connection",
                                                   "transfer-encoding",
                                                   "content-encoding")},
                )
                resp = conn.getresponse()
                log.info("Backend %s:%d responded %d", host, port, resp.status)
                # 流式写回
                self.send_response(resp.status)
                for k, v in resp.getheaders():
                    if k.lower() not in ("transfer-encoding", "content-encoding",
                                         "connection", "server", "date"):
                        self.send_header(k, v)
                self.end_headers()
                while chunk := resp.read(8192):
                    self.wfile.write(chunk)
                conn.close()
            except ConnectionRefusedError:
                log.error("Backend %s:%d refused connection (router gateway not ready?)", host, port)
                self._write_json(502, {"ret": -1, "errmsg": "backend refused"})
            except Exception as e:
                log.error("Proxy forward error to %s:%d: %s", host, port, e)
                self._write_json(502, {"ret": -1, "errmsg": str(e)})

        def _write_json(self, code, obj):
            data = json.dumps(obj).encode()
            self.send_response(code)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

        def log_message(self, format, *args):
            pass  # 屏蔽默认 HTTP 日志

    return PathRouteProxyHandler


def main():
    # 基础配置
    port = int(os.getenv("PROXY_PORT", "19990"))
    agents_file = os.getenv("AGENTS_FILE", "")

    log_level = getattr(logging, os.getenv("LOG_LEVEL", "INFO").upper(), logging.INFO)
    logging.basicConfig(
        level=log_level,
        format="%(asctime)s [%(levelname)s] %(message)s",
    )

    log.info("Loading routes from %s", agents_file or "agents.json (default)")
    routes, meta = load_routes(agents_file or None)

    log.info("iLink config: base_url=%s, admin_uid=%s",
             meta.get("ilink_base_url", "(none)"),
             (meta.get("admin_ilink_uid") or "(none)")[:16])
    log.info("iLink token: %s ...", (meta.get("ilink_token") or "(none)")[:16])

    # Agent 在线状态追踪（需要 agents.json 中有 admin_ilink_uid）
    tracker = None
    if meta.get("admin_ilink_uid") and meta.get("ilink_token"):
        tracker = AgentTracker(
            meta["ilink_base_url"],
            meta["ilink_token"],
            meta["admin_ilink_uid"],
        )
        tracker.start_monitor()
    else:
        log.info("AgentTracker disabled (no admin_ilink_uid or ilink_token in agents.json)")

    handler = make_proxy_handler(routes, tracker=tracker)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)

    log.info("=" * 50)
    log.info("wechat-route proxy listening on :%d", port)
    for prefix, info in sorted(routes.items()):
        log.info("  %s -> %s:%d  (%s)", prefix, info["host"], info["port"], info.get("tag", ""))
    log.info("=" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
