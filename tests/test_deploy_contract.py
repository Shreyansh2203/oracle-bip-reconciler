"""The deployment contract, asserted rather than assumed.

The previous deploy broke because requirements.txt was missing pydantic-settings and redis,
both declared runtime dependencies, so the Vercel runtime could not import the app. A blueprint
that lists settings the code never reads is the same class of defect in the other direction:
it looks configured and is not. Both are cheap to catch statically, so both are tested here
rather than left to the next deploy.

Nothing in this module reaches the network or reads a real credential.
"""

import ast
import json
import re
import sys
from pathlib import Path

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the pinned toolchain is 3.12; this keeps 3.9/3.10 dev boxes working
    import tomli as tomllib

from src.core.config import Settings

REPO_ROOT = Path(__file__).resolve().parent.parent

# BIP report paths and the cache TTL are read with os.getenv rather than through Settings,
# so the field list above does not see them. Pull the names straight out of the source so
# this list cannot quietly fall behind the code.
ENV_GETENV_RE = re.compile(r'os\.getenv\(\s*"([A-Z][A-Z0-9_]*)"')

RENDER_KEY_RE = re.compile(r"^\s*-\s*key:\s*([A-Z][A-Z0-9_]*)\s*$", re.MULTILINE)
REQUIREMENTS_PIN_RE = re.compile(r"^([A-Za-z0-9._-]+)==", re.MULTILINE)


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _env_names_read_by_the_code() -> set[str]:
    names: set[str] = set()
    for path in sorted((REPO_ROOT / "src").rglob("*.py")):
        names.update(ENV_GETENV_RE.findall(path.read_text(encoding="utf-8")))
    return names


def _requirements_pins() -> set[str]:
    text = _read("requirements.txt")
    # Strip the comment header; only the "name==" pins matter and they never start with '#'.
    return {name.lower().replace("_", "-") for name in REQUIREMENTS_PIN_RE.findall(text)}


def _declared_deps(group: str) -> set[str]:
    pyproject = tomllib.loads(_read("pyproject.toml"))
    specs = pyproject["project"]["dependencies"] if group == "runtime" else pyproject["dependency-groups"]["dev"]
    return {
        re.split(r"[<>=!~\[; ]", spec, maxsplit=1)[0].strip().lower().replace("_", "-")
        for spec in specs
    }


def _render_env_keys() -> list[str]:
    return RENDER_KEY_RE.findall(_read("render.yaml"))


# ── requirements.txt is what Vercel installs ───────────────────────────────────────────────


def test_requirements_covers_every_declared_runtime_dependency():
    declared = _declared_deps("runtime")
    pins = _requirements_pins()

    missing = sorted(declared - pins)
    assert not missing, (
        f"requirements.txt is missing declared runtime dependencies {missing}. "
        "Vercel installs this file, not pyproject.toml, so a gap here is a failed deploy."
    )


def test_requirements_excludes_the_dev_dependency_group():
    # httpx is declared in both groups on purpose (a higher floor for the test client), so
    # only names that are dev-only count as a leak.
    dev_only = _declared_deps("dev") - _declared_deps("runtime")
    leaked = sorted(dev_only & _requirements_pins())
    assert not leaked, f"dev-only dependencies leaked into the runtime export: {leaked}"


def test_requirements_is_a_hash_pinned_uv_export():
    # Vercel installs this file, so it has to stay a hash-pinned export of uv.lock rather
    # than a hand-maintained list. Two invariants make that checkable without running uv:
    # every requirement is version-pinned, and the file records the command that produced it.
    text = _read("requirements.txt")
    assert "uv export" in text, "requirements.txt does not record its generating command"

    unpinned = [
        line
        for line in text.splitlines()
        if line and not line.startswith(("#", " ", "--")) and "==" not in line
    ]
    assert not unpinned, f"unpinned requirements: {unpinned}"

    assert text.count("--hash=sha256:") >= len(_requirements_pins()), (
        "every pin needs at least one hash; Vercel verifies these and an unpinned download "
        "would defeat the point of the export"
    )


# ── render.yaml lists only settings the code actually reads ─────────────────────────────────


def test_render_blueprint_declares_exactly_the_settings_the_code_reads():
    declared_by_code = set(Settings.model_fields) | _env_names_read_by_the_code()
    blueprint = _render_env_keys()

    assert len(blueprint) == len(set(blueprint)), f"duplicate keys in render.yaml: {blueprint}"

    unknown = sorted(set(blueprint) - declared_by_code)
    assert not unknown, (
        f"render.yaml declares {unknown}, which no setting reads. Dead config in a deploy "
        "blueprint is worse than absent config: it implies a knob that does nothing."
    )

    missing = sorted(declared_by_code - set(blueprint))
    assert not missing, f"render.yaml is missing settings the code reads: {missing}"


def test_render_blueprint_prompts_for_the_required_credentials():
    # Settings has no default for these three, so a blueprint that ships a default value
    # would start the service with a placeholder credential instead of failing loudly.
    required = {"ORACLE_URL", "ORACLE_USER", "ORACLE_PASS"}
    text = _read("render.yaml")

    for key in required:
        block = re.search(rf"-\s*key:\s*{key}\n(\s+)(\S.*)", text)
        assert block, f"{key} missing from render.yaml"
        assert "sync: false" in block.group(2), f"{key} must use 'sync: false', not a baked-in value"
        assert "value:" not in block.group(2), f"{key} must not carry a value"


def test_render_start_command_does_not_assume_venv_is_on_path():
    # Render does not promise to add .venv/bin to PATH for a custom buildCommand, so the
    # start command has to name the interpreter. A bare `uvicorn` is a build that succeeds
    # and a runtime that cannot start.
    start = re.search(r"startCommand:\s*\"?([^\"\n]+)", _read("render.yaml"))
    assert start, "render.yaml has no startCommand"
    assert start.group(1).strip().startswith(".venv/bin/"), (
        "render.yaml startCommand must address the venv interpreter explicitly"
    )


def test_render_installs_runtime_dependencies_only():
    build = re.search(r"buildCommand:\s*\"([^\"]+)\"", _read("render.yaml"))
    assert build, "render.yaml has no buildCommand"
    assert "--no-dev" in build.group(1)
    assert "--frozen" in build.group(1)


# ── vercel.json routes to the one ASGI app ─────────────────────────────────────────────────


def test_vercel_routes_everything_to_the_python_entry_point():
    vercel = json.loads(_read("vercel.json"))

    assert [b["src"] for b in vercel["builds"]] == ["api/index.py"]
    assert all(b["use"] == "@vercel/python" for b in vercel["builds"])
    # Without this catch-all rewrite every path 404s, because Vercel would look for a
    # matching file rather than handing the request to the ASGI app.
    assert vercel["routes"] == [{"src": "/(.*)", "dest": "api/index.py"}]


def test_vercel_entry_point_does_not_copy_the_app():
    # The re-export identity itself is asserted in tests/test_health.py. What matters here is
    # that the shim has not grown logic that would diverge from the real application, so the
    # module body is read from the AST rather than by line-matching a file that carries a
    # docstring explaining exactly why it must stay this small.
    tree = ast.parse(_read("api/index.py"))
    # The module docstring is the first statement; anything executable after it is the shim.
    body = [node for node in tree.body if not (isinstance(node, ast.Expr) and isinstance(node.value, ast.Constant))]

    assert [type(node) for node in body] == [ast.ImportFrom], (
        f"api/index.py must contain a single import, found {[type(n).__name__ for n in body]}"
    )
    node = body[0]
    assert node.module == "src.main"
    assert [alias.name for alias in node.names] == ["app"]


# ── no credential can be reintroduced through the template ─────────────────────────────────


def test_env_example_carries_no_usable_credential():
    # .env.example is the one env file that is tracked, and it is the file a careless
    # `cp .env.example .env` then `git add .` would push. Its secrets must stay placeholders.
    values = dict(
        line.split("=", 1)
        for line in _read(".env.example").splitlines()
        if line.strip() and not line.strip().startswith("#") and "=" in line
    )

    for key in ("ORACLE_USER", "ORACLE_PASS"):
        assert values[key].startswith("YOUR_"), f".env.example must keep {key} as a placeholder"
    assert "example" in values["ORACLE_URL"], ".env.example must not name a real tenant host"
    for key in ("REDIS_URL", "ORACLE_BIP_INVOICE_PATH", "ORACLE_BIP_RECEIPT_PATH"):
        assert values[key] == "", f".env.example must leave optional {key} empty"
    assert "example" in values["CORS_ORIGINS"], ".env.example must not name a real browser origin"


def test_gitignore_keeps_env_files_out_but_the_template_in():
    # The rule that keeps a credential out of the tree has to survive being rewritten, so it
    # is asserted here rather than trusted. `.env` is ignored, `.env.*` is ignored, and the
    # negated template rule is what keeps .env.example tracked.
    lines = [line.strip() for line in _read(".gitignore").splitlines()]
    assert ".env" in lines
    assert ".env.*" in lines
    assert "!.env.example" in lines
