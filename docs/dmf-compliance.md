# DMF compliance matrix

How this package maps to the **EBU Dynamic Media Facility Reference Architecture, White Paper v2.0 (2026-04-15)**.

**What "compliant" means here.** The DMF RA is a reference architecture, not a conformance specification. There is no test suite or certification. This document therefore claims *alignment*: for each requirement or recommendation in the RA, it says what this package implements and where it falls short.

Status key: **Met**: implemented as the RA describes. **Partial**: implemented with a stated limitation. **Gap**: not implemented. **N/A**: does not apply to a single-site local facility.

## 1. Media Workload lifecycle (RA Fig. 1)

| Stage | RA expectation | Implementation | Status |
|---|---|---|---|
| Design | Templated workload description: production, functions, requirements | `workloads/<name>.yaml` (`kind: MediaWorkload`) is the single source of truth for Ansible and Terraform | Met |
| Plan | Calculate resources, reserve capacity, set up authorisations, test | `make plan`: schema checks, uid/domain uniqueness, capacity check against allocatable minus other reservations (`ansible/scripts/capacity.py`), `terraform plan` | Met |
| Provision | Make resources available; install and launch Media Functions | `make provision`: Flow Domain tmpfs on every host, namespace, ResourceQuota, Keycloak client and groups, Helm releases | Met |
| Configure | Configure and connect Media Functions; alerts | Initial MXL connections (`--mxl-sources`), then a gate that waits until every expected flow is live and NMOS-registered | Met |
| Operate | Authenticate users; operate through UIs within reserved resources | Keycloak SSO → SwitchFrame UI; quota enforces the reservation | Met |
| Finalise & Review | Delete functions, authorisations, reservations; record changes persistently | `make finalise`: archives logs, events, rendered resources and SwitchFrame state to `.state/archive/`, runs `terraform destroy`, unmounts the Flow Domain | Met |
| Monitor & Update | Continuous monitoring at every layer; CI/CD for updates | Prometheus, Alertmanager, Grafana, Loki; `make status`. No CI pipeline is shipped | Partial |

## 2. Horizontal layers

### Infrastructure

| RA item | Implementation | Status |
|---|---|---|
| Compute (CPU/GPU) | Local host or KVM VMs (`infrastructure-libvirt`, `host-passthrough` CPU for SIMD); optional NVIDIA role | Met |
| 100 GbE spine-leaf network | Not applicable to one box | N/A |
| NVMe storage | k3s `local-path` volumes | Met (local) |
| Timing reference (PTP/NTP/SyncE) | `timing` role: linuxptp with ST 2059-2 profile (`ptp4l` + `phc2sys`) when a NIC has a PHC; chrony otherwise. **WSL2**: the clock is disciplined by Windows and cannot be PTP-locked | Met (bare metal/VM), Partial (WSL2) |
| Configure with Ansible | All host configuration is Ansible roles | Met |
| Out-of-band management (iDRAC/IPMI) | Not covered | Gap |
| Monitoring incl. energy | node-exporter (incl. timex clock-sync collector); Kepler optional (`enable_energy_monitoring`). WSL2 and most VMs expose no power counters | Partial |
| Security: audited access, patched firmware, zero-trust segmentation | auditd rules, key-only SSH, unattended-upgrades. Firmware/BIOS patching is out of scope | Partial |

### Host Platform

| RA item | Implementation | Status |
|---|---|---|
| Clusters of Hosts, bare metal or type-1 hypervisor | Bare metal / WSL2, or KVM via libvirt | Met |
| Deploy OS with Terraform; configure with Ansible | `terraform/stacks/infrastructure-*` (cloud-init images) → `ansible/playbooks/site.yml` | Met |
| Kernel bypass / SR-IOV / RDMA | Not configured (single-host shared memory needs none) | Gap (N/A for a single host) |
| Monitoring with Prometheus/Grafana | node-exporter, kube-prometheus-stack | Met |
| Automated vulnerability patching | unattended-upgrades | Met |
| Minimal, AAA-controlled OS access | sudo via key-based SSH only; k3s API audit log | Met |
| Finalise: low-power / shutdown when idle | Not automated | Gap |

### Container Platform

| RA item | Implementation | Status |
|---|---|---|
| OCI containers, declarative orchestration (Kubernetes) | k3s, pinned `v1.36.5+k3s1` | Met |
| Container networking with per-workload isolation | One namespace per workload, default-deny NetworkPolicy, explicit allow rules (k3s kube-router enforcement) | Met |
| Plan: resources protected from oversubscription | ResourceQuota `dmf-reservation` = `resourceProfile` (requests), plus a capacity check in PLAN | Met |
| Specialist capabilities (shared memory, GPU) via extensions | hostPath Flow Domain guarded by a ValidatingAdmissionPolicy; NVIDIA device plugin and RuntimeClass (optional) | Met |
| Telemetry: metrics, logs, traces | Metrics (Prometheus) and logs (Loki + Alloy). No distributed tracing; SwitchFrame emits no traces | Partial |
| Security: authenticated orchestration | API server TLS 1.3 only, secrets encrypted at rest, audit policy | Met |
| Rolling updates | Media Functions use `Recreate`: one MXL writer per flow and fixed media ports | Partial (deliberate) |

### Media Exchange

| RA item | Implementation | Status |
|---|---|---|
| Open, shared-access exchange (MXL) | MXL SDK **v1.1.0** linked into SwitchFrame (`-tags mxl`) and the test sources | Met |
| Asynchronous shared memory within a host | One tmpfs per workload at `/var/lib/dmf/mxl/<domain>`, mounted into each function | Met |
| Exchange between hosts (RDMA/libfabric) | MXL fabrics not enabled; pod affinity keeps a workload's functions on one host | Gap |
| Default payloads: 1080p 10-bit YUV, float32 audio | V210 4:2:2 10-bit video, float32 48 kHz audio | Met |
| Time-related data as JSON/protobuf validated against a schema | SwitchFrame carries SCTE-104 on MXL data flows as binary. No JSON schema yet (the RA leaves this to future work) | Partial |
| Identity: uniquely identified Flows of timestamped Grains | Deterministic UUIDv5 flow ids per workload/function/essence; MXL grain indexes are wall-clock (TAI) based | Met |
| Flow Domains isolate workloads | Per-workload tmpfs: `uid:gid` ownership, mode `2770`, size = reservation. Admission policy allows only the namespace's own domain and only as the workload uid/gid | Met |
| Make flows discoverable (NMOS) | `mxl-flow-agent` registers Node, Device, Source and Flow with an AMWA IS-04 v1.3 registry (nmos-cpp) | Met |
| Monitoring: presence, throughput, latency of flows | `dmf_mxl_flow_active`, `dmf_mxl_flow_head_index` (throughput via `rate()`), `dmf_mxl_flow_latency_grains`; alerts `DMFFlowInactive` and `DMFFlowStalled` | Met |
| Dynamic make/break during operation | Through SwitchFrame's REST API and UI. There is no NMOS IS-05 path, because NMOS has no shared-memory transport (the RA notes this) | Partial |
| Media Exchange APIs encrypted and authorised (TLS 1.3 + OAuth 2.0, as in NMOS/IS-10) | The IS-04 registry is cluster-internal behind NetworkPolicy, but plain HTTP without IS-10 authorisation | Gap |

### Media Functions

| RA item | Implementation | Status |
|---|---|---|
| Well-defined units acting on media | Input functions (`mxl-gst-testsrc` per camera, `mxl-syncgen` as an A/V sync source) and a processing function (SwitchFrame vision mixer + audio mixer) | Met |
| Stateless | SwitchFrame keeps presets, macros, operators and SCTE-35 rules on disk. They live on a PVC and are archived at FINALISE | Partial |
| Fine-grained functions | SwitchFrame bundles vision mixing, audio, keying, graphics, replay and encode in one process | Partial |
| External interfaces: ST 2110 / SRT / etc. | SRT listener (UDP `srtPort`). SwitchFrame's ST 2110/DPDK path is not enabled (needs DPDK NICs) | Partial |
| Timestamp at ingress (JT-NM / MS-04 timing model) | MXL grains are indexed by wall-clock time; SwitchFrame derives a shared A/V epoch, not per-grain origin timestamps | Partial |
| Delivered through registries and Helm charts | OCI images + Helm charts (`charts/switchframe`, `charts/mxl-function`) | Met |
| Version pinning | SwitchFrame commit, MXL tag, Go, k3s, charts and images are all pinned | Met |
| Licence management | Open-source functions (MIT / Apache-2.0); no licence server | N/A |
| Control/monitoring via open APIs | SwitchFrame REST + Prometheus; NMOS IS-04 resource model for flows | Met |
| Control/monitoring traffic TLS 1.3, clients OAuth2/JWT-authorised | Browser edge: TLS 1.3 (Traefik TLSOption) + Keycloak OIDC with PKCE. QUIC/WebTransport is TLS 1.3. East-west hops (gateway → SwitchFrame, Prometheus scrapes, OIDC back-channel) are plain HTTP inside NetworkPolicy-restricted paths | Partial |

### Application & User Interface

| RA item | Implementation | Status |
|---|---|---|
| Browser UI over open protocols | SwitchFrame Svelte UI over HTTPS + WebTransport | Met |
| SSO / enterprise identity (EBU R 143) | Keycloak realm `dmf`; per-workload OIDC client; `sso-bridge` turns `/<workload>/<role>` groups into SwitchFrame operator roles | Met |
| Authorise and audit user access | oauth2-proxy `--allowed-group` per workload; bridge logs every login with role; SwitchFrame records the operator on every change; Keycloak events on | Met |
| Personalised UIs | Role-based views come from SwitchFrame's operator roles | Partial |
| Load-balanced UIs for resilience | Single replica | Gap |

## 3. Vertical concerns

| Vertical | Summary |
|---|---|
| Orchestration (IaC) | Terraform for Infrastructure, Container Platform services and workloads; Ansible for hosts and lifecycle sequencing. All declarative and re-runnable. |
| Control | SwitchFrame UI/API; MXL connections set at CONFIGURE; `make status`. |
| Monitoring | Per-layer alerts (`charts/dmf-platform-config/templates/alerts.yaml`) labelled `dmf_layer`, and the **DMF / Media Workloads** Grafana dashboard. |
| Security | Zero-trust segmentation (namespaces + default-deny), least privilege (non-root, read-only root FS, drop ALL, seccomp), AAA (Keycloak, oauth2-proxy, k8s audit, auditd), TLS 1.3 at every external edge. |

## 4. Known residual risks

1. **The WebTransport/QUIC port bypasses the SSO gateway.** No HTTP proxy can carry WebTransport today, so UDP `quicPort` goes straight to SwitchFrame. Operator commands still need a SwitchFrame bearer token, which only the SSO bridge issues. Whether every MoQ media subscription also checks a token depends on SwitchFrame/Prism internals that were not audited. Keep the QUIC port on a trusted production network.
2. **East-west traffic is unencrypted inside the cluster.** It is confined by NetworkPolicy. A service mesh with mTLS (Linkerd, or Istio ambient) is the upgrade path.
3. **The NMOS registry has no IS-10 authorisation.** Only `mxl-flow-agent` pods may reach it (NetworkPolicy).
4. **Keycloak uses its `dev-file` database.** That is fine for one local facility. Use PostgreSQL (and two replicas) for anything shared.
5. **Upstream SwitchFrame/MXL timing defects are patched at build time, not fixed upstream.** Three patches in `images/media-functions/patches/` cover: MXL readers that stopped for good when an input's writer restarted (`mxl-reader-reopen.sh`); the MXL programme writer's free-running ticker, which dropped and repeated frames (`mxl-writer-phase.py`; measured 19–22 of 30 one-frame flashes before, 30 of 30 after); and mixer audio latency that settled anywhere in 21–64 ms, started ~200 ms after start-up or a cut and drained at 1 ms/s (`mixer-audio-latency.py`; MXL programme A/V now +22 ms steady, was 66–195 ms). Until they are merged upstream, every SwitchFrame version bump must re-verify them; each script fails the build if the code it patches has changed.
6. **Browser monitoring A/V offset varies per page load.** SwitchFrame's browser player presented the sync source anywhere from 81 ms early to 404 ms late on successive reloads (stream timestamps were consistent). Under investigation; it affects monitoring only, not the MXL programme output.
7. **No image signing or SBOM.** Images build from pinned sources, but EBU R 160 vulnerability management would add scanning (e.g. Trivy) and signing (cosign).

## 5. Open topics the RA itself defers

These are out of scope here, as they are in RA v2.0: multi-cluster provisioning and monitoring, inter-cluster media exchange and timing, end-to-end synchronisation with synchronous (genlocked) systems, TAMS integration, and multi-vendor resource-management patterns.
