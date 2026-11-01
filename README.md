# SwitchFrame on a local EBU DMF facility

A deployment package that runs [SwitchFrame](https://github.com/thiagopnts/switchframe) as a Media Function in a local facility aligned with the **EBU Dynamic Media Facility Reference Architecture v2.0** ([white paper, 2026-04-15](https://tech.ebu.ch/publications/white-paper-2026-04-15)). It is provisioned with Terraform and configured with Ansible.

- **Media Exchange:** MXL SDK v1.1.0 shared memory, one isolated Flow Domain per production. Flows are registered in an NMOS IS-04 registry.
- **Media Functions:** containerised SwitchFrame vision mixer and MXL camera sources, packaged as Helm charts.
- **Container Platform:** k3s with per-production namespaces, resource reservations, default-deny network policy and admission guardrails.
- **Security:** Keycloak SSO mapped onto SwitchFrame operator roles, a local PKI, and TLS 1.3 at every external edge.
- **Monitoring:** Prometheus, Alertmanager, Grafana and Loki, with DMF alerts and a dashboard.
- **Lifecycle:** the RA's Design → Plan → Provision → Configure → Operate → Finalise cycle, as `make` targets.

See [docs/architecture.md](docs/architecture.md) for the layer mapping and [docs/dmf-compliance.md](docs/dmf-compliance.md) for an item-by-item compliance matrix, including what is only partially met.

## Quick start (this machine, e.g. WSL2 Ubuntu 24.04)

Requirements: Ubuntu 24.04 with systemd, Docker (for image builds), sudo, Python 3, 8 cores / 16 GB RAM, and about 30 GB of disk.

```bash
make prereqs                      # terraform, kubectl, helm, ansible → ./.tools (no sudo)
make infra                        # Infrastructure: records this host in .state/inventory.yml
make hosts                        # Host + Container Platform: hardening, timing, MXL root, k3s   (sudo)
make images                       # build + load Media Function images (first build: 30–60 min)
make platform                     # PKI, monitoring, Keycloak, NMOS registry, guardrails
make trust-ca                     # shows how to trust .state/dmf-root-ca.crt in your browser
make plan      WORKLOAD=news-studio
make provision WORKLOAD=news-studio
make secrets                      # demo operator passwords (realm "dmf")
```

Then open **https://news-studio.dmf.test/sso/login** and sign in as `director1`. Grafana is at https://grafana.dmf.test (dashboard **DMF / Media Workloads**).

Facility hostnames live under `dmf.test`, which is reserved and never resolves on the internet. Ansible maps them to the ingress in `/etc/hosts`. Don't switch to `*.localhost`: browsers resolve those to `[::1]` first, and WebTransport, unlike ordinary HTTPS, does not fall back to `127.0.0.1`, so live video would silently never connect.

### Viewing it (WSL2)

Preferred: `make windows-browser WORKLOAD=news-studio` (Chrome on Windows; see the WSL2 notes below).

Alternative, a browser **inside WSL** (WSLg shows its window on the Windows desktop). It works, but decodes all video on WSL's CPUs:

```bash
sudo apt-get install -y libnss3-tools libpulse0   # libpulse0: Chrome's sound path to Windows via WSLg
wget -qO /tmp/chrome.deb https://dl.google.com/linux/direct/google-chrome-stable_current_amd64.deb
sudo apt-get install -y /tmp/chrome.deb
mkdir -p ~/.pki/nssdb && { [ -f ~/.pki/nssdb/cert9.db ] || certutil -d sql:$HOME/.pki/nssdb -N --empty-password; }
certutil -d sql:$HOME/.pki/nssdb -A -t "C,," -n "DMF Local Root CA" -i .state/dmf-root-ca.crt
google-chrome https://news-studio.dmf.test/sso/login &
```

Click once anywhere in the UI to enable sound (browser autoplay policy). You hear the **programme** mix. Channels default to AFV (audio follows video), so BARS/BLACK on programme means silence. **PFL** (pre-fade listen) solos a source regardless of its fader or mute; that's what pre-fade means, so mute and fader changes are only audible on programme. The demo cameras send loud sine tones (100 Hz left, 200 Hz right, about −2 dBFS), so start with your volume low.

**Scratchy or broken-up audio means the browser can't decode audio in time**, usually because the machine is out of CPU. The UI decodes every source in software. Measured on an 8-vCPU WSL VM at load 14: Chrome's AAC decoders received 47 frames/s and produced about 30 frames/s, so playback starved about 40% of the time. Give WSL more processors (`processors=` in `.wslconfig`), keep only one SwitchFrame tab open, and keep the tab in the foreground (Chrome throttles background tabs).

The top bar shows **LIVE** when WebTransport is connected. **POLLING** means the UI fell back to REST: you get state and controls, but no video.

When the production is over:

```bash
make finalise WORKLOAD=news-studio   # archive to .state/archive/, tear down, free the Flow Domain
```

`make up WORKLOAD=news-studio` runs everything from `infra` through `provision`.

### WSL2 notes

- **Browser on Windows (recommended):** `make windows-browser WORKLOAD=news-studio` opens the UI in Chrome **on Windows**, so video decoding uses Windows' CPU and GPU instead of WSL's. In NAT mode Windows reaches WSL's own IP directly (TCP 443 and UDP for WebTransport). The launcher maps `*.dmf.test` to the current WSL IP for that Chrome profile only (no hosts-file or admin change), and imports the DMF root CA into your Windows user certificate store once (Windows asks you to confirm). WSL's IP changes on every WSL restart, so close that Chrome window and re-run the target afterwards.
- **Timing.** WSL2's clock is disciplined by Windows. The timing role detects this, skips PTP/NTP and **disables Ubuntu's `systemd-timesyncd`**. Left running, it fights Hyper-V time sync and steps the clock by seconds every ~30 s, which shows up as periodic video freezes and audio dropouts. Use real hardware with a PTP-capable NIC for ST 2059 timing (`dmf_ptp_interface`).
- **After a WSL/host restart,** Flow Domains (tmpfs) are gone, and WSL may regenerate `/etc/hosts`. Pods that start before their domain exists bind an empty, root-owned mount point and fail with "permission denied", rather than silently writing media to disk. Re-run `make provision WORKLOAD=…`: it re-mounts the domain and restarts that workload's Media Functions. It is idempotent and never cuts a live programme.
- **Memory.** 16 GB is tight for the platform plus one workload (SwitchFrame alone may use up to `goMemLimit`, 6 GiB). During testing, running a second copy of the stack alongside it pushed the WSL VM over the edge. If you are short, set `enable_logging = false` (Loki/Alloy) or raise the WSL memory limit in `.wslconfig`.
- k3s runs next to Docker without conflict. It uses its own containerd.

### Lab host with KVM VMs instead

```bash
make infra INFRA=libvirt          # Ubuntu 24.04 VMs via libvirt (edit nodes in terraform/stacks/infrastructure-libvirt)
make hosts images platform        # same as above; Ansible connects over SSH as user "dmf"
```

For PTP on real NICs, set `dmf_ptp_interface` per host (an inventory host var, or `ptp_interface` in the infrastructure stack).

## Productions are files

A production is one file in `workloads/`. Copy [workloads/news-studio.yaml](workloads/news-studio.yaml) and change:

| Field | Purpose |
|---|---|
| `metadata.name` | Workload name. Becomes namespace `wl-<name>`, Keycloak groups `/<name>/<role>`, and the Terraform workspace |
| `spec.resourceProfile` | CPU, memory and GPU reserved by ResourceQuota; checked at PLAN |
| `spec.mediaExchange` | Flow Domain name, **unique** uid/gid, and tmpfs size |
| `spec.access` | Hostname, QUIC and SRT ports (unique per facility), Keycloak members per role |
| `spec.functions` | `mxl-testsrc` (test pattern + tone) and `mxl-syncsrc` (A/V sync source: flash + beep every 1.001 s, 1080p29.97 only) inputs, and exactly one `switchframe`, with `sources` (initial connections), `format` and `extraArgs` |

Several workloads can run side by side. They are isolated by namespace, network policy, Flow Domain ownership and the admission policy.

## What's in the box

```
workloads/                   DESIGN: one MediaWorkload file per production
terraform/stacks/
  infrastructure-localhost   Infrastructure: existing host(s) → Ansible inventory
  infrastructure-libvirt     Infrastructure: KVM VMs (cloud-init) → Ansible inventory
  platform                   cert-manager + CA, kube-prometheus-stack, Loki/Alloy, Keycloak, NMOS registry, policy
  workload                   per-workload namespace, quota, secrets, Keycloak client/groups, Media Functions
ansible/
  roles/                     host_baseline, timing (PTP/NTP), mxl_host, mxl_domain, k3s, nvidia, control_node
  playbooks/                 site, images, platform, workload-{plan,provision,status,finalise}
charts/
  switchframe                SwitchFrame Media Function + SSO gateway (oauth2-proxy + sso-bridge)
  mxl-function               generic MXL function (camera test sources, mxl-flow-agent)
  dmf-platform-config        CA issuers, TLS 1.3 policy, Flow Domain admission policy, alerts, dashboard
images/
  media-functions            MXL SDK v1.1.0 + SwitchFrame (-tags "embed_ui cgo mxl"), MXL tools, mxl-flow-agent
  sso-bridge                 OIDC → SwitchFrame operator-token bridge
docs/                        architecture, DMF compliance matrix
```

### Upstream fix applied at build time

At the pinned commit, SwitchFrame **does not build with `-tags mxl`**: Go rejects a package that uses cgo and also contains Go assembly, and the 10-bit V210 kernels were added to the cgo `server/mxl` package. [`images/media-functions/patches/mxl-split-10bit-asm.sh`](images/media-functions/patches/mxl-split-10bit-asm.sh) moves them into the existing assembly-only `mxl/v210asm` package. Behaviour is unchanged: upstream's 10-bit V210 tests pass, and amd64 and arm64 both vet clean. This is worth sending upstream; the script exits early once it is no longer needed.

A second fix is applied to Prism, the MoQ distribution library SwitchFrame uses. [`patches/prism-audio-pts.sh`](images/media-functions/patches/prism-audio-pts.sh) corrects how it writes audio timestamps to browsers: as `uint32(PTS/1000)*1000`, where PTS is a 90 kHz clock. That rounds every audio timestamp to 11.1 ms, while an AAC frame lasts 21.33 ms. Video and the UI both treat these timestamps as 90 kHz ticks; the patch sends the audio PTS unchanged, like video. Prism's test suite passes with it (one test that asserted the old behaviour is updated).

A third fix targets SwitchFrame's browser audio player. [`patches/ui-audio-jitterbuffer.mjs`](images/media-functions/patches/ui-audio-jitterbuffer.mjs) changes how it buffers. As shipped, the player starts at 50 ms of buffered audio and resumes on the very next frame after running dry, so on any delivery hiccup it toggles between sound and silence every few milliseconds, which you hear as crackle (measured: about 2 underruns per second, 15% silence). The patch makes it (re)start only once 150 ms is buffered and widens its correction band (low/high/flush: 75/300/600 ms). It adds about 50 ms of monitoring latency.

### Add-ons written for this package

- **`mxl-flow-agent`** ([source](images/media-functions/mxl-flow-agent/agent.py)) registers every MXL flow in a domain with an NMOS IS-04 v1.3 registry and exports per-flow Prometheus metrics: active, throughput and latency. It uses only the Python standard library.
- **`sso-bridge`** ([source](images/sso-bridge/bridge.py)) gives SwitchFrame single sign-on without patching it. It reads oauth2-proxy's identity headers on loopback, maps Keycloak groups to SwitchFrame roles, mints a per-user operator token, and hands it to the UI through SwitchFrame's `#token=` fragment.
- **Flow Domain guardrails** ([policy](charts/dmf-platform-config/templates/flow-domain-policy.yaml)) are a CEL ValidatingAdmissionPolicy. It restores restricted-pod rules in workload namespaces and admits only the namespace's own MXL domain, as the workload's uid/gid.

## Operations

| Task | Command |
|---|---|
| Status across layers | `make status WORKLOAD=news-studio` |
| Flows in the NMOS registry | `make nmos-query` |
| Credentials | `make secrets` |
| Break-glass SwitchFrame token | `cd terraform/stacks/workload && terraform workspace select news-studio && terraform output -raw switchframe_api_token` |
| Static checks (no cluster needed) | `make lint` |
| Remove platform and infrastructure | `make finalise` each workload, then `make destroy` |

Pinned versions: SwitchFrame `792361540f14` (thiagopnts fork), MXL SDK `v1.1.0`, k3s `v1.36.5+k3s1`, cert-manager `v1.21.3`, kube-prometheus-stack `92.17.0`, Keycloak `26.8.1`, oauth2-proxy `v7.15.5`. Change them in `images/media-functions/Dockerfile`, `ansible/playbooks/group_vars/all.yml` and `terraform/stacks/platform/variables.tf`.
