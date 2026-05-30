#!/bin/sh
# wechat-route entrypoint: 同时启动路径路由代理和 iLink 轮询路由器
# proxy.py 在后台，router.py 在前台（exec 确保信号直达）

set -e

python3 proxy.py &
exec python3 router.py
