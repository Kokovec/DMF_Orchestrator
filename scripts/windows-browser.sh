#!/usr/bin/env bash
# Open a workload's UI in Chrome on *Windows* (WSL2, default NAT networking),
# so video decoding runs on Windows (and its GPU) instead of inside WSL.
#
# In NAT mode Windows reaches WSL's own IP directly (TCP 443 for the UI and
# SSO, UDP for WebTransport), but that IP changes on every WSL restart and
# the Windows hosts file needs admin rights. So this launcher:
#   1. reads the current ingress IP,
#   2. imports the DMF root CA into the Windows *user* Root store (once;
#      Windows asks you to confirm),
#   3. starts Chrome with a separate profile and --host-resolver-rules that map
#      *.<facility domain> to that IP for this Chrome only.
#
# usage: scripts/windows-browser.sh [workload] [--dry-run]
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
export PATH="$ROOT/.tools/bin:$PATH" KUBECONFIG="$ROOT/.state/kubeconfig"
WORKLOAD="${1:-news-studio}"
DRY_RUN="${2:-}"

command -v powershell.exe >/dev/null || { echo "Not running under WSL (no powershell.exe)." >&2; exit 1; }

HOST=$(awk '/^ *hostname:/ {print $2; exit}' "$("$ROOT/scripts/workload-file.sh" "$WORKLOAD")")
DOMAIN="${HOST#*.}"
IP=$(kubectl -n kube-system get svc traefik -o jsonpath='{.status.loadBalancer.ingress[0].ip}' 2>/dev/null || true)
[ -n "$IP" ] || IP=$(ip -4 -o addr show eth0 | awk '{print $4}' | cut -d/ -f1)

CHROME=""
for c in "/mnt/c/Program Files/Google/Chrome/Application/chrome.exe" \
         "/mnt/c/Program Files (x86)/Google/Chrome/Application/chrome.exe"; do
  [ -f "$c" ] && CHROME="$c" && break
done
[ -n "$CHROME" ] || { echo "Google Chrome not found on Windows." >&2; exit 1; }

CA="$ROOT/.state/dmf-root-ca.crt"
[ -f "$CA" ] || { echo "Run 'make platform' first (no $CA)." >&2; exit 1; }
THUMB=$(openssl x509 -in "$CA" -noout -fingerprint -sha1 | cut -d= -f2 | tr -d ':')
LOCALAPPDATA_WIN=$(powershell.exe -NoProfile -Command '$env:LOCALAPPDATA' | tr -d '\r')
PROFILE_WIN="$LOCALAPPDATA_WIN\\DMF\\chrome-$DOMAIN"

echo "Workload    : $WORKLOAD -> https://$HOST/sso/login"
echo "Ingress IP  : $IP   (rule: MAP *.$DOMAIN $IP)"
echo "Chrome      : $CHROME"
echo "Profile     : $PROFILE_WIN"

if powershell.exe -NoProfile -Command "if (Get-ChildItem Cert:\\CurrentUser\\Root | Where-Object Thumbprint -eq '$THUMB') { exit 0 } else { exit 1 }" >/dev/null 2>&1; then
  echo "Root CA     : already trusted by Windows (user store)"
else
  echo "Root CA     : importing into your Windows user Root store; confirm the Windows security prompt"
  if [ -z "$DRY_RUN" ]; then
    mkdir -p /mnt/c/Users/Public/DMF
    cp "$CA" /mnt/c/Users/Public/DMF/dmf-root-ca.crt
    certutil.exe -user -addstore Root 'C:\Users\Public\DMF\dmf-root-ca.crt' | tr -d '\r' | tail -1
  fi
fi

if [ -n "$DRY_RUN" ]; then echo "(dry run: Chrome not started)"; exit 0; fi
# Close this DMF Chrome window before re-running after a WSL restart: an already
# running instance keeps the IP mapping it was started with.
# DMF_CHROME_DEBUG_PORT=9333 opens Chrome's DevTools port on Windows localhost
# (diagnostics, e.g. scripts/windows-audio-probe.mjs). Leave unset normally.
DEBUG_ARGS=()
[ -n "${DMF_CHROME_DEBUG_PORT:-}" ] && DEBUG_ARGS=(--remote-debugging-port="$DMF_CHROME_DEBUG_PORT") && echo "DevTools    : 127.0.0.1:$DMF_CHROME_DEBUG_PORT (Windows only)"
"$CHROME" --user-data-dir="$PROFILE_WIN" --no-first-run --no-default-browser-check "${DEBUG_ARGS[@]}" \
  --host-resolver-rules="MAP *.$DOMAIN $IP" "https://$HOST/sso/login" >/dev/null 2>&1 &
disown
echo "Chrome started on Windows. Sign in, wait for LIVE, then click once to enable audio."
