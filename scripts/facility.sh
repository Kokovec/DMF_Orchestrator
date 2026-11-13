#!/usr/bin/env bash
# Start or stop the whole local facility on demand. On WSL it does not start
# with the host, so WSL stays free for other work until you ask for it.
#
# usage: scripts/facility.sh start|stop [workload]
# start with no workload: the only deployed workload. With none deployed, start brings up the
# facility and waits for sign-in; with several, it brings up the facility and lists them.
# On WSL, start also opens the UI in Chrome on Windows, with its DevTools port
# (Windows localhost only) for diagnostics. NO_BROWSER=1 skips this;
# DMF_CHROME_DEBUG_PORT changes the port (default 9333).
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools/bin:$PATH" KUBECONFIG="$ROOT/.state/kubeconfig"
ACTION="${1:?usage: $0 start|stop [workload]}"
WORKLOAD="${2:-}"
NS="wl-$WORKLOAD"
SERVICE=k3s
systemctl cat k3s >/dev/null 2>&1 || SERVICE=k3s-agent

# Sign-in goes through Keycloak and the workload's SSO gateway, which start on their own after a
# cold start. Until both answer, the first login gets Traefik's "no available server", then a 500
# on the callback. So wait for them through the ingress, as the browser will reach them: Keycloak's
# realm (terraform/stacks/platform/identity.tf) must serve its discovery document, and the
# gateway's /sso/login must redirect to that realm's login page, which must load.
# With no workload file, only Keycloak is waited for, on the base domain DMF_BASE_DOMAIN
# (default dmf.test, as in the Ansible inventory).
wait_for_sso() {
  local host="" domain ip kc ca step got login
  if [ -n "${1:-}" ]; then
    host=$(awk '/^ *hostname:/ {print $2; exit}' "$1")
    if [ -z "$host" ]; then
      echo "$1 has no access.hostname, so there's no sign-in to wait for."
      return 0
    fi
    domain="${host#*.}"
  else
    domain="${DMF_BASE_DOMAIN:-dmf.test}"
  fi
  kc="https://keycloak.$domain/realms/dmf"
  # The same address windows-browser.sh maps *.<domain> to.
  ip=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
  [ -n "$ip" ] || ip=$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)
  ca=(-k)
  [ -f "$ROOT/.state/dmf-root-ca.crt" ] && ca=(--cacert "$ROOT/.state/dmf-root-ca.crt")
  probe() {  # url -> "<status> <redirect url>"
    local h="${1#https://}"
    curl -s -o /dev/null -w '%{http_code} %{redirect_url}' --max-time 5 "${ca[@]}" \
      --resolve "${h%%/*}:443:$ip" "$1" 2>/dev/null || true
  }
  if [ -n "$host" ]; then
    echo "Waiting for sign-in: Keycloak and the $host SSO gateway..."
  else
    echo "Waiting for sign-in: Keycloak..."
  fi
  for _ in $(seq 1 100); do
    step="Keycloak ($kc)"
    got=$(probe "$kc/.well-known/openid-configuration")
    if [ "${got%% *}" = 200 ] && [ -z "$host" ]; then
      echo "Sign-in is ready."
      return 0
    fi
    if [ "${got%% *}" = 200 ]; then
      step="SSO gateway (https://$host/sso/login)"
      got=$(probe "https://$host/sso/login")
      login="${got#* }"
      if [ "${got%% *}" = 302 ] && [ "${login#"$kc/"}" != "$login" ]; then
        step="Keycloak login page"
        got=$(probe "$login")
        if [ "${got%% *}" = 200 ]; then
          echo "Sign-in is ready."
          return 0
        fi
      fi
    fi
    sleep 3
  done
  echo "Sign-in isn't ready after 5 minutes: $step answered '${got%% *}' through the ingress at $ip." >&2
  echo "Check: kubectl -n dmf-identity get pods${host:+; kubectl -n $NS get pods}" >&2
  if [ -n "$host" ]; then
    echo "The facility and $WORKLOAD are up; Chrome wasn't opened. When sign-in answers: make windows-browser WORKLOAD=$WORKLOAD" >&2
  fi
  return 1
}

case "$ACTION" in
start)
  # k3s pulls in the Flow Domain mounts it needs (drop-ins from the mxl_domain role).
  sudo systemctl start "$SERVICE"
  # Straight after a start the API server may not answer yet: only its own NotFound is an answer.
  for i in $(seq 1 60); do
    if [ -z "$WORKLOAD" ] && out=$(kubectl get namespaces -l dmf.ebu.ch/workload \
        -o jsonpath='{range .items[*]}{.metadata.labels.dmf\.ebu\.ch/workload}{" "}{end}' 2>&1); then
      read -r -a deployed <<<"$out"
      case "${#deployed[@]}" in
        0)
          echo "The facility is up. No workload is deployed."
          wait_for_sso "" || exit 1
          echo "Deploy one with: make deploy XML=<published XML url or file>"
          exit 0
          ;;
        1)
          WORKLOAD="${deployed[0]}"
          NS="wl-$WORKLOAD"
          echo "Starting $WORKLOAD, the only deployed workload."
          ;;
        *)
          echo "The facility is up. Deployed workloads: ${deployed[*]}"
          wait_for_sso "" || exit 1
          echo "Open one with: make start WORKLOAD=<one of those>"
          exit 0
          ;;
      esac
    fi
    if [ -n "$WORKLOAD" ] && out=$(kubectl get namespace "$NS" -o name 2>&1); then
      break
    fi
    case "$out" in
      *NotFound*)
        deployed=$(kubectl get namespaces -l dmf.ebu.ch/workload \
          -o jsonpath='{range .items[*]}{.metadata.labels.dmf\.ebu\.ch/workload}{" "}{end}' 2>/dev/null || true)
        echo "Workload $WORKLOAD is not deployed: namespace $NS doesn't exist." >&2
        echo "Deployed workloads: ${deployed:-none}" >&2
        echo "Use make start WORKLOAD=<one of those>, or deploy it with make provision WORKLOAD=$WORKLOAD." >&2
        echo "The facility itself is running; make stop stops it." >&2
        exit 1
        ;;
    esac
    if [ "$i" = 60 ]; then
      echo "The Kubernetes API did not answer within 2 minutes: $out" >&2
      echo "Check: journalctl -u $SERVICE -n 50" >&2
      exit 1
    fi
    sleep 2
  done
  echo "Waiting for $WORKLOAD (first start after a stop takes about a minute)..."
  # exec reaches the running container through the kubelet. Pod status can't be
  # trusted here: straight after a restart it still shows the old "Running".
  FILE=$("$ROOT/scripts/workload-file.sh" "$WORKLOAD")
  MIXER=$(awk '/^ *kind: *switchframe *(#.*)?$/ {print prev} {if ($1=="-" && $2=="name:") prev=$3}' "$FILE")
  # An SRT source only goes on air once its encoder connects, which may be much later, so for a
  # generated workload whose on-air source isn't MXL a reachable API is all there is to wait for.
  ON_AIR='"programSource":"[^"]'
  if grep -q '^ *configFile:' "$FILE" && ! grep -q '^ *programSource: *mxl:' "$FILE"; then
    ON_AIR='"programSource":'
  fi
  for _ in $(seq 1 60); do
    if kubectl -n "$NS" exec "deploy/$MIXER" -c switchframe -- sh -c \
        'curl -sf -H "Authorization: Bearer $SWITCHFRAME_API_TOKEN" http://127.0.0.1:8081/api/switch/state' 2>/dev/null \
        | grep -q "$ON_AIR"; then
      if [ "$ON_AIR" = '"programSource":' ]; then
        echo "$WORKLOAD is up. Its on-air source goes on air when its encoder connects."
      else
        echo "$WORKLOAD is on air."
      fi
      wait_for_sso "$FILE" || exit 1
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
