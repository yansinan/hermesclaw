#!/usr/bin/env bash
# helpers/increment_restart_count.sh
# 安全地增加 /opt/data/skills/local/wechat-route/restart_count.txt 的计数，仅在被明确调用的重启路径中执行。
# 用法: helpers/increment_restart_count.sh <skill_dir>
set -euo pipefail
SKILL_DIR=${1:-/opt/data/skills/local/wechat-route}
COUNT_FILE="$SKILL_DIR/restart_count.txt"
mkdir -p "$(dirname "$COUNT_FILE")"
if [[ -f "$COUNT_FILE" ]]; then
  current=$(cat "$COUNT_FILE" | tr -d '[:space:]')
  if [[ -z "$current" ]]; then
    n=0
  else
    n=$(printf "%d" "$current")
  fi
else
  n=0
fi
n=$((n+1))
printf "%d" "$n" > "$COUNT_FILE"
echo "$n"
