"""Read a published workload XML into the toolkit's published JSON (docs/orchestration/01-contract.md).

In this order, stopping at the first failure:
1. refuse any document containing a DOCTYPE or entity declaration, before any parsing;
2. validate against schemas/dmf-workload-v1.xsd, with network and file access disabled;
3. convert to the published JSON, which the semantic checks then use.
"""
import re
from pathlib import Path

from .problems import CompileError, Problem

NS = "urn:dmf:workload:v1"
SCHEMA_PATH = Path(__file__).resolve().parents[2] / "schemas" / "dmf-workload-v1.xsd"
MAX_BYTES = 16 * 2**20

# XML names are case-sensitive, but refusing any case costs nothing and closes the question.
_DECLARATION = re.compile(rb"<!\s*(DOCTYPE|ENTITY)", re.IGNORECASE)
_PATH_TOKEN = re.compile(r"([A-Za-z0-9_-]+)|\[([0-9]+)\]|\.")
_INTEGER = re.compile(r"-?[0-9]+")
_NUMBER = re.compile(r"-?[0-9]+(\.[0-9]+)?")

_schema = None


def schema():
    """The XSD, loaded once from this repo. It has no imports or includes."""
    global _schema
    if _schema is None:
        import xmlschema
        _schema = xmlschema.XMLSchema10(str(SCHEMA_PATH))
    return _schema


def _refuse(message):
    raise CompileError([Problem("read", "document", message)])


def check_declarations(data: bytes):
    """Step 1. Works on raw bytes, so nothing has parsed the document yet."""
    if len(data) > MAX_BYTES:
        _refuse(f"larger than {MAX_BYTES // 2**20} MiB")
    m = _DECLARATION.search(data)
    if m:
        kind = m.group(1).decode().upper()
        _refuse(f"contains a {kind} declaration, which published workloads never have; refused before parsing")
    if data.startswith((b"\xff\xfe", b"\xfe\xff")):
        _refuse("is not UTF-8")
    try:
        data.decode("utf-8")
    except UnicodeDecodeError as e:
        _refuse(f"is not UTF-8 ({e.reason} at byte {e.start})")


def validate(data: bytes):
    """Step 2. Returns the root element. allow='none' forbids any URL or file resolution, and
    defuse='always' parses with a parser that refuses DTDs and entities."""
    import xmlschema
    sch = schema()
    try:
        resource = xmlschema.XMLResource(data, allow="none", defuse="always")
    except Exception as e:  # malformed XML, or something the safe parser refused
        _refuse(f"is not well-formed XML: {e}")
    errors = list(sch.iter_errors(resource, use_location_hints=False))
    if errors:
        problems = []
        for e in errors[:20]:
            path = getattr(e, "path", None) or "document"
            problems.append(Problem("read", path, f"schema: {e.reason or e.message}"))
        if len(errors) > 20:
            problems.append(Problem("read", "document", f"schema: {len(errors) - 20} more errors"))
        raise CompileError(problems)
    return resource.root


def read(data: bytes):
    """All three steps. Returns the published JSON document."""
    check_declarations(data)
    root = validate(data)
    return to_published(root)


# ---------------------------------------------------------------------------------------------
# XML -> published JSON
# ---------------------------------------------------------------------------------------------
def _q(name):
    return f"{{{NS}}}{name}"


def _child(el, name):
    return el.find(_q(name))


def _children(el, name):
    return el.findall(_q(name))


def _decimal(text):
    return int(text) if _INTEGER.fullmatch(text) else float(text)


def _bool(text):
    return text in ("true", "1")


def _text(el):
    return el.text or ""


def _set_attrs(out, el, names, conv=None):
    for name in names:
        v = el.get(name)
        if v is not None:
            out[name] = conv[name](v) if conv and name in conv else v


def scalar(vtype, text, path):
    if vtype == "string":
        return text
    if vtype == "integer":
        if not _INTEGER.fullmatch(text):
            raise ValueError(f"value {path!r}: {text!r} is not an integer")
        return int(text)
    if vtype == "number":
        if not _NUMBER.fullmatch(text):
            raise ValueError(f"value {path!r}: {text!r} is not a decimal number")
        return float(text)
    if vtype == "boolean":
        if text not in ("true", "false"):
            raise ValueError(f"value {path!r}: {text!r} is not true or false")
        return text == "true"
    if vtype == "secret":
        if not text:
            raise ValueError(f"value {path!r}: a secret reference needs a name")
        return {"$secret": text}
    if text:
        raise ValueError(f"value {path!r}: type {vtype} must be empty")
    return {"null": None, "emptyObject": {}, "emptyArray": []}[vtype]


def parse_path(path):
    """'a.b[0][1].c' -> ['a', 'b', 0, 1, 'c']"""
    tokens, pos = [], 0
    while pos < len(path):
        m = _PATH_TOKEN.match(path, pos)
        if not m or m.end() == pos:
            raise ValueError(f"value path {path!r} can't be parsed")
        if m.group(1) is not None:
            tokens.append(m.group(1))
        elif m.group(2) is not None:
            tokens.append(int(m.group(2)))
        pos = m.end()
    return tokens


def value_tree(el):
    """<value path type> leaves -> the JSON tree they encode."""
    root = {}
    for v in _children(el, "value"):
        path = v.get("path")
        leaf = scalar(v.get("type"), _text(v), path)
        tokens = parse_path(path)
        node = root
        for i, tok in enumerate(tokens):
            last = i == len(tokens) - 1
            nxt = None if last else ([] if isinstance(tokens[i + 1], int) else {})
            if isinstance(tok, str):
                if not isinstance(node, dict):
                    raise ValueError(f"value path {path!r}: {tok!r} indexes an array as an object")
                if last:
                    if tok in node:
                        raise ValueError(f"value path {path!r} is given twice")
                    node[tok] = leaf
                else:
                    node = node.setdefault(tok, nxt)
            else:
                if not isinstance(node, list):
                    raise ValueError(f"value path {path!r}: [{tok}] indexes an object as an array")
                if tok > len(node):
                    raise ValueError(f"value path {path!r}: index {tok} skips {len(node)}")
                if last:
                    if tok < len(node):
                        raise ValueError(f"value path {path!r} is given twice")
                    node.append(leaf)
                else:
                    if tok == len(node):
                        node.append(nxt)
                    node = node[tok]
            if not last and not isinstance(node, (dict, list)):
                raise ValueError(f"value path {path!r} goes through a leaf value")
            if not last and (isinstance(node, list) != isinstance(tokens[i + 1], int)):
                raise ValueError(f"value path {path!r} mixes object and array at {tokens[i + 1]!r}")
    return root


def _selection(el):
    return {u.get("block"): int(u.get("copies")) for u in _children(el, "use")}


def _discovery(el):
    out = {"register": _bool(el.get("register"))}
    labels = [_text(lab) for lab in _children(el, "label")]
    if labels:
        out["labels"] = labels
    return out


def _function(el):
    fn = {}
    _set_attrs(fn, el, ["id", "type", "typeVersion", "name", "domain"])
    sel = _child(el, "inputs")
    if sel is not None:
        fn["inputs"] = _selection(sel)
    sel = _child(el, "outputs")
    if sel is not None:
        fn["outputs"] = _selection(sel)
    feats = _child(el, "features")
    if feats is not None:
        fn["features"] = {f.get("block"): _bool(f.get("enabled")) for f in _children(feats, "feature")}
    fn["parameters"] = value_tree(_child(el, "parameters"))
    settings = _child(el, "settings")
    if settings is not None:
        fn["settings"] = value_tree(settings)
    control = _child(el, "control")
    if control is not None:
        c = {}
        for name, attrs in (("health", ["type", "port", "path"]), ("api", ["type", "port", "path"]), ("nmos", ["registers"])):
            sub = _child(control, name)
            if sub is not None:
                c[name] = {}
                _set_attrs(c[name], sub, attrs, {"port": int})
        fn["control"] = c
    deps = _child(el, "dependencies")
    if deps is not None:
        fn["dependencies"] = []
        for d in _children(deps, "dependency"):
            dep = {}
            _set_attrs(dep, d, ["name", "kind", "required", "role", "binding"], {"required": _bool})
            fn["dependencies"].append(dep)
    res = _child(el, "resources")
    if res is not None:
        r = {}
        _set_attrs(r, res, ["cpu", "memoryMiB", "networkMbps", "storageGiB"],
                   {k: _decimal for k in ("cpu", "memoryMiB", "networkMbps", "storageGiB")})
        gpu = _child(res, "gpu")
        if gpu is not None:
            r["gpu"] = {"class": gpu.get("class"), "count": int(gpu.get("count"))}
        fn["resources"] = r
    exp = _child(el, "expectations")
    if exp is not None:
        fn["expectations"] = {}
        _set_attrs(fn["expectations"], exp, ["resilience", "scaling"])
    pl = _child(el, "placement")
    if pl is not None:
        p = {}
        _set_attrs(p, pl, ["site", "cluster", "environment", "licensing"])
        for name in ("with", "apart"):
            ids = [_text(x) for x in _children(pl, name)]
            if ids:
                p[name] = ids
        fn["placement"] = p
    disc = _child(el, "discovery")
    if disc is not None:
        fn["discovery"] = _discovery(disc)
    return fn


def _rational(el, prefix):
    return {"num": int(el.get(prefix + "Num")), "den": int(el.get(prefix + "Den"))}


def _flow(el):
    flow = {}
    _set_attrs(flow, el, ["id", "label", "domain", "essence", "writer"])
    flow["readers"] = [_text(r) for r in _children(el, "reader")]
    f = _child(el, "format")
    fmt = {"mediaType": f.get("mediaType")}
    if f.get("grainRateNum") is not None:
        fmt["grainRate"] = _rational(f, "grainRate")
    if f.get("sampleRateNum") is not None:
        fmt["sampleRate"] = _rational(f, "sampleRate")
    _set_attrs(fmt, f, ["frameWidth", "frameHeight", "interlace", "colorspace", "transfer", "channelCount"],
               {"frameWidth": int, "frameHeight": int, "channelCount": int})
    flow["format"] = fmt
    flow["historyMs"] = int(el.get("historyMs"))
    flow["discovery"] = _discovery(_child(el, "discovery"))
    return flow


def to_published(root):
    try:
        return _to_published(root)
    except ValueError as e:
        raise CompileError([Problem("read", "document", str(e))])


def _to_published(root):
    meta = {}
    _set_attrs(meta, root, ["id", "name", "uid", "revision", "libraryVersion"], {"revision": int})
    desc = _child(root, "description")
    if desc is not None:
        meta["description"] = _text(desc)

    d = _child(root, "defaults")
    v, a = _child(d, "video"), _child(d, "audio")
    defaults = {
        "video": {"grainRate": _rational(v, "grainRate"),
                  "frameSize": {"width": int(v.get("frameWidth")), "height": int(v.get("frameHeight"))},
                  "colorspace": v.get("colorspace")},
        "audio": {"sampleRate": _rational(a, "sampleRate"), "channelCount": int(a.get("channelCount"))},
    }
    mxl = _child(d, "mxl")
    if mxl is not None:
        defaults["mxl"] = {"historyMs": int(mxl.get("historyMs"))}

    spec = {"defaults": defaults,
            "domains": [{"id": x.get("id")} for x in _children(_child(root, "domains"), "domain")],
            "functions": [_function(f) for f in _children(_child(root, "functions"), "function")],
            "externals": [], "wires": [], "flows": []}
    access = _child(root, "access")
    if access is not None:
        spec["access"] = {"roles": {r.get("name"): [_text(m) for m in _children(r, "member")]
                                    for r in _children(access, "role")}}
    ext = _child(root, "externals")
    for e in (_children(ext, "external") if ext is not None else []):
        x = {}
        _set_attrs(x, e, ["id", "kind", "name"])
        notes = _child(e, "notes")
        if notes is not None:
            x["notes"] = _text(notes)
        ref = _child(e, "reference")
        if ref is not None:
            x["reference"] = {"kind": ref.get("kind"), "value": ref.get("value")}
        spec["externals"].append(x)
    flows = _child(root, "flows")
    spec["flows"] = [_flow(f) for f in (_children(flows, "flow") if flows is not None else [])]
    wires = _child(root, "wires")
    spec["wires"] = [{"from": w.get("from"), "to": w.get("to")}
                     for w in (_children(wires, "wire") if wires is not None else [])]

    return {"apiVersion": root.get("apiVersion"), "kind": "MediaWorkload", "metadata": meta, "spec": spec,
            # Not part of the published JSON: kept for rule 1.
            "_schemaVersion": int(root.get("schemaVersion"))}
