#!/bin/bash
set -euo pipefail

cd "$(dirname "$0")"
if [[ "${1:-}" != "--no-browser" ]]; then
  for desktop_app in "/Applications/谷歌羊毛管理器.app" "$HOME/Applications/谷歌羊毛管理器.app" "$PWD/dist/谷歌羊毛管理器.app"; do
    if [[ -d "$desktop_app" ]]; then
      exec /usr/bin/open "$desktop_app"
    fi
  done
  echo "Build or install 谷歌羊毛管理器.app first. See README.md."
  exit 1
fi
export PATH="/opt/homebrew/bin:/usr/local/bin:$HOME/.local/bin:$PATH"
product_python="${GOOGLE_MANAGER_PYTHON:-python3}"
product_url="http://127.0.0.1:18782"
product_log_dir="$HOME/Library/Logs/GooglePerksManager"

if ! "$product_python" -I -c 'import sys; sys.exit(sys.version_info < (3, 10))'; then
  echo "Google Perks Manager requires Python 3.10 or later."
  exit 1
fi

manager_running() {
  /usr/bin/curl --noproxy '*' --connect-timeout 1 --max-time 2 -fsS "$product_url/api/health" 2>/dev/null |
    "$product_python" -I -c 'import json,sys; sys.exit(json.load(sys.stdin).get("product_id") != "com.heige.google-manager")' 2>/dev/null
}

if ! manager_running; then
  umask 077
  mkdir -p "$product_log_dir"
  "$product_python" -I - "$product_log_dir/启动.log" <<'PYTHON'
import subprocess
import sys

with open(sys.argv[1], "ab") as log:
    subprocess.Popen(
        [sys.executable, "-B", "-m", "google_manager.server", "--port", "18782"],
        stdin=subprocess.DEVNULL,
        stdout=log,
        stderr=subprocess.STDOUT,
        start_new_session=True,
    )
PYTHON
  for attempt in {1..30}; do
    if manager_running; then
      break
    fi
    sleep 0.2
  done
fi

if ! manager_running; then
  echo "Google Perks Manager failed to start. See $product_log_dir/启动.log"
  exit 1
fi

echo "Google Perks Manager is running at $product_url"
