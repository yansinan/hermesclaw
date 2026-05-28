#!/usr/bin/env python3
"""
Increment restart_count.txt under /opt/data/wechat-route safely and send a notification via manager.py msg.
Usage: python3 scripts/increment_restart_and_notify.py --reason "short text"
"""
import argparse
from pathlib import Path
import subprocess

ROOT = Path('/opt/data/wechat-route')
COUNT_FILE = ROOT / 'restart_count.txt'
MANAGER = Path('/opt/data/skills/local/wechat-route/manager.py')


def read_count():
    try:
        return int(COUNT_FILE.read_text().strip())
    except Exception:
        return 0


def write_count(n):
    COUNT_FILE.parent.mkdir(parents=True, exist_ok=True)
    COUNT_FILE.write_text(str(n) + "\n")


def send_msg(text):
    cmd = ['python3', str(MANAGER), 'msg', text]
    subprocess.run(cmd, check=False)


def main():
    p = argparse.ArgumentParser()
    p.add_argument('--reason', '-r', default='automated restart')
    args = p.parse_args()
    n = read_count() + 1
    write_count(n)
    send_msg(f"wechat-route断开，已重启完成，这是第{n}次（{args.reason}）")


if __name__ == '__main__':
    main()
