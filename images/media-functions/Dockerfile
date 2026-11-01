# syntax=docker/dockerfile:1.7@sha256:a57df69d0ea827fb7266491f2813635de6f17269be881f696fbfdf2d83dda33e
#
# DMF Media Function images, built from pinned upstream sources.
#
#   target "switchframe" : SwitchFrame vision mixer built with -tags "embed_ui cgo mxl"
#                          (the upstream Dockerfile has no MXL support).
#   target "mxl-tools"   : MXL SDK tools (mxl-gst-testsrc, mxl-gst-looping-filesrc, mxl-info),
#                          the mxl-syncgen A/V sync source,
#                          plus the mxl-flow-agent (NMOS IS-04 registration and flow metrics).
#
# Build (make images does this):
#   docker build --target switchframe -t dmf.local/switchframe:<tag> images/media-functions
#   docker build --target mxl-tools   -t dmf.local/mxl-tools:<tag>   images/media-functions

# Upstream sources come from forks, pinned to commits (tagged dmf-base-* in the
# forks), and base images are pinned by digest. Forks: github.com/dmf-mxl/mxl and
# github.com/thiagopnts/switchframe. Not pinned: apt packages and vcpkg's own tool
# checkout (the MXL manifest's builtin-baseline sets its package versions).
ARG BASE_IMAGE=ubuntu:24.04@sha256:fe81560990fae86dc4743202b2cb4d3df98d0bcf949173cecef15ce1127ef852
ARG MXL_REPO=https://github.com/Kokovec/mxl.git
ARG MXL_VERSION=v1.1.0
ARG MXL_REF=8f3f1096e804bfb822eb959cdf2fc23199a7d15d
ARG SWITCHFRAME_REPO=https://github.com/Kokovec/switchframe.git
ARG SWITCHFRAME_REF=792361540f148b9ec8a93ec7727919672bd2330d
ARG GO_VERSION=1.25.0

# ----------------------------------------------------------------------------
# MXL SDK (same recipe as SwitchFrame's ci-nightly.yml, pinned to a tag)
# ----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS mxl-sdk
ARG MXL_REPO
ARG MXL_REF
ENV DEBIAN_FRONTEND=noninteractive \
    VCPKG_ROOT=/opt/vcpkg \
    VCPKG_DISABLE_METRICS=1
RUN apt-get update && apt-get install -y --no-install-recommends \
        build-essential cmake ninja-build git curl zip unzip tar pkg-config \
        ca-certificates python3 flex bison autoconf automake libtool \
        librdmacm-dev libgstreamer1.0-dev libgstreamer-plugins-base1.0-dev libspdlog-dev \
    && rm -rf /var/lib/apt/lists/*
# vcpkg needs full history to resolve the manifest's builtin-baseline.
RUN git clone https://github.com/microsoft/vcpkg.git ${VCPKG_ROOT} \
    && ${VCPKG_ROOT}/bootstrap-vcpkg.sh -disableMetrics
RUN git clone ${MXL_REPO} /src/mxl \
    && git -C /src/mxl checkout --detach ${MXL_REF}
WORKDIR /src/mxl
RUN cmake --preset Linux-GCC-Release \
        -DCMAKE_INSTALL_PREFIX=/opt/mxl \
        -DCMAKE_TOOLCHAIN_FILE=${VCPKG_ROOT}/scripts/buildsystems/vcpkg.cmake \
        -DBUILD_TESTS=OFF -DBUILD_DOCS=OFF \
    && cmake --build build/Linux-GCC-Release \
    && cmake --install build/Linux-GCC-Release
ENV LD_LIBRARY_PATH=/opt/mxl/lib

# ----------------------------------------------------------------------------
# SwitchFrame UI (SvelteKit), embedded into the Go binary
# ----------------------------------------------------------------------------
FROM node:22-bookworm-slim@sha256:4b150b48b4116e2a22f2c39cc27025fbf4f153e2078fd3b14d159fdb5c7279da AS ui
ARG SWITCHFRAME_REPO
ARG SWITCHFRAME_REF
RUN apt-get update && apt-get install -y --no-install-recommends git ca-certificates \
    && rm -rf /var/lib/apt/lists/*
RUN git clone ${SWITCHFRAME_REPO} /src/switchframe \
    && git -C /src/switchframe checkout --detach ${SWITCHFRAME_REF}
WORKDIR /src/switchframe/ui
# The browser audio player has no real jitter buffer, so audio crackles
# (see the script header for measurements).
COPY patches/ui-audio-jitterbuffer.mjs /patches/
RUN node /patches/ui-audio-jitterbuffer.mjs /src/switchframe/ui/src/lib/prism
# The player's A/V clock subtracted audio it had not played yet, so the offset
# between picture and sound changed on every page load (see the script header).
COPY patches/ui-av-clock.mjs /patches/
RUN node /patches/ui-av-clock.mjs /src/switchframe/ui/src/lib/prism
RUN npm ci && npx vitest run src/lib/prism/audio-decoder.test.ts src/lib/prism/renderer.test.ts && npm run build

# ----------------------------------------------------------------------------
# SwitchFrame server with MXL
# ----------------------------------------------------------------------------
FROM mxl-sdk AS switchframe-build
ARG GO_VERSION
RUN apt-get update && apt-get install -y --no-install-recommends \
        libavcodec-dev libavutil-dev libavformat-dev libswscale-dev libswresample-dev \
        libx264-dev libfdk-aac-dev libopus-dev libopusfile-dev \
    && rm -rf /var/lib/apt/lists/*
RUN curl -fsSL "https://go.dev/dl/go${GO_VERSION}.linux-amd64.tar.gz" | tar -C /usr/local -xz
ENV PATH=/usr/local/go/bin:${PATH}
COPY --from=ui /src/switchframe /src/switchframe
# Upstream fix needed for -tags mxl at the pinned commit (see the script header).
COPY patches/ /patches/
RUN sh /patches/mxl-split-10bit-asm.sh /src/switchframe/server
# MXL inputs reopen their flows after the writing Media Function restarts.
RUN sh /patches/mxl-reader-reopen.sh /src/switchframe/server
# The MXL programme writer writes each grain once, phase-aligned to frame
# arrivals, instead of from a free-running ticker (drops/repeats frames).
RUN python3 /patches/mxl-writer-phase.py /src/switchframe/server
# Fixed, short mixer input latency (narrow drift band, fast resync when far
# above target) instead of anywhere in 1-3 frames plus minutes-long drains.
RUN python3 /patches/mixer-audio-latency.py /src/switchframe/server
# Programme video timestamps jump to wall clock after time on a built-in source,
# instead of leaving picture and sound apart for twice that long.
RUN python3 /patches/programme-pts-resync.py /src/switchframe/server
RUN rm -f /src/switchframe/server/cmd/switchframe/ui \
    && cp -r /src/switchframe/ui/build /src/switchframe/server/cmd/switchframe/ui
WORKDIR /src/switchframe/server
RUN go mod download \
    && go test -count=1 ./mxl/ ./audio/ \
    && go test -count=1 -run "WallClock" ./switcher/
# Prism (MoQ distribution) rounds audio timestamps to 11.1 ms, which makes
# browser audio crackle; build against a patched copy (see the script header).
RUN PRISM_DIR=$(go list -m -f '{{.Dir}}' github.com/zsiec/prism) \
    && cp -r "$PRISM_DIR" /src/prism && chmod -R u+w /src/prism \
    && sh /patches/prism-audio-pts.sh /src/prism \
    && go mod edit -replace github.com/zsiec/prism=/src/prism
RUN PKG_CONFIG_PATH=/opt/mxl/lib/pkgconfig \
    CGO_LDFLAGS="-L/opt/mxl/lib -Wl,-rpath-link,/opt/mxl/lib -lspdlog" \
    go build -trimpath -tags "embed_ui cgo mxl" -o /out/bin/switchframe ./cmd/switchframe
# Collect every non-glibc shared library the binaries need, so the runtime
# stages don't depend on guessing Ubuntu runtime package names. GStreamer
# libraries are excluded: libgstreamer locates its plugins relative to its own path, so
# it must be the apt-installed copy (mxl-tools installs it with the plugins).
RUN mkdir -p /out/lib \
    && cp -a /opt/mxl/lib/*.so* /out/lib/ \
    && for b in /out/bin/* /opt/mxl/bin/*; do ldd "$b"; done \
       | awk '/=> \// {print $3}' | sort -u \
       | grep -Ev '/(libc|libm|libpthread|libdl|librt|libresolv|ld-linux[^/]*)\.so' \
       | grep -Ev '/libgst[^/]*\.so' \
       | xargs -r cp -Lnt /out/lib/

# ----------------------------------------------------------------------------
# mxl-syncgen: A/V sync test source (flash + beep locked to the MXL index)
# ----------------------------------------------------------------------------
FROM mxl-sdk AS mxl-syncgen
COPY mxl-syncgen/mxl-syncgen.c /src/mxl-syncgen/
RUN mkdir -p /out \
    && gcc -O2 -Wall -Werror -I/opt/mxl/include -o /out/mxl-syncgen /src/mxl-syncgen/mxl-syncgen.c -L/opt/mxl/lib -lmxl -lm

# ----------------------------------------------------------------------------
# Runtime: SwitchFrame Media Function
# ----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS switchframe
ARG SWITCHFRAME_REF
LABEL org.opencontainers.image.title="switchframe-mxl" \
      org.opencontainers.image.description="SwitchFrame vision mixer with MXL media exchange (EBU DMF Media Function)" \
      org.opencontainers.image.source="https://github.com/thiagopnts/switchframe" \
      org.opencontainers.image.revision="${SWITCHFRAME_REF}" \
      org.opencontainers.image.licenses="MIT"
RUN apt-get update && apt-get install -y --no-install-recommends ca-certificates curl \
    && rm -rf /var/lib/apt/lists/*
COPY --from=switchframe-build /out/lib/ /usr/local/lib/dmf/
RUN echo /usr/local/lib/dmf > /etc/ld.so.conf.d/dmf.conf && ldconfig
COPY --from=switchframe-build /out/bin/switchframe /usr/local/bin/switchframe
# The UID is overridden per workload (Flow Domain ownership); this is only the default.
USER 65532:65532
EXPOSE 8443/udp 8443/tcp 8081/tcp 9090/tcp 6464/udp
ENTRYPOINT ["/usr/local/bin/switchframe"]

# ----------------------------------------------------------------------------
# Runtime: MXL tools + flow agent
# ----------------------------------------------------------------------------
FROM ${BASE_IMAGE} AS mxl-tools
ARG MXL_VERSION
LABEL org.opencontainers.image.title="mxl-tools" \
      org.opencontainers.image.description="MXL SDK tools and DMF flow agent (NMOS IS-04 registration, flow monitoring)" \
      org.opencontainers.image.source="https://github.com/dmf-mxl/mxl" \
      org.opencontainers.image.version="${MXL_VERSION}" \
      org.opencontainers.image.licenses="Apache-2.0"
RUN apt-get update && apt-get install -y --no-install-recommends \
        ca-certificates python3 librdmacm1 fonts-dejavu-core \
        gstreamer1.0-plugins-base gstreamer1.0-plugins-good gstreamer1.0-x \
        gstreamer1.0-plugins-bad gstreamer1.0-libav \
    && rm -rf /var/lib/apt/lists/*
COPY --from=switchframe-build /out/lib/ /usr/local/lib/dmf/
RUN echo /usr/local/lib/dmf > /etc/ld.so.conf.d/dmf.conf && ldconfig
# Pods run with a read-only root filesystem; /tmp is an emptyDir.
ENV HOME=/tmp GST_REGISTRY=/tmp/gstreamer-registry.bin
COPY --from=mxl-sdk /opt/mxl/bin/ /usr/local/bin/
COPY --from=mxl-syncgen /out/mxl-syncgen /usr/local/bin/mxl-syncgen
# mxl-gst-looping-filesrc needs MXL's own GStreamer element (looping_filesrc)
# plus the TS demuxer and H.264/AAC decoders above.
COPY --from=mxl-sdk /opt/mxl/lib/gstreamer-1.0/ /usr/local/lib/dmf/gstreamer-1.0/
ENV GST_PLUGIN_PATH=/usr/local/lib/dmf/gstreamer-1.0
COPY mxl-flow-agent/agent.py /usr/local/bin/mxl-flow-agent
USER 65532:65532
ENTRYPOINT ["/usr/local/bin/mxl-gst-testsrc"]
