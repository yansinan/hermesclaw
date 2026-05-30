"""proxy.py 本地集成测试：启动模拟后端 + proxy → 验证路径路由。"""
import json
import http.server
import os
import subprocess
import sys
import threading
import time
from http.client import HTTPConnection
from pathlib import Path


def main():
    test_dir = Path(__file__).parent.parent
    test_agents = test_dir / "test_agents.json"

    # Step 0: 创建测试用 agents.json
    test_agents.write_text(json.dumps({
        "agents": [
            {"name": "hermes", "host": "127.0.0.1", "port": 18998, "enabled": True},
            {"name": "browser.hermes", "host": "127.0.0.1", "port": 18997, "enabled": True},
            {"name": "helix", "host": "127.0.0.1", "port": 18996, "enabled": True},
            {"name": "openclaw", "host": "127.0.0.1", "port": 18999, "enabled": True},
        ]
    }))

    backends = {
        "hermes": 18998, "browser.hermes": 18997,
        "helix": 18996, "openclaw": 18999,
    }
    PROXY_PORT = 18995

    # Step 1: 启动所有模拟后端
    class MockHandler(http.server.BaseHTTPRequestHandler):
        backend_name = "?"

        def do_POST(self):
            resp = json.dumps({
                "ret": 0,
                "backend": self.backend_name,
                "path": self.path,
            }).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(resp)))
            self.end_headers()
            self.wfile.write(resp)

        def log_message(self, *a):
            pass

    servers = []
    for name, port in backends.items():
        def make_handler(n):
            class H(MockHandler):
                backend_name = n
            return H
        srv = http.server.HTTPServer(("127.0.0.1", port), make_handler(name))
        t = threading.Thread(target=srv.serve_forever, daemon=True)
        t.start()
        servers.append(srv)
    time.sleep(0.3)
    print(f"[OK] {len(servers)} mock backends ready")

    # Step 2: 启动 proxy
    proc = subprocess.Popen(
        [sys.executable, str(test_dir / "proxy.py")],
        env={**os.environ, "PROXY_PORT": str(PROXY_PORT),
             "AGENTS_FILE": str(test_agents)},
        stdout=subprocess.PIPE, stderr=subprocess.PIPE,
    )
    time.sleep(1)
    ret = proc.poll()
    if ret is not None:
        stdout, stderr = proc.communicate()
        print(f"[FAIL] Proxy exited with code {ret}")
        print("STDERR:", stderr.decode())
        return 1
    print(f"[OK] proxy on :{PROXY_PORT}")

    # Step 3: 测试路由
    tests = [
        ("hermes", "/hermes/ilink/bot/getupdates", "hermes"),
        ("helix", "/helix/ilink/bot/getupdates", "helix"),
        ("browser.hermes", "/browser.hermes/ilink/bot/getupdates", "browser.hermes"),
        ("openclaw", "/openclaw/ilink/bot/sendmessage", "openclaw"),
    ]
    all_ok = True
    for label, path, expected in tests:
        try:
            conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=5)
            conn.request("POST", path, body=b"{}", headers={"Content-Type": "application/json"})
            resp = conn.getresponse()
            data = json.loads(resp.read())
            ok = data.get("backend") == expected
            status = "✓" if ok else "✗"
            if not ok:
                all_ok = False
            print(f"  {status} {path} -> {data.get('backend','?')} ✓" if ok else
                  f"  {status} {path} -> {data.get('backend','?')} (expected {expected})")
            conn.close()
        except Exception as e:
            print(f"  ✗ {path} -> ERROR: {e}")
            all_ok = False

    # Step 4: 404 路径
    try:
        conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=5)
        conn.request("POST", "/nonexistent/ilink/bot/getupdates")
        assert conn.getresponse().status == 404
        print("  ✓ /nonexistent -> 404")
        conn.close()
    except Exception as e:
        print(f"  ✗ 404 test -> {e}")
        all_ok = False

    # Step 5: 不在 allowlist 的 endpoint
    try:
        conn = HTTPConnection("127.0.0.1", PROXY_PORT, timeout=5)
        conn.request("POST", "/hermes/ilink/bot/deleteuser")
        assert conn.getresponse().status == 404
        print("  ✓ /hermes/ilink/bot/deleteuser -> 404 (not in allowlist)")
        conn.close()
    except Exception as e:
        print(f"  ✗ allowlist test -> {e}")
        all_ok = False

    # 清理
    proc.terminate()
    for srv in servers:
        srv.shutdown()
    test_agents.unlink(missing_ok=True)

    print()
    print("=== ALL OK ===" if all_ok else "=== SOME FAILED ===")
    return 0 if all_ok else 1


if __name__ == "__main__":
    sys.exit(main())
