#!/usr/bin/env python3
"""wechat-route manager: start/stop/status/cron/logs for hermesclaw within the skill dir.

Usage:
  manager.py start
  manager.py stop
  manager.py status
  manager.py cron    # 用于 hermes cron 的守护入口（检查并在必要时启动）
  manager.py logs [n]
"""
import os
import sys
import time
import socket
import subprocess
import signal
from pathlib import Path

BASE = Path(__file__).parent
PIDFILE = BASE / "hermesclaw.pid"
LOGFILE = BASE / "hermesclaw.log"
HERMES_FILE = BASE / "hermesclaw.py"
# 支持覆盖
HERMES_PROXY_PORT = int(os.getenv("HERMES_PROXY_PORT", "19998"))
OPENCLAW_PROXY_PORT = int(os.getenv("OPENCLAW_PROXY_PORT", "19999"))


def is_port_open(host, port, timeout=0.5):
    s = socket.socket()
    s.settimeout(timeout)
    try:
        s.connect((host, port))
        s.close()
        return True
    except Exception:
        return False


def read_pid():
    try:
        return int(PIDFILE.read_text().strip())
    except Exception:
        return None


def write_pid(pid):
    tmp = PIDFILE.with_suffix('.pid.tmp')
    tmp.write_text(str(pid))
    tmp.replace(PIDFILE)


def remove_pid():
    try:
        PIDFILE.unlink()
    except Exception:
        pass


def running_via_pid():
    pid = read_pid()
    if not pid:
        return False
    try:
        os.kill(pid, 0)
        return True
    except Exception:
        try:
            remove_pid()
        except Exception:
            pass
        return False


def any_port_running():
    # 检查本地回环端口
    for p in (HERMES_PROXY_PORT, OPENCLAW_PROXY_PORT):
        if is_port_open('127.0.0.1', p):
            return True
    return False


def start():
    if running_via_pid() or any_port_running():
        print("already running")
        return 0
    py = None
    venv_py = BASE / '.venv' / 'bin' / 'python'
    if venv_py.exists():
        py = str(venv_py)
    else:
        py = os.getenv('PYTHON', 'python3')
    cmd = [py, str(BASE / 'hermesclaw.py')]
    print('starting:', ' '.join(cmd))
    out = open(LOGFILE, 'ab')
    env = os.environ.copy()
    # detach from current process group
    proc = subprocess.Popen(cmd, stdout=out, stderr=out, cwd=str(BASE), env=env, close_fds=True, start_new_session=True)
    write_pid(proc.pid)
    # 等待短时间确认端口
    for _ in range(6):
        time.sleep(0.5)
        if running_via_pid() or any_port_running():
            print('started', proc.pid)
            return 0
    print('start: failed to detect running instance after spawn')
    return 2


def stop():
    pid = read_pid()
    if not pid:
        print('not running')
        return 0
    try:
        os.kill(pid, signal.SIGTERM)
    except Exception:
        pass
    # wait
    for _ in range(10):
        time.sleep(0.3)
        try:
            os.kill(pid, 0)
        except Exception:
            remove_pid()
            print('stopped')
            return 0
    try:
        os.kill(pid, signal.SIGKILL)
    except Exception:
        pass
    remove_pid()
    print('killed')
    return 0


def status():
    if running_via_pid():
        print('running (pid', read_pid(), ')')
        return 0
    if any_port_running():
        print('running (detected open port)')
        return 0
    print('not running')
    return 1


def restart():
    # Stop then start, returning start()'s code.
    stop()
    return start()


def cron():
    """Named 'cron' entry used by hermes cron.

    Behavior:
    - Ensure a hermes cron job named 'wechat-route-watchdog' exists; if the
      hermes CLI is not available or the list command fails we skip creation but
      still perform the status check.
    - If the service is not running, start it.
    """
    # Ensure hermes cron job exists when possible
    cron_name = "wechat-route-watchdog"
    try:
        p = subprocess.run(["hermes", "cron", "list"], capture_output=True, text=True, timeout=5)
        if p.returncode == 0:
            out = p.stdout or p.stderr or ""
            if cron_name not in out:
                # create job using absolute path to this manager script
                script_path = str(BASE / "manager.py") + " cron"
                create_cmd = [
                    "hermes",
                    "cron",
                    "create",
                    "--schedule",
                    "every 1m",
                    "--name",
                    cron_name,
                    "--script",
                    script_path,
                ]
                try:
                    c = subprocess.run(create_cmd, capture_output=True, text=True, timeout=10)
                    if c.returncode == 0:
                        print("cron: created", cron_name)
                    else:
                        print("cron: failed to create job (hermes returned non-zero)")
                except Exception:
                    print("cron: failed to create job (exception)")
        else:
            # hermes CLI returned error; just continue to status check
            pass
    except Exception:
        # hermes CLI likely missing; skip cron creation
        pass

    # Now ensure service is running
    if running_via_pid() or any_port_running():
        print('ok')
        return 0
    return start()


def logs(n=200):
    try:
        data = LOGFILE.read_text(encoding='utf-8')
    except Exception:
        print('no log')
        return 1
    lines = data.splitlines()
    for l in lines[-int(n):]:
        print(l)
    return 0


if __name__ == '__main__':
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == 'start':
        sys.exit(start())
    if cmd == 'stop':
        sys.exit(stop())
    if cmd == 'status':
        sys.exit(status())
    if cmd == 'restart':
        sys.exit(restart())
    if cmd == 'cron':
        sys.exit(cron())
    if cmd == 'logs':
        n = int(sys.argv[2]) if len(sys.argv) > 2 else 200
        sys.exit(logs(n))
    print('unknown command')
    sys.exit(2)
