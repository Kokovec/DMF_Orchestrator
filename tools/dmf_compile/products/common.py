"""Helpers shared by the product modules."""
from fractions import Fraction

MISSING = object()

# Rates as the toolkit writes them, and as SwitchFrame's --format spells them.
RATES = {"23.976": Fraction(24000, 1001), "24": Fraction(24), "25": Fraction(25), "29.97": Fraction(30000, 1001),
         "30": Fraction(30), "50": Fraction(50), "59.94": Fraction(60000, 1001), "60": Fraction(60)}
# SwitchFrame formats at the pinned commit (02-switchframe-options.md, engine).
FORMATS = {"1920x1080": ["23.976", "24", "25", "29.97", "30", "50", "59.94", "60"],
           "1280x720": ["25", "29.97", "30", "50", "59.94", "60"],
           "3840x2160": ["25", "29.97", "30", "50", "59.94", "60"]}


def get(tree, path, default=MISSING):
    node = tree
    for key in path.split("."):
        if not isinstance(node, dict) or key not in node:
            return default
        node = node[key]
    return node


def leaves(tree, prefix=""):
    """Yield (path, value) for every leaf, as the XML encodes them."""
    if isinstance(tree, dict) and tree and "$secret" not in tree:
        for k, v in tree.items():
            yield from leaves(v, f"{prefix}.{k}" if prefix else k)
    elif isinstance(tree, list) and tree:
        for i, v in enumerate(tree):
            yield from leaves(v, f"{prefix}[{i}]")
    else:
        yield prefix, tree


def copies(selection, block):
    """Copy ids of a block in use: mxl-src, mxl-src_2, mxl-src_3 ..."""
    n = (selection or {}).get(block, 0)
    return [block if i == 1 else f"{block}_{i}" for i in range(1, n + 1)]


def is_secret(v):
    return isinstance(v, dict) and set(v) == {"$secret"} and isinstance(v["$secret"], str)


def is_number(v):
    return isinstance(v, (int, float)) and not isinstance(v, bool)


def rate_fraction(r):
    return Fraction(r["num"], r.get("den", 1))


def rate_name(fraction):
    for name, f in RATES.items():
        if f == fraction:
            return name
    return f"{fraction.numerator}/{fraction.denominator}"


def frame_size(text):
    try:
        w, h = text.split("x")
        return int(w), int(h)
    except (AttributeError, ValueError):
        return None


def format_name(resolution, rate):
    size = frame_size(resolution)
    return f"{size[1]}p{rate}" if size else None


class Compiled:
    """What a product module returns for one function."""

    def __init__(self, entry, pod_requests, config=None, secrets=()):
        self.entry = entry                # the function's item in spec.functions of the workload YAML
        self.pod_requests = pod_requests  # [(cpu cores, memory MiB)] that count towards the quota
        self.config = config              # <id>.config.json content, for SwitchFrame
        self.secrets = list(secrets)      # [(where, secret name)] that rule 6 must find
