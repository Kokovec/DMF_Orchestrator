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
DOMAIN_FLOOR_MIB = 2048
DOMAIN_SLACK_MIB = 256
DOMAIN_FACTOR = Fraction(5, 4)
STATE_FLOOR_GIB = 5
CACHE_DEFAULT_GIB = 5
CLIPS_DEFAULT_GB = 10                    # SwitchFrame's --clip-storage-max default, 10<<30 bytes
PLAYOUT_CACHE_DEFAULT_GB = 200           # SwitchFrame's --playout-cache-max default


def v210_frame_bytes(width, height):
    return math.ceil(width / 6) * 16 * height


def video_bytes_per_second(width, height, rate: Fraction):
    return v210_frame_bytes(width, height) * rate


def domain_size_mib(video_flows):
    """video_flows: iterable of (width, height, Fraction rate).
    sizeMiB = max(2048, ceil(1.25 x sum of bytes/s x 1 s / 2^20) + 256)"""
    total = sum((video_bytes_per_second(w, h, r) for w, h, r in video_flows), Fraction(0))
    return max(DOMAIN_FLOOR_MIB, math.ceil(DOMAIN_FACTOR * total / MIB) + DOMAIN_SLACK_MIB)


def switchframe_resources(cpu, memory_mib):
    """Requests from the design; limits 3x CPU and 2x memory; GOMEMLIMIT 75% of the memory limit.
    These ratios reproduce the chart's defaults (2/4Gi -> 6/8Gi, 6GiB)."""
    cpu = Fraction(str(cpu))
    memory_mib = Fraction(str(memory_mib))
    limit_mib = memory_mib * 2
    return {
        "requests": {"cpu": cpu_quantity(cpu), "memory": memory_quantity(memory_mib)},
        "limits": {"cpu": cpu_quantity(cpu * 3), "memory": memory_quantity(limit_mib)},
    }, go_mem_limit(limit_mib * Fraction(3, 4))


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


def volumes(clips_storage_max_gb=None, playout_on=False, playout_cache_max_gb=None, recording_gib=None):
    clips = CLIPS_DEFAULT_GB if clips_storage_max_gb is None else clips_storage_max_gb
    out = {"stateGi": max(STATE_FLOOR_GIB, math.ceil(clips) + 1)}
    if playout_on:
        cache = PLAYOUT_CACHE_DEFAULT_GB if playout_cache_max_gb is None else playout_cache_max_gb
        out["cacheGi"] = math.ceil(cache) + 2
    else:
        out["cacheGi"] = CACHE_DEFAULT_GIB
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
