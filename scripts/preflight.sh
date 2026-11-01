#!/usr/bin/env bash
# Catch known environment problems before Terraform/Helm fail on them.
set -uo pipefail

# WSL2 DNS proxy bug: with IPv6 disabled and resolv.conf pointing at WSL's DNS
# proxy (10.255.255.254), Go programs (terraform, its helm provider) receive
# only AAAA records for dual-stack hosts and fail with
# "dial tcp [2600:...]:443: connect: cannot assign requested address".
# glibc tools (curl) are unaffected, so the network looks fine otherwise.
if grep -qi microsoft /proc/version 2>/dev/null \
   && [ "$(awk '/^nameserver/ {print $2; exit}' /etc/resolv.conf)" = "10.255.255.254" ] \
   && [ "$(cat /proc/sys/net/ipv6/conf/all/disable_ipv6 2>/dev/null)" = "1" ]; then
  cat >&2 <<'MSG'
preflight: WSL DNS proxy + disabled IPv6 detected.
  Terraform and its Helm provider will fail to reach registry.terraform.io and chart
  repositories ("connect: cannot assign requested address"). Fix DNS first, e.g.:

  Quick (until the next WSL restart):
    sudo sed -i '1i nameserver 1.1.1.1' /etc/resolv.conf

  Persistent:
    printf '[network]\ngenerateResolvConf = false\n' | sudo tee -a /etc/wsl.conf
    sudo rm /etc/resolv.conf && printf 'nameserver 1.1.1.1\nnameserver 8.8.8.8\n' | sudo tee /etc/resolv.conf
    (then from Windows: wsl --shutdown)

  Override this check with: make ... SKIP_PREFLIGHT=1
MSG
  exit 1
fi
exit 0
