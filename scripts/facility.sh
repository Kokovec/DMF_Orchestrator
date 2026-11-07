#!/usr/bin/env bash
# Start or stop the whole local facility on demand. On WSL it does not start
# with the host, so WSL stays free for other work until you ask for it.
#
# usage: scripts/facility.sh start|stop [workload]
# On WSL, start also opens the UI in Chrome on Windows, with its DevTools port
# (Windows localhost only) for diagnostics. NO_BROWSER=1 skips this;
# DMF_CHROME_DEBUG_PORT changes the port (default 9333).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools/bin:$PATH" KUBECONFIG="$ROOT/.state/kubeconfig"
ACTION="${1:?usage: $0 start|stop [workload]}"
WORKLOAD="${2:-news-studio}"
NS="wl-$WORKLOAD"
SERVICE=k3s
systemctl cat k3s >/dev/null 2>&1 || SERVICE=k3s-agent

case "$ACTION" in
start)
  # k3s pulls in the Flow Domain mounts it needs (drop-ins from the mxl_domain role).
  sudo systemctl start "$SERVICE"
  echo "Waiting for $WORKLOAD (first start after a stop takes about a minute)..."
  # exec reaches the running container through the kubelet. Pod status can't be
  # trusted here: straight after a restart it still shows the old "Running".
  MIXER=$(awk '/^ *kind: *switchframe *$/ {print prev} {if ($1=="-" && $2=="name:") prev=$3}' "$("$ROOT/scripts/workload-file.sh" "$WORKLOAD")")
  for _ in $(seq 1 60); do
    if kubectl -n "$NS" exec "deploy/$MIXER" -c switchframe -- sh -c \
        'curl -sf -H "Authorization: Bearer $SWITCHFRAME_API_TOKEN" http://127.0.0.1:8081/api/switch/state' 2>/dev/null \
        | grep -q '"programSource":"[^"]'; then
      echo "$WORKLOAD is on air."
      if [ -z "${NO_BROWSER:-}" ] && command -v powershell.exe >/dev/null; then
        DMF_CHROME_DEBUG_PORT="${DMF_CHROME_DEBUG_PORT:-9333}" "$ROOT/scripts/windows-browser.sh" "$WORKLOAD" \
          || echo "Could not open Chrome; the facility is up. Try: make windows-browser WORKLOAD=$WORKLOAD" >&2
      else
        echo "View it with: make windows-browser WORKLOAD=$WORKLOAD"
      fi
      exit 0
    fi
    sleep 5
  done
  echo "$WORKLOAD did not come on air within 5 minutes." >&2
  echo "Check: kubectl -n $NS get pods; journalctl -u $SERVICE -n 50" >&2
  echo "If the kubelet is stuck: make stop, then make start." >&2
  exit 1
  ;;
stop)
  # systemctl stop k3s alone leaves every pod running; k3s-killall.sh ends them.
  sudo /usr/local/bin/k3s-killall.sh >/dev/null
  sudo systemctl stop 'var-lib-dmf-mxl-*.mount'
  echo "Facility stopped: k3s, all pods and the Flow Domains (their memory is released)."
  ;;
*)
  echo "usage: $0 start|stop [workload]" >&2
  exit 2
  ;;
esac
