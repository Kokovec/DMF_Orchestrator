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
 Verticals            Orchestration: Terraform + Ansible, driven by make or the facility console
                      Control: SwitchFrame API/UI
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

## From design to running workload: the compiler

A workload is either hand-written (`workloads/<id>.yaml`, like `news-studio`) or designed in DMF Toolkit and compiled. The compiler, `tools/dmf_compile` (Python, stdlib plus `xmlschema` and PyYAML), is the only way in for a design:

```
GET /api/workloads/<id>/published.xml          (or a file)
   │  reader.py     refuse DOCTYPE, validate against schemas/dmf-workload-v1.xsd, XML → model
   │  facility.py   check every selected option against facility/local.yaml (capabilities, limits)
   │  products/     one module per product type (switchframe.py, dmf_mxl.py); unknown types refused
   │  allocate.py   UID/GID, QUIC and SRT ports, hostname, domain → .state/allocations.json
   │  sizing.py     MXL domain size, pod requests/limits, quota, volume sizes
   ▼  emit.py
workloads/generated/<id>.yaml          MediaWorkload v1alpha2: what Terraform and Ansible read
workloads/generated/<id>.config.json   the reconciler's desired state: API calls per source, destinations, recording
workloads/generated/<id>.source.xml    the XML as received, archived at finalise
```

Every problem is reported at once, each naming the function and option, and nothing is written when there is one. Rules include: a capability the facility lacks (ST 2110, MoQ, GPU, ASR, neural key, SCTE-104, SRT listener output); a format SwitchFrame doesn't have, or 720p with MXL, which SwitchFrame's V210 path can't carry; an MXL source whose format differs from the engine's; a missing secret; RTMP with an HEVC colour profile; a uid or flow ID another workload holds. The toolkit reads the same facility profile (`make facility-export`) to grey options out, so both sides apply one list. Flow IDs come from the toolkit (`uuidv5(workload uid, "<function>/<port>")`), so they stay the same across revisions; Terraform derives its own only for hand-written workloads.

`make plan` then checks what only the live facility knows: capacity (CPU by the sum of pod limits, memory, every Flow Domain), port and UID clashes, free disk for new volumes, and volume sizes against live PVCs, which `local-path` can't resize.

## Keeping settings applied: the reconciler

SwitchFrame forgets destinations and recording when it restarts, resets an SRT source's label and position on every connection, and has no API object for an SRT listener source until its encoder connects. A one-shot start hook can't cover that, so the SwitchFrame pod runs a sidecar, `sf-reconciler` (`images/media-functions/sf-reconciler/reconciler.py`, Python stdlib, in the `mxl-tools` image).

```
vision-mixer pod
  switchframe ◄──127.0.0.1:8081── reconciler ◄── /etc/switchframe/config/config.json  (ConfigMap, <id>.config.json)
                                     │       ◄── /etc/switchframe/secrets/            (Secret workload-refs)
                                     └── :9103/metrics  dmf_reconcile_pending|failed|api_up|errors_total
```

Every 5 s for two minutes after start, then every 30 s, it:

1. Waits for the API, and registers a short-lived `dmf-automation-<epoch>` director operator.
2. For each designed source that is present, applies its settings once per appearance: label, position, delay, trim, AFV, filters, EQ, compressor, key. An SRT source's appearance is a connection.
3. Applies workload-wide settings once: master limiter, replay shutter angle, captions, SCTE-35 default rule.
4. Creates and starts missing destinations once per SwitchFrame run, and recreates one only when its design or secret changed. An operator's deletion stands until the next restart.
5. Starts recording once per run, and restarts it only if it failed, not after an operator's clean stop.
6. Cuts to the design's on-air source when nothing is on air.
7. Removes SRT caller pulls the design used to have, and only those, tracked in a file on the state volume.

Calls are remembered by their content, pruned to the current config on every pass. When a new revision changes only the config, the ConfigMap is updated in place, nothing restarts, and the reconciler applies only the calls that changed, within about two minutes (the kubelet's ConfigMap sync); a value changed back to an earlier one is applied again. Unchanged settings aren't sent again, so an operator's live change to them stands. When SwitchFrame restarts, the reconciler notices and applies everything again.

Commands need an identified operator once any operator exists. If registration doesn't answer (a first start busy writing ST-map tables), SwitchFrame isn't ready: the reconciler sends nothing and tries again next loop, rather than fall back to the bare API token. If a call comes back 403 `operator not identified`, or 401 on the reconciler's own operator token, it registers again and retries the call once. It never exits on API errors, and it redacts passphrases, stream keys and the API token from its logs.

## Running the lifecycle from a page: the facility console

`tools/console/` (`make console`) is a small aiohttp server on `127.0.0.1` with a page that shows the facility and runs its lifecycle. `make` stays the only implementation: every button types its `make` command into one bash PTY that the page shows with xterm.js. That's also where sudo and `make secret-set` prompt, so the console never handles a password or a secret value.

- **Status** is polled every 5 s without sudo, each check with its own timeout: k3s, Kubernetes, the Keycloak probe through the ingress, the NMOS registry pod, `ansible/scripts/capacity.py` (the numbers `make plan` uses), the toolkit bind mount, the exported profile, the toolkit's API, and `.state/archive/`. A workload's deployed revision is its namespace's `dmf.ebu.ch/revision` label, which Terraform sets from the compiled header. On air comes from the reconciler's metrics.
- **Deploy** runs in the server as separate steps rather than `make deploy`, so the plan is reviewed first: `make import` (which writes `.state/last-import.json`), `make secret-set` for each missing secret, `make plan` (which writes `.state/<id>.plan-summary.json`, with restarts found by comparing `helm template` pod templates before and after), a review in the page, then `make provision`. Neither file holds secret values.
- **Access**: a random token per start, held in an `HttpOnly`, `SameSite=Strict` cookie, plus an `Origin` check on the WebSocket and every non-GET request. Terminal output is kept in memory only.

The design and its decisions are in [orchestration/05-facility-console.md](orchestration/05-facility-console.md).

## Naming and identities

| Item | Value |
|---|---|
| Namespace | `wl-<workload>` |
| Flow Domain | `/var/lib/dmf/mxl/<mediaExchange.domain>` |
| Workload uid/gid | `mediaExchange.uid` (must equal gid, ≥ 10000, unique per facility) |
| Flow ids | Compiled workloads: from the toolkit, `uuidv5(<workload uid>, "<function>/<port>")`, stable across revisions. Hand-written: `uuidv5(url, "dmf://<workload>/<function>/<video\|audio>")`, stable across re-provisioning |
| Keycloak client | `wl-<workload>`, redirect `https://<hostname>/oauth2/callback` |
| Keycloak groups | `/<workload>/{director,audio,graphics,captioner,viewer}` |
| Labels | `dmf.ebu.ch/workload`, `dmf.ebu.ch/flow-domain`, `dmf.ebu.ch/uid`, `dmf.ebu.ch/layer`, `dmf.ebu.ch/role` |

## Why some choices differ from upstream SwitchFrame docs

- **Kubernetes rather than systemd.** SwitchFrame's deployment guide recommends systemd or Nomad. The DMF RA's Container Platform layer calls for declarative container orchestration (Kubernetes is its example), and multi-workload isolation, quotas and admission policy come with it. SwitchFrame still runs as a single process; Kubernetes only supervises it.
- **MXL build.** The upstream Dockerfiles do not build with `-tags mxl`. `images/media-functions/Dockerfile` builds MXL SDK v1.1.0 the way upstream CI does (`ci-nightly.yml`) and links SwitchFrame against it. At the pinned commit, it also applies `patches/mxl-split-10bit-asm.sh`: cgo and Go assembly cannot share a package, so the 10-bit V210 kernels move into `mxl/v210asm`.
- **Initial programme connection.** SwitchFrame writes programme video to MXL only once a source is on programme. The reconciler therefore cuts to the design's on-air source (the first source of a hand-written workload), and only when programme is empty, so the programme flow never sits stalled after provisioning or a restart.
- **No `--admin-token-file`.** The upstream deployment guide shows this flag, but it doesn't exist in the code at the pinned commit. Tokens are passed as `SWITCHFRAME_*` environment variables from Kubernetes Secrets.
