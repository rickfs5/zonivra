"""META LIVE READINESS 04: structural invariants of the inert Meta transport (static, AST/text based; no network). They are what keeps 'prepared' from becoming 'active'."""
import ast
import re
from pathlib import Path

SRC = Path(__file__).resolve().parents[1] / "mineguard"
META = {"sam31", "meta_obs", "meta_pipeline", "metahttp", "metaclient", "muse_open", "projection"}
NETWORK = {"http", "urllib", "ssl", "requests", "httpx", "aiohttp", "ftplib", "smtplib", "websockets", "websocket", "grpc", "boto3", "socketserver", "xmlrpc"}


def imports(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom):
            base = ("." * n.level) + (n.module or "")
            out.add(base)
            out |= {f"{base}.{a.name}".lstrip(".") if n.level == 0 else a.name for a in n.names}
    return out


def test_no_application_module_imports_any_meta_module():
    for p in sorted(SRC.rglob("*.py")):
        if p.stem in META:
            continue
        got = {i.split(".")[-1] for i in imports(p)}
        assert not (got & META), (p.name, got & META)        # no route, startup path, provider panel or explanation module can reach the transport or the clients


def absolute_modules(path: Path) -> set[str]:
    out = set()
    for n in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(n, ast.Import):
            out |= {a.name for a in n.names}
        elif isinstance(n, ast.ImportFrom) and n.level == 0 and n.module:
            out.add(n.module)
    return out


def is_network(mod: str) -> bool:
    return mod.split(".")[0] in NETWORK - {"urllib"} or mod in ("urllib.request", "urllib.error", "urllib3") or mod.startswith("http")


DYNAMIC_CALLS = ("__import__", "import_module", "exec", "eval", "compile", "spec_from_file_location", "load_module", "find_spec")


def dynamic_reach(path: Path) -> list[str]:
    """Static signs that a module could load another module dynamically or name a meta module as a string. Review CD-7: plain import statements were the only thing checked.
    A dynamic-load call is allowed ONLY with a literal string that names no meta module; any computed argument is flagged, and so is any string constant naming a meta module."""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found = []
    for n in ast.walk(tree):
        if isinstance(n, ast.Call):
            f = n.func
            name = f.id if isinstance(f, ast.Name) else (f.attr if isinstance(f, ast.Attribute) else "")
            builtin_exec = isinstance(f, ast.Name) and name in ("__import__", "exec", "eval", "compile")      # `re.compile(...)` is an attribute call and is not code loading
            loader = isinstance(f, ast.Attribute) and name in ("import_module", "spec_from_file_location", "load_module", "find_spec", "__import__")
            if builtin_exec or loader:
                a = n.args[0] if n.args else None
                if not (isinstance(a, ast.Constant) and isinstance(a.value, str)):
                    found.append(f"call-computed:{name}")
        elif isinstance(n, ast.Import) and any(a.name.split(".")[0] in ("importlib", "runpy", "pkgutil", "imp") for a in n.names):
            found.append("import:dynamic-loader")
        elif isinstance(n, ast.ImportFrom) and (n.module or "").split(".")[0] in ("importlib", "runpy", "pkgutil", "imp"):
            found.append("from:dynamic-loader")
        elif isinstance(n, ast.Constant) and isinstance(n.value, str) and re.search(r"\b(" + "|".join(sorted(META - {"projection"})) + r")\b|mineguard\.projection|\.projection\b", n.value):
            found.append("string:" + n.value[:40])
    return found


EXPECTED_DYNAMIC = {"provenance.py": ["import:dynamic-loader", "import:dynamic-loader", "call-computed:find_spec"]}    # package-metadata lookups in the pre-existing provenance module (reviewed; unrelated)


def test_no_application_module_can_reach_the_meta_modules_dynamically_or_by_name():
    for p in sorted(SRC.rglob("*.py")):
        if p.stem in META:
            continue
        assert dynamic_reach(p) == EXPECTED_DYNAMIC.get(p.name, []), (p.name, dynamic_reach(p))      # frozen: a new dynamic load or a string naming a meta module fails


def test_the_dynamic_reach_scanner_detects_each_bypass_shape(tmp_path):
    shapes = {"import importlib\nimportlib.import_module('mineguard.metaclient')\n": ("import:dynamic-loader", "string:mineguard.metaclient"), "x = __import__('mineguard.metahttp')\n": ("string:mineguard.metahttp",),
              "x = __import__('mineguard.' + 'metahttp')\n": ("call-computed:__import__",), "n = 'a'\nx = __import__(n)\n": ("call-computed:__import__",),
              "from importlib import import_module\nimport_module('a')\n": ("from:dynamic-loader",), "name = 'mineguard.muse_open'\n": ("string:mineguard.muse_open",), "exec(code)\n": ("call-computed:exec",),
              "import runpy\n": ("import:dynamic-loader",), "import os\nos.environ\n": (), "x = __import__('uuid')\n": (), "name = 'mineguard.projection'\n": ("string:mineguard.projection",), "n = 'x.projection'\n": ("string:x.projection",), "n = 'a projection of data'\n": ()}
    for i, (src, want) in enumerate(shapes.items()):
        f = tmp_path / f"m{i}.py"
        f.write_text(src)
        got = dynamic_reach(f)
        assert all(any(g.startswith(w) for g in got) for w in want), (src, got)
        if not want:
            assert got == []


def test_only_metahttp_imports_a_network_client_among_the_meta_modules():
    for name in META:
        net = {m for m in absolute_modules(SRC / f"{name}.py") if is_network(m)}
        assert net == ({"http.client", "ssl"} if name == "metahttp" else set()), (name, net)


def test_the_http_client_is_used_in_exactly_one_dispatch_function():
    src = (SRC / "metahttp.py").read_text()
    assert len(re.findall(r"\.request\(", src)) == 1 and src.count("getresponse(") == 1


def test_no_meta_module_reads_the_environment_files_or_a_credential_source():
    for name in META:
        t = (SRC / f"{name}.py").read_text()
        for bad in ("os.environ", "getenv", "environ[", "from_env", r"(?<![A-Za-z_])open\(", "keychain", "dotenv", "subprocess", "Path(", "expanduser"):
            code = "\n".join(l for l in t.splitlines() if not l.strip().startswith("#"))
            code = re.sub(r'"""[\s\S]*?"""', "", code)
            assert not re.search(bad if bad.startswith('(?<') else re.escape(bad), code), (name, bad)


def test_default_provider_status_still_ignores_everything_and_declares_zero_calls():
    t = (SRC / "providers.py").read_text()
    assert "GovernorConfig())" in t and "from_env()" not in t.replace("deliberately does NOT use `from_env()`", "")


def test_no_route_can_enable_a_provider_credentials_or_spending():
    routes = set()
    for p in SRC.glob("*.py"):
        routes |= set(re.findall(r"@\w+\.(?:get|post|put|patch|delete)\(\s*[\"']([^\"']+)", p.read_text()))
    bad = [r for r in routes if re.search(r"sam|muse|baseten|modal|spend|credit|credential|enable|secret|budget|governor|transport|(?<![a-z])live(?![a-z])", r, re.I) and r not in ("/api/providers",)]
    assert bad == [], bad


def test_no_secret_shaped_string_in_the_new_modules_or_tests():
    pats = [re.compile(x) for x in (r"LLM_\d+_[A-Za-z0-9_-]{8,}", r"Bearer [A-Za-z0-9._-]{24,}", r"sk-[A-Za-z0-9]{20,}", r"ghp_[A-Za-z0-9]{30,}", r"AKIA[0-9A-Z]{16}", r"-----BEGIN [A-Z ]*PRIVATE KEY")]
    files = [SRC / f"{n}.py" for n in META] + [Path(__file__).parent / f for f in ("test_meta_sam31.py", "test_meta_pipeline.py", "test_meta_transport.py", "test_muse_open.py")]
    for p in files:
        t = p.read_text()
        t = t.replace("LLM_[0-9]+_[A-Za-z0-9_-]+", "")
        for rx in pats:
            assert not rx.search(t), (p.name, rx.pattern)


def test_the_canonical_modules_and_the_reviewed_explanation_layer_do_not_import_the_new_ones():
    for name in ("schema", "canon", "timebase", "detector", "explain", "costgov", "rawjson", "incident", "netguard"):
        p = SRC / f"{name}.py"
        if p.exists():
            assert not ({i.split(".")[-1] for i in imports(p)} & META), name
    for p in (SRC / "risk").glob("*.py"):
        assert not ({i.split(".")[-1] for i in imports(p)} & META), p.name
