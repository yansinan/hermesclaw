#!/usr/bin/env python3
"""
strict_watchdog.py
- Run from cron. Only performs restart when manager.py status indicates service is NOT running.
- On successful restart, increments restart_count.txt atomically and sends a notification via manager.py msg.

Usage: python3 strict_watchdog.py
"""
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).parent
MANAGER = BASE / 'manager.py'
COUNT_FILE = BASE / 'restart_count.txt'


def run(cmd, timeout=60):
    try:
        r = subprocess.run(cmd, shell=True, check=False, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, timeout=timeout, text=True)
        return r.returncode, r.stdout
    except Exception as e:
        return 99, str(e)


def read_count():
    try:
        s = COUNT_FILE.read_text().strip()
        return int(s.splitlines()[0]) if s else 0
    except Exception:
        return 0


def write_count(n):
    # atomic replace
    tmp = COUNT_FILE.with_suffix('.tmp')
    tmp.write_text(str(n))
    tmp.replace(COUNT_FILE)


def main():
    code, out = run(f'python3 {MANAGER} status')
    if code == 0 and 'running' in out.lower():
        print('Status: running — no action')
        sys.exit(0)
    # Not running — attempt restart
    print('Status: not running — attempting restart')
    code, out = run(f'python3 {MANAGER} restart', timeout=120)
    if code != 0:
        print('Restart failed:', out)
        sys.exit(2)
    # verify
    code, out = run(f'python3 {MANAGER} status')
    if code == 0 and 'running' in out.lower():
        n = read_count() + 1
        write_count(n)
        msg = f'wechat-route断开，已重启完成，这是第{n}次'
        run(f'python3 {MANAGER} msg "{msg}"', timeout=30)
        print('Restart success, notified, count=', n)
        sys.exit(0)
    else:
        print('Restart did not result in running state:', out)
        sys.exit(3)


if __name__ == '__main__':
    main()
