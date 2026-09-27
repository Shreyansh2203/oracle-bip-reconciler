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
import tomllib
from pathlib import Path

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


# Declared runtime dependencies the code deliberately does not import by name, each with the
# reason. Everything else must appear in src/ or api/ -- see the test below.
NOT_IMPORTED_BY_NAME = {
    "uvicorn": "the ASGI server, run as a process (uvicorn src.main:app), never imported",
    "python-dotenv": "a transitive requirement of pydantic-settings, which reads .env itself",
}


def _imported_module_roots() -> set[str]:
    """Every module name imported anywhere in src/ or api/, read from the AST.

    Read from the parse tree rather than by scanning the text, because a prose mention in a
    docstring is not an import: a textual check would pass on a comment saying "uvicorn is
    started as a process", which is exactly the dependency this test exists to catch.
    """
    roots: set[str] = set()
    for directory in ("src", "api"):
        for path in sorted((REPO_ROOT / directory).rglob("*.py")):
            for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
                if isinstance(node, ast.Import):
                    roots.update(alias.name.split(".")[0] for alias in node.names)
                elif isinstance(node, ast.ImportFrom) and node.module and node.level == 0:
                    roots.add(node.module.split(".")[0])
    return {name.lower() for name in roots}


def test_every_declared_runtime_dependency_is_used_by_the_code():
    # scipy was declared, pinned in requirements.txt and pulled in numpy -- 142 MB of the
    # 500 MB Vercel function limit, uploaded on every build -- and nothing in src/ or api/
    # imported it or numpy. A dependency nothing imports is a dependency that only costs
    # money, so the declaration is checked against the source rather than trusted.
    imported = _imported_module_roots()

    unused = []
    for name in sorted(_declared_deps("runtime")):
        if name in NOT_IMPORTED_BY_NAME:
            continue
        # A distribution name maps to an import name by the usual substitutions: the
        # hyphen becomes an underscore, and some names differ in case (Levenshtein).
        spellings = {name, name.replace("-", "_"), name.replace("-", "")}
        if not imported & spellings:
            unused.append(name)

    assert not unused, (
        f"declared runtime dependencies nothing in src/ or api/ imports: {unused}. They are "
        "installed on every build and in every Vercel function. Remove the declaration, or add "
        "the name to NOT_IMPORTED_BY_NAME with the reason it is not imported."
    )


def test_the_dependency_allow_list_does_not_go_stale():
    # An entry for a dependency the code has since started importing is not harmless: it
    # would hide a future removal of the import, and the entry would outlive its reason.
    imported = _imported_module_roots()
    declared = _declared_deps("runtime")
    for name in NOT_IMPORTED_BY_NAME:
        assert name in declared, f"NOT_IMPORTED_BY_NAME lists {name}, which is no longer declared"
        spellings = {name, name.replace("-", "_"), name.replace("-", "")}
        assert not imported & spellings, (
            f"{name} is in NOT_IMPORTED_BY_NAME but the code now imports it; remove the entry"
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


# ── vercel.json does not configure the deployment, so the README has to ─────────────────────


def _vercel_section() -> str:
    """The body of the `### Vercel` subsection, up to the next heading at its level or above.

    Scoped by heading *level* rather than by "any `#`", because the section uses `####`
    subheadings of its own and those are part of it.
    """
    lines = _read("README.md").splitlines()
    start = next(i for i, line in enumerate(lines) if line.strip() == "### Vercel")
    end = next(
        (
            i
            for i in range(start + 1, len(lines))
            if re.match(r"^#{1,3}\s", lines[i])
        ),
        len(lines),
    )
    return "\n".join(lines[start:end])


def test_vercel_declares_no_environment_of_its_own():
    # Settings has no default for the three Oracle variables and src/core/config.py builds
    # them at import time, so a Vercel function with none of them set raises a
    # ValidationError while initialising and answers 503 -- no wait, 500 -- to every path.
    # vercel.json cannot supply them: `env` and `builds` are mutually exclusive, and this
    # file uses `builds`. The only remaining place is the dashboard.
    vercel = json.loads(_read("vercel.json"))
    assert "env" not in vercel
    assert "env" not in vercel.get("build", {})


def test_the_vercel_section_tells_the_operator_to_set_the_three_credentials_in_the_dashboard():
    section = _vercel_section()
    for key in ("ORACLE_URL", "ORACLE_USER", "ORACLE_PASS"):
        assert key in section, (
            f"the Vercel section never mentions {key}. vercel.json declares no env block, so "
            "the dashboard is the only place these can be set."
        )
    # Naming the variables is not enough: the reader has to be told which screen.
    assert re.search(r"dashboard|Settings|Environment Variables", section, re.I), (
        "the Vercel section names the variables but not the screen to set them on"
    )


def test_the_vercel_section_says_the_build_succeeds_and_the_requests_still_fail():
    # This is the failure mode that is invisible until it has shipped: the build is fine,
    # because the import happens in the function's init phase, and every request 500s. A
    # reader who only checks the build log will call the deploy successful.
    section = _vercel_section().lower()
    assert "500" in section, "the Vercel section does not say what a missing credential does"
    assert "build" in section, "the Vercel section does not distinguish the build from the requests"


# ── the FastAPI preset signature, and the explicit opt-out from it ────────────────────────

# Vercel selects a framework preset from a matching dependency plus a matching entrypoint.
# The FastAPI preset looks for one of these names inside src/ or app/.
PRESET_ENTRYPOINTS = ("app.py", "index.py", "server.py", "main.py", "wsgi.py", "asgi.py")
PRESET_SEARCH_DIRS = ("", "src", "app")


def _preset_signature_is_present() -> list[str]:
    """Every reason Vercel would select its FastAPI preset for this repository."""
    reasons = []
    if "fastapi" in _declared_deps("runtime"):
        reasons.append("fastapi is a declared runtime dependency")
    for directory in PRESET_SEARCH_DIRS:
        base = REPO_ROOT / directory if directory else REPO_ROOT
        for name in PRESET_ENTRYPOINTS:
            if (base / name).is_file():
                reasons.append(f"{directory or '.'}/{name} is an entrypoint Vercel recognises")
    return reasons


def test_the_fastapi_preset_signature_is_fully_present_in_this_repository():
    # Asserted positively, not as a warning. Both halves of Vercel's FastAPI detection
    # match here: `fastapi` is a declared runtime dependency, and src/main.py defines a
    # top-level `app = FastAPI(...)`. The opt-out below is therefore load-bearing, and this
    # is what tells the next reader that removing it would change the deployment.
    reasons = _preset_signature_is_present()
    assert any("fastapi" in reason for reason in reasons), reasons
    assert any("src/main.py" in reason for reason in reasons), reasons


def test_vercel_opts_out_of_the_framework_preset_explicitly():
    # `framework: null` is Vercel's documented way to select the "Other" preset, and it
    # holds whether or not the signature below is matched. Without it the only thing
    # preventing the FastAPI preset from being applied is that `builds` is present -- which
    # Vercel calls legacy, and which cannot coexist with `functions`. That is incidental
    # protection, so the opt-out is stated rather than depended on.
    vercel = json.loads(_read("vercel.json"))
    assert "framework" in vercel, (
        "vercel.json does not state a framework. The FastAPI preset is matched by this "
        "repository, and only `builds` is keeping it off."
    )
    assert vercel["framework"] is None, (
        f'vercel.json pins framework {vercel["framework"]!r}; the preset must be opted out of, '
        "not selected"
    )


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
