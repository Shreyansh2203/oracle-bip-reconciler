"""The documentation, asserted rather than trusted.

Two things drift silently here. A field is added to a Pydantic model and the README keeps
describing the old surface, so a caller reads a field that no longer exists or misses one that
does. A relative link is renamed or deleted and the README keeps pointing at it, which is
only discovered by whoever follows the link next. Both are cheap to check and neither shows
up in review, so they are checked here.

Extending tests/test_deploy_contract.py would have buried these among the deployment
assertions, where a reader would not think to look. Nothing here reaches the network.
"""

import ast
import re
import tomllib
from pathlib import Path

import pytest

from src.models import InvoiceItem, ReconciliationRequest

REPO_ROOT = Path(__file__).resolve().parent.parent
DOCS = sorted(
    path
    for path in REPO_ROOT.rglob("*.md")
    if ".venv" not in path.parts and ".git" not in path.parts and "node_modules" not in path.parts
)

LINK_RE = re.compile(r"\[([^\]]*)\]\(([^)\s]+)(?:\s+\"[^\"]*\")?\)")
JSONC_BLOCK_RE = re.compile(r"```jsonc\n(.*?)```", re.S)
FIELD_KEY_RE = re.compile(r'^\s*"([a-z_]+)"\s*:', re.M)
HEADING_RE = re.compile(r"^(#{1,6})\s+(.*)$", re.M)

# Fields that exist on a model but are not part of the wire contract a caller cares about.
IGNORED_FIELD_NAMES = set()


def _read(relative: str) -> str:
    return (REPO_ROOT / relative).read_text(encoding="utf-8")


def _slugify(heading: str) -> str:
    # GitHub's anchor rule: strip punctuation, spaces become hyphens, case-insensitive.
    return re.sub(r"[^a-z0-9 -]", "", heading.strip().lower()).replace(" ", "-")


def _anchor_exists(document: str, anchor: str) -> bool:
    return any(_slugify(m.group(2)) == anchor.lower() for m in HEADING_RE.finditer(document))


# ── relative links ─────────────────────────────────────────────────────────────────────────


@pytest.mark.parametrize("doc", DOCS, ids=lambda p: p.name)
def test_every_relative_link_in_the_documentation_resolves(doc):
    text = doc.read_text(encoding="utf-8")
    broken = []

    for _label, target in LINK_RE.findall(text):
        if target.startswith(("http://", "https://", "mailto:")):
            continue
        if target.startswith("#"):
            if not _anchor_exists(text, target[1:]):
                broken.append(target)
            continue
        path_part = target.split("#", 1)[0]
        if path_part and not (doc.parent / path_part).resolve().exists():
            broken.append(target)

    assert not broken, f"{doc.relative_to(REPO_ROOT)} links to {broken}, which do not exist"


def test_the_important_documents_are_present_and_linked_from_the_readme():
    # These are not decoration: SECURITY.md carries the credential-rotation procedure and
    # CONTRIBUTING.md carries the rule about never pointing a test at a real tenant.
    for name in ("SECURITY.md", "CONTRIBUTING.md", "docs/report_processing_rules.md"):
        assert (REPO_ROOT / name).exists(), f"{name} is missing"

    readme = _read("README.md")
    for name in ("SECURITY.md", "CONTRIBUTING.md", "docs/report_processing_rules.md"):
        assert name in readme, f"README.md does not link {name}"


def test_the_readme_documents_every_quality_gate_it_lists():
    readme = _read("README.md")
    for tool in ("ruff", "mypy", "bandit", "vulture", "pytest", "pip-audit"):
        assert tool in readme, f"README.md never mentions the {tool} gate"


# ── the API reference matches the models ────────────────────────────────────────────────────


def _tables(document: str) -> list[list[str]]:
    """Every markdown table in the document, as a list of rows."""
    tables: list[list[str]] = []
    current: list[str] = []
    for line in document.splitlines():
        if line.startswith("|"):
            current.append(line)
        elif current:
            tables.append(current)
            current = []
    if current:
        tables.append(current)
    return tables


def _section(document: str, heading: str) -> str:
    """The body of a `## ` section, up to the next `## ` heading."""
    lines = document.splitlines()
    start = next((i for i, line in enumerate(lines) if line.strip() == heading), None)
    if start is None:
        return ""
    end = next(
        (i for i in range(start + 1, len(lines)) if lines[i].startswith("## ")),
        len(lines),
    )
    return "\n".join(lines[start:end])


def _first_cells(tables: list[list[str]], header_first_cell: str) -> set[str]:
    """The leading cell of every row of the tables headed by `header_first_cell`.

    A cell may name more than one thing ("`fusion_receipt_number` / `fusion_receipt_date`"),
    so it is split on `/` before each name is taken. Scoping by header is what keeps the
    quality-gate table's `lint`/`test`/`types` out of a set of model field names.
    """
    names: set[str] = set()
    for table in tables:
        if not table:
            continue
        cells = table[0].split("|")
        header = cells[1].strip().strip("`").lower() if len(cells) > 1 else ""
        if header != header_first_cell:
            continue
        for row in table[2:]:
            parts = row.split("|")
            if len(parts) < 2:
                continue
            for name in parts[1].split("/"):
                cleaned = name.strip().strip("`* ").lower()
                if cleaned:
                    names.add(cleaned)
    return names


def _documented_fields(document: str) -> set[str]:
    """Every model field name the README names, whether in an example or in a reference table."""
    in_examples = {
        match.group(1)
        for block in JSONC_BLOCK_RE.findall(document)
        for match in FIELD_KEY_RE.finditer(block + "\n")
    }
    return (in_examples | _first_cells(_tables(document), "field")) - IGNORED_FIELD_NAMES



def _wire_names(model) -> set[str]:
    """Every name a caller can use for a field: the field name and its wire alias.

    FastAPI serialises by alias, so `_meta` is the name on the wire and `meta_extra` is
    only the name in Python. A field counts as documented under either, and a documented
    name that is neither is still an invention.
    """
    names: set[str] = set()
    for name, field in model.model_fields.items():
        names.add(name)
        if field.alias:
            names.add(field.alias)
        if field.serialization_alias:
            names.add(field.serialization_alias)
    return names


def test_every_documented_field_exists_on_a_model():
    # A field documented here but not on a model is the worst kind of documentation error:
    # it is wrong, and a caller who trusts it writes code against a field that is silently
    # discarded.
    documented = _documented_fields(_read("README.md"))
    real = _wire_names(ReconciliationRequest) | _wire_names(InvoiceItem)

    invented = sorted(documented - real)
    assert not invented, f"README.md documents fields that do not exist: {invented}"


def test_every_model_field_is_documented():
    # The other direction: a field that exists and is populated but is not in the reference
    # is a field nobody can rely on, because they have no way to know it is there.
    documented = _documented_fields(_read("README.md"))

    for model in (ReconciliationRequest, InvoiceItem):
        undocumented = sorted(set(model.model_fields) - documented - _wire_names(model))
        assert not undocumented, f"{model.__name__} fields missing from the README: {undocumented}"


# ── the reference's types and bounds, not just its field names ─────────────────────────────

# Comparing field NAMES is not enough, and the gap was not theoretical: `header_id` was
# documented as `int | null` while the model said `int | str | null`, `_meta` was documented
# under its Python name `meta_extra` rather than the alias FastAPI serialises, and
# `confidence_score` was documented as an output when it is a bounded input. All three
# passed a name-only comparison. So the type column and the bound claims are checked too.

JSON_TYPE_ALIASES = {
    str: "string",
    float: "number",
    bool: "boolean",
    int: "int",
}

REFERENCE_HEADER = "field"


def _normalise_type(cell: str) -> str:
    """A documented JSON type reduced to a comparable form.

    Backticks and the escaped pipes inside a markdown table cell carry no meaning, and
    `number` is the JSON spelling of a Python float.
    """
    text = cell.replace("\\|", "|").replace("`", "").strip()
    # Only pipes are separators. A comma appears inside `{"warnings": string[]}`, so
    # splitting on one would shatter the object type.
    parts = [part.strip() for part in text.split("|") if part.strip()]
    return ", ".join(JSON_TYPE_ALIASES.get(part, part) for part in parts)


def _declared_bounds(field) -> dict[str, object]:
    """{bound name: value} for whatever pydantic actually attached to this field."""
    bounds: dict[str, object] = {}
    for constraint in field.metadata or ():
        for name in ("max_length", "min_length", "ge", "le"):
            value = getattr(constraint, name, None)
            if value is not None:
                bounds[name] = value
    return bounds


def _split_row(row: str) -> list[str]:
    """Split a markdown table row on its *unescaped* pipes.

    A documented type is written `` `string \\| null` ``, so splitting on every pipe
    shatters it into `string \\` and `null`.
    """
    return [cell.strip() for cell in re.split(r"(?<!\\)\|", row.strip().strip("|"))]


def _render_annotation(annotation) -> str:
    """A model annotation reduced to the same form as `_normalise_type`."""
    import types
    from typing import Literal, Union, get_args, get_origin

    if annotation is type(None):
        return "null"
    origin = get_origin(annotation)
    # `str | None` and `Optional[str]` are the same union written two ways, and pydantic
    # keeps whichever the declaration used, so both spellings have to be recognised.
    if origin is Union or origin is types.UnionType:
        return ", ".join(_render_annotation(arg) for arg in get_args(annotation))
    if origin is Literal:
        return ", ".join(f'"{arg}"' for arg in get_args(annotation))
    if origin is list:
        return "InvoiceItem[]"
    if origin is dict or annotation is dict:
        return "dict"
    if hasattr(annotation, "model_fields"):
        return '{"warnings": string[]}'
    return JSON_TYPE_ALIASES.get(annotation, str(annotation))


def _reference_tables(document: str) -> list[list[str]]:
    """The complete `| Field | JSON type | Direction | Notes |` tables, in document order.

    Selected by the full header rather than by the first cell, so the narrower
    `| Field | JSON type | Contract |` table of `fusion_*` fields is not mistaken for one
    of them.
    """
    selected = []
    for table in _tables(document):
        if not table:
            continue
        header = [cell.strip().strip("`").lower() for cell in _split_row(table[0])]
        if header[:4] == ["field", "json type", "direction", "notes"]:
            selected.append(table)
    return selected


def _reference_rows(table: list[str]) -> dict[str, tuple[str, str]]:
    """{field: (documented type, notes)} for one reference table."""
    rows: dict[str, tuple[str, str]] = {}
    for row in table[2:]:
        cells = _split_row(row)
        if len(cells) < 4:
            continue
        rows[cells[0].strip("`")] = (cells[1], cells[-1])
    return rows


def test_the_documented_json_type_matches_the_model_for_every_field():
    document = _read("README.md")
    tables = _reference_tables(document)
    assert len(tables) == 2, f"expected the two complete field reference tables, found {len(tables)}"

    for table, model in zip(tables, (ReconciliationRequest, InvoiceItem), strict=True):
        rows = _reference_rows(table)
        for name, field in model.model_fields.items():
            row_name = field.alias or name
            assert row_name in rows, (
                f"{model.__name__}.{name} is documented as {row_name!r} but the table has no "
                f"such row; it has {sorted(rows)}"
            )
            documented, _notes = rows[row_name]
            expected = _render_annotation(field.annotation)
            assert _normalise_type(documented) == expected, (
                f"{model.__name__}.{name} is documented as {_normalise_type(documented)!r} but the "
                f"model says {expected!r}"
            )


def test_the_documented_reference_tables_cover_each_model_exactly():
    document = _read("README.md")
    tables = _reference_tables(document)
    assert len(tables) == 2
    for table, model in zip(tables, (ReconciliationRequest, InvoiceItem), strict=True):
        # One row per field, named the way it appears on the wire, so an aliased field is
        # documented under its alias rather than its Python name.
        expected = {field.alias or name for name, field in model.model_fields.items()}
        assert set(_reference_rows(table)) == expected, (
            f"the reference table for {model.__name__} does not list exactly its fields, "
            f"each once, by wire name"
        )


BOUND_SPELLING_RE = re.compile(r"`(ge|le|max_length|min_length)=([^`]+)`")


def _normalise_bound_spelling(notes: str) -> str:
    """Rewrite the compact `ge=0.0` form to `ge` 0.0, so either phrasing satisfies a bound.

    Both are readable ways to write a bound in a notes column, and the test is about the
    bound being stated at all, not about which of the two spellings the author picked.
    """
    return BOUND_SPELLING_RE.sub(r"`\1` \2", notes)


def test_every_bound_a_model_declares_is_stated_in_the_reference():
    # A bound the reader cannot see is a bound they will trip over. `invoices` capped at
    # 2500 while no individual field was bounded at all is what this catches.
    document = _read("README.md")
    tables = _reference_tables(document)
    for table, model in zip(tables, (ReconciliationRequest, InvoiceItem), strict=True):
        rows = _reference_rows(table)
        for name, field in model.model_fields.items():
            notes = _normalise_bound_spelling(rows[field.alias or name][1])
            for bound, declared in _declared_bounds(field).items():
                assert f"`{bound}` {declared}" in notes, (
                    f"{model.__name__}.{name} declares {bound}={declared} and the reference "
                    f"notes do not say so: {notes!r}"
                )


def test_the_response_alias_is_documented_as_the_alias_fastapi_serialises():
    # FastAPI serialises by alias, so the key on the wire is `_meta` and the Python field
    # name `meta_extra` is only ever what a caller writes in their own code.
    rows = _reference_rows(_reference_tables(_read("README.md"))[0])
    assert "_meta" in rows, "the reference documents no _meta row"
    assert "meta_extra" not in rows, "the reference documents the Python name, not the wire name"


def test_the_status_table_lists_the_statuses_the_service_actually_returns():
    from fastapi.testclient import TestClient

    from src.main import app

    document = _read("README.md")
    section = next(
        table
        for table in _tables(document)
        if table and table[0].split("|")[1].strip().lower() == "status"
    )
    documented = {row.split("|")[1].strip().strip("`") for row in section[2:]}

    observed = set()
    with TestClient(app) as http:
        observed.add(str(http.get("/").status_code))
        observed.add(str(http.get("/health").status_code))
        observed.add(str(http.post("/v1/reconcile/batch", json={"confidence_score": 9}).status_code))
        # A payload the model rejects, and a payload the model accepts but cannot reconcile.
        observed.add(str(http.post("/v1/reconcile/batch", json={}).status_code))
    assert {"200", "422"} <= documented, f"the status table omits a status the service returns: {documented}"
    # 503 is the fail-closed refusal and 502 the upstream failure; both are load-bearing
    # contract states, so neither may be undocumented.
    for required in ("200", "422", "429", "500", "502", "503"):
        assert required in documented, f"the status table does not document {required}"
    assert observed <= documented | {"404", "405"}, (
        f"the service returns {sorted(observed - documented)} but the status table does not say so"
    )


def test_the_schema_fix_is_documented_as_a_number():
    # The regression this guards: fusion_invoice_amount shipping as a string behind a float
    # annotation. The example has to show a JSON number, and the prose has to say the coercion
    # is on assignment, or the next reader has no way to know the field is trustworthy.
    readme = _read("README.md")
    response_block = next(
        block for block in JSONC_BLOCK_RE.findall(readme) if '"fusion_customer_name"' in block
    )

    assert '"fusion_invoice_amount": 1234.56' in response_block
    assert '"fusion_invoice_amount": "1234.56"' not in response_block
    assert "validate_assignment" in readme
    assert "`number \\| null`" in readme, "the fusion_invoice_amount JSON type is not documented as a number"


def test_the_readme_does_not_claim_a_coverage_or_test_number_it_cannot_have():
    # These numbers are asserted by the gates, so the README quoting them is fine -- quoting
    # one that the gates would reject is not. The check is that the floor and the actual
    # measurement agree with each other.
    pyproject = tomllib.loads(_read("pyproject.toml"))
    floor = pyproject["tool"]["coverage"]["report"]["fail_under"]
    assert 0 < floor <= 100
    assert f"{floor} %" in _read("README.md") or f"**{floor} %**" in _read("README.md")


# ── the two floors: one number, stated identically everywhere ──────────────────────────────

# The defect this exists to stop is not one stale sentence, it is three documents carrying
# three different numbers for the same two facts. The README claimed 100.00 % and 294 tests,
# CONTRIBUTING claimed 99.71 % and 187, and the floor in pyproject.toml cited 99.71 % as the
# measured value. All of it was plausible, none of it agreed, and nothing failed.
#
# So the numbers are parsed rather than trusted, and the parse is deliberately strict: the
# marker is the literal "measured **X**, floor **Y**" pair, which appears exactly once per
# document. Rephrasing a bullet into prose makes the test fail with a message naming the
# file, which is the point -- a number nobody can find is a number nobody will update.
COVERAGE_CLAIM_RE = re.compile(r"measured \*\*(\d+(?:\.\d+)?) %\*\*, floor \*\*(\d+(?:\.\d+)?) %\*\*")
TEST_COUNT_CLAIM_RE = re.compile(r"measured \*\*(\d+)\*\*, floor \*\*(\d+)\*\*")

# The documents that have to agree. SECURITY.md carries no gate numbers and is not one of
# them; adding a fourth here is the point at which the guard starts paying for itself.
NUMBERED_DOCS = ("README.md", "CONTRIBUTING.md")


def _claims(document: str, pattern: re.Pattern[str]) -> list[tuple[str, str]]:
    return pattern.findall(document)


def test_the_documented_coverage_is_one_number_and_it_clears_the_floor():
    pyproject = tomllib.loads(_read("pyproject.toml"))
    report = pyproject["tool"]["coverage"]["report"]
    floor, precision = report["fail_under"], report["precision"]

    seen: dict[str, tuple[str, str]] = {}
    for name in NUMBERED_DOCS:
        found = _claims(_read(name), COVERAGE_CLAIM_RE)
        assert len(found) == 1, (
            f"{name} must state the coverage exactly once, as 'measured **X %**, floor **Y %**'; "
            f"found {found}"
        )
        seen[name] = found[0]

    distinct = set(seen.values())
    assert len(distinct) == 1, f"the documents disagree on the coverage figure: {seen}"

    measured, documented_floor = distinct.pop()
    assert documented_floor == str(floor), (
        f"the documents state a coverage floor of {documented_floor} % but fail_under is {floor}"
    )
    assert float(measured) >= floor, (
        f"the documents claim {measured} % measured coverage, which is below the {floor} % the "
        "gate enforces. Either the number is stale or the gate has been raised past it."
    )
    # A figure quoted with more decimals than `precision` produces are is a figure nobody
    # read off a coverage report, and precision is exactly what makes the floor comparable.
    assert len(measured.split(".")[-1]) <= precision, (
        f"the documents quote {measured} %, which has more decimals than precision = {precision}. "
        "coverage.py compares the rounded display value, so that is not a real measurement."
    )


def _min_tests() -> int:
    """The MIN_TESTS floor, read from the conftest source rather than imported.

    `import tests.conftest` would execute the module a second time under a different name --
    pytest already imported it as the top-level `conftest` -- and conftest has import-time
    side effects. The value is a single module-level integer, so the parse is unambiguous and
    a rename makes this fail rather than quietly report something else.
    """
    for node in ast.parse(_read("tests/conftest.py")).body:
        if isinstance(node, ast.Assign) and any(
            isinstance(target, ast.Name) and target.id == "MIN_TESTS" for target in node.targets
        ):
            return ast.literal_eval(node.value)
    raise AssertionError("tests/conftest.py no longer defines MIN_TESTS")


def test_the_documented_test_count_is_one_number_and_it_clears_the_floor():
    min_tests = _min_tests()

    seen: dict[str, tuple[str, str]] = {}
    for name in NUMBERED_DOCS:
        found = _claims(_read(name), TEST_COUNT_CLAIM_RE)
        assert len(found) == 1, (
            f"{name} must state the test count exactly once, as 'measured **X**, floor **Y**'; "
            f"found {found}"
        )
        seen[name] = found[0]

    distinct = set(seen.values())
    assert len(distinct) == 1, f"the documents disagree on the test count: {seen}"

    measured, documented_floor = distinct.pop()
    assert documented_floor == str(min_tests), (
        f"the documents state a floor of {documented_floor} tests but MIN_TESTS is {min_tests}"
    )
    assert int(measured) >= min_tests, (
        f"the documents claim {measured} tests collected, below the MIN_TESTS floor of {min_tests}"
    )


def test_the_documented_test_count_is_the_number_this_run_collected(request):
    # The static checks above prove the documents agree with each other and with the floors.
    # Only this one compares them to reality: a number copied faithfully into two documents
    # can still be a number that stopped being true three refactors ago.
    #
    # Skipped for a narrowed run on the same grounds as MIN_TESTS in conftest.py -- `-k`, `-m`
    # and `--collect-only` shrink the collection on purpose, and holding the documentation to a
    # deliberately partial run is how this kind of guard gets switched off.
    config = request.config
    if config.option.keyword or config.option.markexpr or config.option.collectonly:
        pytest.skip("narrowed run: the collected count is only meaningful for a full collection")

    collected = len(request.session.items)
    for name in NUMBERED_DOCS:
        measured = _claims(_read(name), TEST_COUNT_CLAIM_RE)[0][0]
        assert int(measured) == collected, (
            f"{name} says the suite collects {measured} tests; this run collected {collected}. "
            "Update the stated figure in README.md and CONTRIBUTING.md, and raise MIN_TESTS in "
            "tests/conftest.py in the same commit if the count went up."
        )


def test_the_readme_claims_no_coverage_exclusions_and_there_are_none():
    # The README states outright that this repository has no coverage-suppression directive
    # of any kind and no exclusions. That is a claim about the whole tree, and it is the kind
    # that decays: the two tomllib/tomli fallbacks carried one, and
    # `[tool.coverage.report].exclude_also` is an exclusion list that has been sitting in
    # pyproject.toml the whole time. A reader who trusts the sentence is reading a 100 % that
    # may have been bought.
    #
    # exclude_also is allowed to exist -- excluding `if TYPE_CHECKING:` and `__main__` guards
    # is ordinary hygiene, not a loophole. What is not allowed is for an entry to be *doing*
    # work, because that is what turns a measured number into a filtered one. So each pattern
    # is compiled and run against every line of every measured file: all of them have to miss.
    #
    # The needle is assembled rather than written out, because a literal here would make this
    # module its own first finding and the check would be permanently red.
    needle = "# " + "pragma" + ": no cover"
    assert needle not in Path(__file__).read_text(encoding="utf-8"), (
        "this module has stopped hiding its own needle; the scan below would find itself"
    )
    pragmas = [
        f"{path.relative_to(REPO_ROOT)}"
        for path in sorted(REPO_ROOT.rglob("*.py"))
        if ".venv" not in path.parts and "__pycache__" not in path.parts
        and needle in path.read_text(encoding="utf-8")
    ]
    assert not pragmas, (
        f"a coverage-suppression directive is present in {pragmas}, which contradicts the "
        "README. Either delete the directive or withdraw the claim -- do not leave them "
        "disagreeing."
    )

    measured = [
        path
        for directory in ("src", "api")
        for path in sorted((REPO_ROOT / directory).rglob("*.py"))
    ]
    patterns = tomllib.loads(_read("pyproject.toml"))["tool"]["coverage"]["report"].get("exclude_also", [])
    for pattern in patterns:
        compiled = re.compile(pattern)
        hits = [
            f"{path.relative_to(REPO_ROOT)}:{number}"
            for path in measured
            for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
            if compiled.search(line)
        ]
        assert not hits, (
            f"the coverage exclusion {pattern!r} suppresses {hits}. The README promises the "
            "number is measured, not filtered; delete the line, or withdraw the promise."
        )


def test_the_readme_does_not_document_environment_variables_that_do_not_exist():
    # A configuration table that has drifted from the code is the same defect as a blueprint
    # that has: it implies a knob that does nothing.
    from src.core.config import Settings

    declared = set(Settings.model_fields) | {
        "ORACLE_BIP_INVOICE_PATH",
        "ORACLE_BIP_RECEIPT_PATH",
        "BIP_CACHE_TTL_SECONDS",
    }
    listed = _first_cells(_tables(_section(_read("README.md"), "## Configuration")), "variable")
    listed = {name.upper() for name in listed}

    assert listed, "the configuration table was not found in the README"
    invented = sorted(listed - declared)
    assert not invented, f"README documents env vars no setting reads: {invented}"

    missing = sorted(declared - listed)
    assert not missing, f"the README does not document {missing}, which the code reads"


def test_the_readme_and_the_processing_rules_agree_on_the_tier_list():
    # Both documents enumerate the matching tiers. If one gains a tier and the other does
    # not, a reader of the wrong document is misled about how the engine decides a match.
    readme = _read("README.md")
    rules = _read("docs/report_processing_rules.md")
    section = _section(readme, "## The tiered invoice-matching algorithm")

    readme_tiers = re.findall(r"^###\s+(\d+)\.\s+(.*)$", section, re.M)
    assert readme_tiers, "the README tier list is gone; the processing rules and this test need updating"

    numbers = [int(number) for number, _ in readme_tiers]
    # Tier 0 is the mapped-row exclusion, so the list starts at 0 and has no gaps.
    assert numbers[0] == 0, f"the README tier list must start at 0, found {numbers[0]}"
    assert numbers == list(range(numbers[0], numbers[0] + len(numbers))), f"gaps in the README tier list: {numbers}"

    # The rules express the same tiers as an ordered list, phrased with digits where the
    # README spells them out, so the two are compared after normalising that. Scoped to the
    # matching section: the document has other ordered lists (the architecture, the receipt
    # fallbacks) that have nothing to do with the tiers.
    rule_titles = re.findall(
        r"^\d+\.\s+\*\*(.+?)\*\*",
        _section(rules, "## Invoice Matching Logic (Tiered Fallback)"),
        re.M,
    )
    assert len(rule_titles) == len(numbers) - 1, (
        f"the README has {len(numbers) - 1} matching tiers and the processing rules have "
        f"{len(rule_titles)}: {rule_titles}"
    )

    def normalise(text: str) -> str:
        # Lower-cased first, so "3-Way" and "three-way" normalise to the same phrase.
        lowered = text.lower()
        for digit, word in (("1", "one"), ("2", "two"), ("3", "three"), ("4", "four")):
            lowered = lowered.replace(f"{digit}-way", f"{word}-way")
        return re.sub(r"[^a-z -]", "", lowered).strip()

    for (_number, title), rule_title in zip(readme_tiers[1:], rule_titles, strict=True):
        # "Three-way match — number + date + amount *(highest confidence)*" is compared on its
        # leading phrase. The dash is matched as a character class rather than a literal so the
        # test does not depend on which dash codepoint the file happens to use.
        keyword = normalise(re.split(r"[\u2014\u2013\u2010]", title)[0])
        assert keyword in normalise(rule_title), (
            f"the processing rules describe the tier as '{rule_title}', which the README "
            f"calls '{title}'"
        )

    # Tier 0 is README framing for the per-row `mapped` flag, which the rules describe in
    # prose rather than as a numbered tier.
    assert "`mapped`" in rules, "the processing rules no longer explain the per-row mapped flag"
