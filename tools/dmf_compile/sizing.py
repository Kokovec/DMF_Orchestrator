"""Domain size, pod resources, quota and volumes (04-orchestrator-spec.md, Sizing)."""
import math
from fractions import Fraction

MIB = 2**20

# Fixed pod requests of everything but SwitchFrame: (cpu cores, memory MiB).
TEST_SOURCE_REQUESTS = (Fraction(1, 4), 256)
TEST_SOURCE_LIMITS = (1, 1024)
FLOW_AGENT = (Fraction(5, 100), 64)
SSO_GATEWAY = (Fraction(3, 100), 56)     # oauth2-proxy 20m/32Mi + sso-bridge 10m/24Mi
RECONCILER = (Fraction(2, 100), 32)

QUOTA_HEADROOM = Fraction(6, 5)          # +20%
# MXL domain, measured on studio-a (stage 4, df and du on the domain): each video flow holds 5
# grains (27.69 MB at 1080p: 5 x 5,529,600 B + 44 KB of headers) while historyMs isn't passed to
# MXL; each 2-channel audio flow 158 KB.
DOMAIN_FLOOR_MIB = 512
DOMAIN_SLACK_MIB = 256
DOMAIN_FACTOR = Fraction(5, 4)
MIN_VIDEO_GRAINS = 5
AUDIO_FLOW_BYTES = MIB
STATE_FLOOR_GIB = 5
CACHE_SLACK_GIB = 2
# SwitchFrame prewarms /cache/stmap at start (server/cmd/switchframe/app.go, prewarmSTMapCache):
# every built-in animated ST map effect (5 at the pinned commit), 90 frames each, as a 10 B/px
# int32 LUT plus an 8 B/px float32 S/T array. Measured at 1080p: 3,359,232,000 B per effect.
STMAP_EFFECTS = 5
STMAP_FRAMES = 90
STMAP_BYTES_PER_PIXEL = 18
CLIPS_DEFAULT_GB = 10                    # SwitchFrame's --clip-storage-max default, 10<<30 bytes
PLAYOUT_CACHE_DEFAULT_GB = 200           # SwitchFrame's --playout-cache-max default
REPLAY_RING_MIB = 200                    # replay.DefaultConfig MaxBufferBytes: one 200 MiB ring file per source
REPLAY_MAX_SOURCES = 8                   # replay.DefaultConfig MaxSources; AddSource refuses more
REPLAY_TMPFS_SLACK_MIB = 64


def v210_frame_bytes(width, height):
    return math.ceil(width / 6) * 16 * height


def video_bytes_per_second(width, height, rate: Fraction):
    return v210_frame_bytes(width, height) * rate


def video_grains(rate: Fraction, history_ms):
    """Grains a video flow holds: its history, but never fewer than the writers keep (5)."""
    return max(MIN_VIDEO_GRAINS, math.ceil(Fraction(history_ms, 1000) * rate))


def domain_size_mib(video_flows, audio_flows=0):
    """video_flows: iterable of (width, height, Fraction rate, historyMs); audio_flows: a count.
    sizeMiB = max(512, ceil(1.25 x (sum of grains x V210 frame bytes + 1 MiB per audio flow) / 2^20) + 256)"""
    total = sum((v210_frame_bytes(w, h) * video_grains(r, hist) for w, h, r, hist in video_flows), 0)
    total += AUDIO_FLOW_BYTES * audio_flows
    return max(DOMAIN_FLOOR_MIB, math.ceil(DOMAIN_FACTOR * total / MIB) + DOMAIN_SLACK_MIB)


def stmap_cache_bytes(width, height):
    return STMAP_EFFECTS * STMAP_FRAMES * STMAP_BYTES_PER_PIXEL * width * height


def replay_tmpfs_mib(sources, replay_on=True):
    """Size of the /replay tmpfs: one 200 MiB ring per source (at most 8) plus slack. 0 when off.

    Measured 2026-11-09 on studio-a, 1080p29.97, 3 sources, --replay-buffer-secs=300, 30 minutes:
    each file is 209715200 bytes whatever the buffer length. The busiest source reached 204796 KiB
    and stayed there; the other two were at 90320 and 13692 KiB and still growing about 3 MiB and
    0.5 MiB a minute, long after the 300 s window had filled, because a ring only reuses space once
    it wraps. df showed 308808 KiB, the same as the three files, so tmpfs adds no overhead worth
    sizing. Every source therefore ends up at its cap, and that is what has to fit."""
    if not replay_on or sources <= 0:
        return 0
    return min(sources, REPLAY_MAX_SOURCES) * REPLAY_RING_MIB + REPLAY_TMPFS_SLACK_MIB


def switchframe_resources(cpu, memory_mib, replay_tmpfs_mib=0):
    """Requests from the design; limits 3x CPU and 2x memory; GOMEMLIMIT 75% of the memory limit.
    These ratios reproduce the chart's defaults (2/4Gi -> 6/8Gi, 6GiB).
    The replay tmpfs is memory the pod uses but Go doesn't: it adds to the request and the limit,
    not to GOMEMLIMIT. Returns (resources, GOMEMLIMIT, memory request MiB)."""
    cpu = Fraction(str(cpu))
    memory_mib = Fraction(str(memory_mib))
    tmpfs = Fraction(replay_tmpfs_mib)
    heap_limit_mib = memory_mib * 2
    request_mib = memory_mib + tmpfs
    return {
        "requests": {"cpu": cpu_quantity(cpu), "memory": memory_quantity(request_mib)},
        "limits": {"cpu": cpu_quantity(cpu * 3), "memory": memory_quantity(heap_limit_mib + tmpfs)},
    }, go_mem_limit(heap_limit_mib * Fraction(3, 4)), request_mib


def test_source_resources(cpu=None, memory_mib=None):
    """Requests from the design, defaulting to 250m/256Mi. Limits 1 CPU/1Gi, raised to the requests."""
    cpu = Fraction(str(cpu)) if cpu is not None else TEST_SOURCE_REQUESTS[0]
    memory_mib = Fraction(str(memory_mib)) if memory_mib is not None else Fraction(TEST_SOURCE_REQUESTS[1])
    return {
        "requests": {"cpu": cpu_quantity(cpu), "memory": memory_quantity(memory_mib)},
        "limits": {"cpu": cpu_quantity(max(cpu, Fraction(TEST_SOURCE_LIMITS[0]))),
                   "memory": memory_quantity(max(memory_mib, Fraction(TEST_SOURCE_LIMITS[1])))},
    }


def quota(requests):
    """requests: iterable of (cpu cores, memory MiB) for every pod container in the workload.
    Sum + 20%, rounded up to whole CPUs and whole GiB."""
    cpu = sum((Fraction(str(c)) for c, _ in requests), Fraction(0)) * QUOTA_HEADROOM
    mem = sum((Fraction(str(m)) for _, m in requests), Fraction(0)) * QUOTA_HEADROOM
    return {"cpu": str(math.ceil(cpu)), "memory": f"{math.ceil(mem / 1024)}Gi", "gpu": 0}


def workload_requests(switchframe_cpu, switchframe_memory_mib, test_sources):
    """The pod requests that the quota has to cover. test_sources: [(cpu, memory MiB)]."""
    pods = [(switchframe_cpu, switchframe_memory_mib)]
    pods += list(test_sources)
    pods += [FLOW_AGENT, SSO_GATEWAY, RECONCILER]
    return pods


def volumes(width, height, clips_storage_max_gb=None, playout_on=False, playout_cache_max_gb=None, recording_gib=None,
            replay_tmpfs=0):
    """/cache, a PVC, holds the ST map prewarm for the engine's resolution: too small and the
    prewarm fails to finish (an emptyDir limit got the pod evicted). The state PVC holds the clips
    and, when on, the playout cache: SwitchFrame keeps that in <state dir>/playout-cache
    (server/cmd/switchframe/app_playout.go), not under --cache-dir."""
    clips = CLIPS_DEFAULT_GB if clips_storage_max_gb is None else clips_storage_max_gb
    state = math.ceil(clips) + 1
    if playout_on:
        state += math.ceil(PLAYOUT_CACHE_DEFAULT_GB if playout_cache_max_gb is None else playout_cache_max_gb)
    out = {"stateGi": max(STATE_FLOOR_GIB, state)}
    out["cacheGi"] = math.ceil(Fraction(stmap_cache_bytes(width, height), 2**30)) + CACHE_SLACK_GIB
    out["replayTmpfsMi"] = int(replay_tmpfs)
    if recording_gib is not None:
        out["recordingsGi"] = int(recording_gib)
    return out


# ---------------------------------------------------------------------------------------------
# Kubernetes / Go quantities
# ---------------------------------------------------------------------------------------------
def cpu_quantity(cores: Fraction):
    if cores.denominator == 1:
        return str(cores.numerator)
    milli = cores * 1000
    return f"{math.ceil(milli)}m"


def memory_quantity(mib: Fraction):
    mib = Fraction(math.ceil(mib))
    if mib % 1024 == 0:
        return f"{int(mib // 1024)}Gi"
    return f"{int(mib)}Mi"


def go_mem_limit(mib: Fraction):
    mib = math.floor(mib)
    if mib % 1024 == 0:
        return f"{mib // 1024}GiB"
    return f"{mib}MiB"
