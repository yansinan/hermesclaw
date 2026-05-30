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
import sys
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
    """读 agents.json，返回 {prefix: (host, port)} 映射。"""
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
        routes[prefix] = (host, port)
        log.info("  %s -> %s:%d", prefix, host, port)

    if not routes:
        log.error("No enabled agents found")
        sys.exit(1)
    return routes


def make_proxy_handler(routes):
    """Factory: 返回绑定路由表的 ProxyHandler 类。"""

    class PathRouteProxyHandler(BaseHTTPRequestHandler):
        # 避免 keep-alive 连接堆积
        close_connection = True

        def do_GET(self):
            self._proxy()

        def do_POST(self):
            self._proxy()

        def _proxy(self):
            path = urlparse(self.path).path

            # 按前缀长度降序匹配，优先匹配长前缀（如 browser.hermes > hermes）
            matched = None
            for prefix in sorted(routes.keys(), key=len, reverse=True):
                if path == prefix or path.startswith(prefix + "/"):
                    matched = prefix
                    break

            if not matched:
                self._write_json(404, {"ret": -1, "errmsg": "no route for this path"})
                return

            # 检查 endpoint 是否在白名单内
            rest = path[len(matched) + 1:]  # 去前缀和开头的 /
            if rest and rest not in PROXY_ALLOWLIST:
                self._write_json(404, {"ret": -1, "errmsg": "endpoint not allowed"})
                log.warning("Proxy blocked: %s (prefix=%s)", rest, matched)
                return

            host, port = routes[matched]
            self._forward(host, port, path[len(matched):] or "/")

        def _forward(self, host, port, target_path):
            """转发请求到后端，保留 query string。"""
            parsed = urlparse(self.path)
            qs = ("?" + parsed.query) if parsed.query else ""
            forward_path = target_path + qs

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
                log.error("Backend %s:%d refused connection", host, port)
                self._write_json(502, {"ret": -1, "errmsg": "backend refused"})
            except Exception as e:
                log.error("Proxy forward error: %s", e)
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
    routes = load_routes(agents_file or None)

    handler = make_proxy_handler(routes)
    server = ThreadingHTTPServer(("0.0.0.0", port), handler)

    log.info("=" * 50)
    log.info("wechat-route proxy listening on :%d", port)
    for prefix, (h, p) in sorted(routes.items()):
        log.info("  %s -> %s:%d", prefix, h, p)
    log.info("=" * 50)

    try:
        server.serve_forever()
    except KeyboardInterrupt:
        log.info("Shutting down...")
        server.shutdown()


if __name__ == "__main__":
    main()
