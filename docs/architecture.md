# Architecture

How SwitchFrame is deployed onto the six DMF layers, and which tool owns each layer.

```
 DMF layer            What runs                                             Owned by
 ───────────────────  ────────────────────────────────────────────────────  ─────────────────────────────
 Application & UI     SwitchFrame UI (browser) ── Traefik (TLS 1.3)          charts/switchframe (gateway)
                        └─ oauth2-proxy ─ Keycloak (OIDC/PKCE)               terraform/stacks/platform
                        └─ sso-bridge: groups /<wl>/<role> → operator token
 Media Functions      cam1, cam2: mxl-gst-testsrc        (input)             charts/mxl-function
                      vision-mixer: SwitchFrame + MXL    (processing)        charts/switchframe
 Media Exchange       MXL Flow Domain = tmpfs /var/lib/dmf/mxl/<domain>      ansible/roles/mxl_domain
                        uid:gid owned, mode 2770, size = reservation
                      mxl-flow-agent → NMOS IS-04 registry; flow metrics     charts/mxl-function + platform
 Container Platform   k3s: namespace wl-<name>, ResourceQuota, default-deny  ansible/roles/k3s
                        NetworkPolicy, admission guardrails, cert-manager    terraform/stacks/{platform,workload}
 Host Platform        Ubuntu 24.04: hardening, sysctl, auditd, PTP/NTP       ansible/roles/{host_baseline,timing,mxl_host}
 Infrastructure       this machine (WSL2/bare metal) or KVM VMs (libvirt)    terraform/stacks/infrastructure-*
 ───────────────────  ────────────────────────────────────────────────────  ─────────────────────────────
 Verticals            Orchestration: Terraform + Ansible │ Control: SwitchFrame API/UI
                      Monitoring: Prometheus, Alertmanager, Grafana, Loki │ Security: Keycloak, PKI, policy
```

## Media path inside one workload

```
  cam1 (mxl-gst-testsrc) ──V210 + float32──┐
                                           ├─►  MXL Flow Domain (shared memory)  ─►  vision-mixer (SwitchFrame)
  cam2 (mxl-gst-testsrc) ──V210 + float32──┘          ▲                                │
                                                      └──── programme V210 + float32 ◄─┘
                                                                                       │
                       browsers ◄── WebTransport/QUIC (udp quicPort, 480p proxies) ───┤
                       SRT contribution ──► udp srtPort ──────────────────────────────┘
```

All media between functions goes through MXL shared memory, never the pod network. The network carries only control traffic, browser monitoring (compressed proxies) and SRT at the facility edge, as RA Fig. 13 describes.

## Control path and identity

```
browser ──https──► Traefik ──► oauth2-proxy ──(Keycloak OIDC: login, PKCE)
                                   │  /sso/login  ──► sso-bridge (127.0.0.1, same pod)
                                   │                   └─ POST /api/operator/register (role from groups)
                                   │                   └─ 302 → /#token=<operator token>
                                   └─ /*          ──► SwitchFrame :8081 (UI + REST, Bearer token)
browser ──QUIC (udp quicPort)────────────────────────► SwitchFrame (WebTransport, cert-hash pinned)
```

SwitchFrame's QUIC listener uses its own short-lived self-signed certificate, pinned by the browser via `/api/cert-hash`. This is deliberate. The Prism WebTransport server speaks draft-02, which Chrome only negotiates with pinned certificates, so a CA-issued certificate would break WebTransport (see `ui/src/lib/prism/transport-utils.ts` upstream).

## Naming and identities

| Item | Value |
|---|---|
| Namespace | `wl-<workload>` |
| Flow Domain | `/var/lib/dmf/mxl/<mediaExchange.domain>` |
| Workload uid/gid | `mediaExchange.uid` (must equal gid, ≥ 10000, unique per facility) |
| Flow ids | `uuidv5(url, "dmf://<workload>/<function>/<video\|audio>")`, stable across re-provisioning |
| Keycloak client | `wl-<workload>`, redirect `https://<hostname>/oauth2/callback` |
| Keycloak groups | `/<workload>/{director,audio,graphics,captioner,viewer}` |
| Labels | `dmf.ebu.ch/workload`, `dmf.ebu.ch/flow-domain`, `dmf.ebu.ch/uid`, `dmf.ebu.ch/layer`, `dmf.ebu.ch/role` |

## Why some choices differ from upstream SwitchFrame docs

- **Kubernetes rather than systemd.** SwitchFrame's deployment guide recommends systemd or Nomad. The DMF RA's Container Platform layer calls for declarative container orchestration (Kubernetes is its example), and multi-workload isolation, quotas and admission policy come with it. SwitchFrame still runs as a single process; Kubernetes only supervises it.
- **MXL build.** The upstream Dockerfiles do not build with `-tags mxl`. `images/media-functions/Dockerfile` builds MXL SDK v1.1.0 the way upstream CI does (`ci-nightly.yml`) and links SwitchFrame against it. At the pinned commit, it also applies `patches/mxl-split-10bit-asm.sh`: cgo and Go assembly cannot share a package, so the 10-bit V210 kernels move into `mxl/v210asm`.
- **Initial programme connection.** SwitchFrame writes programme video to MXL only once a source is on programme. PROVISION/CONFIGURE therefore cuts to the first configured source, and only when programme is empty, so the programme flow never sits stalled after provisioning.
- **No `--admin-token-file`.** The upstream deployment guide shows this flag, but it doesn't exist in the code at the pinned commit. Tokens are passed as `SWITCHFRAME_*` environment variables from Kubernetes Secrets.
