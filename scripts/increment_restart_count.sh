#!/bin/sh
# Atomic increment of /opt/data/wechat-route/restart_count.txt
# Usage: scripts/increment_restart_count.sh

COUNT_FILE="/opt/data/wechat-route/restart_count.txt"
LOCK_FILE="/opt/data/wechat-route/restart_count.lock"
TMP_FILE="${COUNT_FILE}.tmp"

mkdir -p "$(dirname "$COUNT_FILE")"

# Use flock if available; otherwise use a pidfile lock fallback.
if command -v flock >/dev/null 2>&1; then
  exec 9>"$LOCK_FILE"
  flock -n 9 || exit 2
  OLD=0
  if [ -f "$COUNT_FILE" ]; then
    OLD=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
  fi
  case "$OLD" in
    ''|*[!0-9]*) OLD=0 ;;
  esac
  NEW=$((OLD + 1))
  printf "%d\n" "$NEW" > "$TMP_FILE"
  mv "$TMP_FILE" "$COUNT_FILE"
  echo "$NEW"
  flock -u 9
  exit 0
else
  # fallback: very simple exclusive write with pidfile
  if [ -f "$LOCK_FILE" ]; then
    echo "lock exists" >&2
    exit 2
  fi
  printf "%s" "$$" > "$LOCK_FILE"
  OLD=0
  if [ -f "$COUNT_FILE" ]; then
    OLD=$(cat "$COUNT_FILE" 2>/dev/null || echo 0)
  fi
  case "$OLD" in
    ''|*[!0-9]*) OLD=0 ;;
  esac
  NEW=$((OLD + 1))
  printf "%d\n" "$NEW" > "$TMP_FILE"
  mv "$TMP_FILE" "$COUNT_FILE"
  echo "$NEW"
  rm -f "$LOCK_FILE"
  exit 0
fi
