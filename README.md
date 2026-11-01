# SwitchFrame on a local EBU DMF facility

A deployment package that runs [SwitchFrame](https://github.com/thiagopnts/switchframe) as a Media Function in a local facility aligned with the **EBU Dynamic Media Facility Reference Architecture v2.0** ([white paper, 2026-04-15](https://tech.ebu.ch/publications/white-paper-2026-04-15)). It is provisioned with Terraform and configured with Ansible.

- **Media Exchange:** MXL SDK v1.1.0 shared memory, one isolated Flow Domain per production. Flows are registered in an NMOS IS-04 registry.
- **Media Functions:** containerised SwitchFrame vision mixer and MXL camera sources, packaged as Helm charts.
- **Container Platform:** k3s with per-production namespaces, resource reservations, default-deny network policy and admission guardrails.
- **Security:** Keycloak SSO mapped onto SwitchFrame operator roles, a local PKI, and TLS 1.3 at every external edge.
- **Monitoring:** Prometheus, Alertmanager, Grafana and Loki, with DMF alerts and a dashboard.
- **Lifecycle:** the RA's Design → Plan → Provision → Configure → Operate → Finalise cycle, as `make` targets.

See [docs/architecture.md](docs/architecture.md) for the layer mapping and [docs/dmf-compliance.md](docs/dmf-compliance.md) for an item-by-item compliance matrix, including what is only partially met.

## Set up from scratch

This builds the whole facility on one machine: the DMF environment first, then a production with SwitchFrame in it. You don't install SwitchFrame separately. It is compiled from a pinned upstream commit, with MXL support, when you build the images.

Two things are called a "domain" here:

- The **facility domain** is the DNS suffix of the web interfaces (`dmf.test`). Step 3 sets it.
- A **Flow Domain** is the shared memory one production uses to exchange media (MXL). Step 4 creates it, from `spec.mediaExchange` in the workload file.

### 1. Prepare the machine

You need:

| | Minimum | Recommended |
|---|---|---|
| OS | Ubuntu 24.04 with systemd, x86-64 (native or WSL2) | |
| CPU | 8 cores | 16 cores. At 8 vCPUs under load, Chrome inside WSL could not decode audio in time |
| Memory | 16 GB | 20 GB or more |
| Free disk | 100 GB | Images and build cache take about 75 GB, plus volumes |

You also need sudo, `git`, `make`, `curl`, `python3` and internet access. Docker is needed to build the images:

```bash
sudo apt-get update && sudo apt-get install -y git make curl python3
curl -fsSL https://get.docker.com | sudo sh     # or see docs.docker.com/engine/install/ubuntu
sudo usermod -aG docker "$USER"                 # then open a new shell
```

**On Windows with WSL2,** do this first:

1. In PowerShell, install Ubuntu if you don't have it: `wsl --install -d Ubuntu-24.04`.
2. Give WSL enough resources in `C:\Users\<you>\.wslconfig`:
   ```ini
   [wsl2]
   processors=16
   memory=20GB
   ```
3. Inside Ubuntu, make sure `/etc/wsl.conf` enables systemd:
   ```ini
   [boot]
   systemd=true
   ```
4. Apply both from PowerShell with `wsl --shutdown`, then reopen Ubuntu.
5. Install **Google Chrome on Windows**. It is the recommended way to view the UI.

Later steps run a check for a known WSL DNS problem. If it stops you, it prints the fix.

### 2. Get the code and tools

```bash
git clone https://github.com/Kokovec/switchframe-dmf.git
cd switchframe-dmf
make prereqs                      # terraform, kubectl, helm, ansible into ./.tools (no sudo)
```

Everything goes into `./.tools`; nothing is installed system-wide. Run all later commands from this directory. Commands marked `(sudo)` below ask for your sudo password.

### 3. Build the DMF environment

Run these once, in order. Each one is a layer of the DMF reference architecture, and it is safe to re-run if one fails.

```bash
make infra                        # Infrastructure: records this host in .state/inventory.yml
make hosts                        # Host + Container Platform: OS settings, timing, MXL, k3s   (sudo)
make images                       # Media Functions: compile SwitchFrame and MXL; load them    (sudo)
make platform                     # Shared services: PKI, monitoring, Keycloak, NMOS registry  (sudo)
```

The first `make images` compiles the MXL SDK and its dependencies and takes 30–60 minutes.

**To use a different facility domain,** set it before `make infra`, and use the same suffix for `spec.access.hostname` in your workload file:

```bash
TF_VAR_base_domain=studio.test make infra
```

Keep it a name you control or a reserved one such as `.test`. Ansible maps the hostnames to the ingress in `/etc/hosts`, so no DNS server is needed. Don't use `*.localhost`: browsers resolve those to `[::1]` first, and WebTransport does not fall back to `127.0.0.1` the way HTTPS does, so live video would silently never connect.

### 4. Create a production with SwitchFrame

A production is one file in `workloads/`. The included `news-studio` has two test cameras, an A/V sync test source and a SwitchFrame vision mixer.

```bash
make plan      WORKLOAD=news-studio   # check the design and capacity, show what will change
make provision WORKLOAD=news-studio   # create the Flow Domain and start the Media Functions (sudo)
make secrets                          # print the generated passwords
```

`make secrets` shows sensitive values, so keep its output private.

### 5. Open it

On WSL, close any earlier DMF Chrome window, then run:

```bash
make windows-browser WORKLOAD=news-studio
```

The first time, Windows asks you to confirm trusting the facility's root certificate. On other machines, or for a browser inside WSL, run `make trust-ca`, or see [Viewing it](#viewing-it-wsl2), then open **https://news-studio.dmf.test/sso/login**.

Sign in as `director1` with the password from `make secrets`.

### 6. Check that it works

1. The top bar shows **LIVE**, and Camera 1 is on programme.
2. Click once anywhere in the page to enable sound, with your volume **low**: the test cameras send loud tones.
3. Cut to **Sync test**: a white flash and a beep every second, together.
4. Run `make status WORKLOAD=news-studio` and `make nmos-query` to see the pods, reservation usage and registered flows.

Grafana is at https://grafana.dmf.test (dashboard **DMF / Media Workloads**).

`make up WORKLOAD=news-studio` runs steps 3 and 4 up to `provision` in one go.

### Day to day

```bash
make start WORKLOAD=news-studio    # start the facility, wait until on air, open Chrome
make stop                          # stop it and free its memory
make finalise WORKLOAD=news-studio # production over: archive to .state/archive/, tear down, free the Flow Domain
```

On WSL the facility does not start with WSL, so run `make start` after each WSL restart. After `make hosts` it is already running. `make start NO_BROWSER=1` skips opening Chrome.

### If something goes wrong

| Symptom | Cause and fix |
|---|---|
| `dial tcp [2600:...]: connect: cannot assign requested address` | WSL DNS with IPv6 disabled. Follow the fix `make` prints |
| UI shows **POLLING**, no video | WebTransport isn't connected. Check that UDP `quicPort` is reachable and that you used `make windows-browser` after any WSL IP change |
| Page says LIVE but sources show STALL | The tab outlived a SwitchFrame restart. Reload it |
| `make start` times out | Run `make stop`, then `make start`. If it persists: `journalctl -u k3s -n 50` |
| Scratchy audio | Usually too little CPU. See [Viewing it](#viewing-it-wsl2) |
| No sound | Click in the page once. BARS and BLACK on programme are silent |

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

### WSL2 notes

- **Browser on Windows (recommended):** `make windows-browser WORKLOAD=news-studio` opens the UI in Chrome **on Windows**, so video decoding uses Windows' CPU and GPU instead of WSL's. In NAT mode Windows reaches WSL's own IP directly (TCP 443 and UDP for WebTransport). The launcher maps `*.dmf.test` to the current WSL IP for that Chrome profile only (no hosts-file or admin change), and imports the DMF root CA into your Windows user certificate store once (Windows asks you to confirm). WSL's IP changes on every WSL restart, so close that Chrome window and re-run the target afterwards.
- **Timing.** WSL2's clock is disciplined by Windows. The timing role detects this, skips PTP/NTP and **disables Ubuntu's `systemd-timesyncd`**. Left running, it fights Hyper-V time sync and steps the clock by seconds every ~30 s, which shows up as periodic video freezes and audio dropouts. Use real hardware with a PTP-capable NIC for ST 2059 timing (`dmf_ptp_interface`).
- **Starting and stopping.** On WSL the facility does not start with WSL, so the machine stays free for other work. `make start WORKLOAD=…` starts k3s and the Flow Domains, waits until the workload is on air, then opens its UI in Chrome on Windows (`NO_BROWSER=1` skips this; the DevTools port stays on Windows localhost, for diagnostics); `make stop` ends everything and releases its memory. On each start the mixer waits for its input flows, restores source labels and puts the workload's first source on programme (the previous source is not remembered). Close the DMF Chrome window before `make start`: a running window keeps its old WSL IP and debug settings, and a tab left open across a SwitchFrame restart stays black until reloaded. Other hosts start with the OS; set `dmf_k3s_autostart` to change either default.
- **After rebuilding images,** `make images` then `make provision` restarts only the Media Functions whose image actually changed.
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

Three more patches fix timing defects found with the `sync` source (`mxl-syncsrc`, a flash and beep locked to the MXL index); each fails the build if upstream code changed and runs upstream's tests during the build:
- [`mxl-reader-reopen.sh`](images/media-functions/patches/mxl-reader-reopen.sh): MXL inputs reopen their flow when the writing Media Function restarts. Upstream readers stopped for good, leaving the source black until SwitchFrame was restarted.
- [`mxl-writer-phase.py`](images/media-functions/patches/mxl-writer-phase.py): the MXL programme writer writes each grain once, half a frame after frames typically arrive, instead of from a free-running ticker that dropped and repeated frames.
- [`mixer-audio-latency.py`](images/media-functions/patches/mixer-audio-latency.py): the audio mixer holds a fixed, short per-source buffer (narrow drift band; immediate resync after start-up, reconnects and cuts, where it used to drain at 1 ms/s for minutes). Programme audio is timestamped with the time it entered the mixer, in MXL and in the browser stream. MXL programme A/V: +22 ms steady, was 66–195 ms.
- [`programme-pts-resync.py`](images/media-functions/patches/programme-pts-resync.py): programme video timestamps jump to wall-clock time after time on a built-in source (BARS, BLACK). They used to freeze there and then catch up at 1.5x, leaving picture and sound apart for twice as long as the source was on air.
- [`ui-av-clock.mjs`](images/media-functions/patches/ui-av-clock.mjs): the browser player's audio clock no longer subtracts audio it has not played yet. Programme offset in Chrome: a steady 26–29 ms across page loads, was −371 to +422 ms.

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

## Licence

This package is released under the [MIT License](LICENSE). The images it builds download and compile third-party software that keeps its own licence, including [SwitchFrame](https://github.com/thiagopnts/switchframe) and [MXL](https://github.com/dmf-mxl/mxl). The scripts in `images/media-functions/patches/` modify that software at build time, and those changes apply under the licence of the code they modify.
