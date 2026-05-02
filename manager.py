#!/usr/bin/env python3
"""wechat-route manager: start/stop/status/cron/logs for hermesclaw within the skill dir.

Usage:
  manager.py start
  manager.py stop
  manager.py status
  manager.py cron    # 用于 hermes cron 的守护入口（检查并在必要时启动）
  manager.py logs [n]
    manager.py msg "text to send"
"""
import os
import sys
import time
import socket
import subprocess
import signal
from pathlib import Path

BASE = Path(__file__).parent
LOGS_DIR = BASE / "logs"
PIDFILE = LOGS_DIR / "hermesclaw.pid"
LOGFILE = BASE / "hermesclaw.log"
HERMES_FILE = BASE / "hermesclaw.py"


def _load_env_fallback():
    env_path = BASE / '.env'
    if not env_path.exists():
        return
    for raw in env_path.read_text(encoding='utf-8').splitlines():
        line = raw.strip()
        if not line or line.startswith('#') or '=' not in line:
            continue
        k, v = line.split('=', 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        if k and k not in os.environ:
            os.environ[k] = v


def _configured_ports():
    """Return enabled proxy ports from AGENTS config, fallback to legacy vars."""
    try:
        from hermesclaw import load_agents_config

        cfg = load_agents_config(env=os.environ, base_dir=BASE)
        ports = []
        for agent in cfg.get("agents", []):
            if not agent.get("enabled", True):
                continue
            ports.append(int(agent.get("port", 0)))
        ports = [p for p in ports if p > 0]
        if ports:
            return ports
    except Exception:
        pass

    return [
        int(os.getenv("HERMES_PROXY_PORT", "19998")),
        int(os.getenv("OPENCLAW_PROXY_PORT", "19999")),
    ]


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
    PIDFILE.parent.mkdir(parents=True, exist_ok=True)
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
    for p in _configured_ports():
        if is_port_open('127.0.0.1', p):
            return True
    return False


def start():
    if running_via_pid() or any_port_running():
        print("already running")
        return 0
    # 优先使用环境变量 PYTHON 指定的解释器；如果未设置，则尝试使用 .venv 中的 python；如果都不可用，则报错。
    py = os.getenv('PYTHON', 'python3')
    if not py:
        venv_py = BASE / '.venv' / 'bin' / 'python'
        if venv_py.exists():
            py = str(venv_py)
        else:
            print("python executable not found")
            return 1

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


def msg_send(text):
    _load_env_fallback()

    cfg = {}
    try:
        from hermesclaw import load_agents_config

        cfg = load_agents_config(env=os.environ, base_dir=BASE)
    except Exception:
        cfg = {}

    admin_uid = str(cfg.get('admin_ilink_uid', '')).strip() or os.getenv('ADMIN_ILINK_UID', '').strip()
    base_url = str(cfg.get('ilink_base_url', '')).strip() or os.getenv('ILINK_BASE_URL', '').strip()
    token = str(cfg.get('ilink_token', '')).strip() or os.getenv('ILINK_TOKEN', '').strip()

    if not admin_uid:
        print('ADMIN_ILINK_UID not set')
        return 2
    if not base_url or not token:
        print('ILINK_BASE_URL or ILINK_TOKEN missing')
        return 2

    body = (text or '').strip()
    if not body:
        print('no message text')
        return 2

    final_text = f"[Route] {body}"
    try:
        sys.path.insert(0, str(BASE))
        from hermesclaw import send_text_ilink

        send_text_ilink(base_url, token, admin_uid, final_text)
        print('sent')
        return 0
    except Exception as e:
        print('send failed:', e)
        return 1


if __name__ == '__main__':
    # Support optional flags placed before the subcommand so wrappers or cron
    # invocations can pass arguments like --no-save-session or
    # --no-session-persistence without making the script treat them as the
    # command. Collect leading --flags and expose them via variables for
    # potential future use.
    flags = []
    cmd = None
    cmd_index = -1
    for i, a in enumerate(sys.argv[1:], start=1):
        if a.startswith('-'):
            flags.append(a)
            continue
        # first non-flag arg is the command
        cmd = a
        cmd_index = i
        break
    # If no explicit command provided, default to 'restart'. This keeps the
    # previous behavior where invoking the script with no args runs restart.
    if not cmd:
        cmd = 'restart'
    # Recognize known wrapper flags for compatibility; set local booleans.
    NO_SAVE_SESSION = '--no-save-session' in flags
    NO_SESSION_PERSISTENCE = '--no-session-persistence' in flags
    if cmd == 'start' or cmd == 'cron':  # 允许 cron 命令也调用 start()，以便在 cron 模式下也能执行一次启动检查
        sys.exit(start())
    if cmd == 'stop':
        sys.exit(stop())
    if cmd == 'status':
        sys.exit(status())
    if cmd == 'restart':
        sys.exit(restart())
    if cmd == 'logs':
        # Accept numeric argument or --lines N / --lines=N / -n N for compatibility with various callers
        n = 200
        if len(sys.argv) > 2:
            arg = sys.argv[2]
            if arg.startswith('--lines='):
                try:
                    n = int(arg.split('=', 1)[1])
                except Exception:
                    pass
            elif arg in ('--lines', '-n') and len(sys.argv) > 3:
                try:
                    n = int(sys.argv[3])
                except Exception:
                    pass
            else:
                try:
                    n = int(arg)
                except Exception:
                    pass
        sys.exit(logs(n))
    if cmd == 'msg':
        msg_text = ' '.join(sys.argv[cmd_index + 1:]).strip() if cmd_index >= 0 else ''
        sys.exit(msg_send(msg_text))
    print('unknown command')
    sys.exit(2)
