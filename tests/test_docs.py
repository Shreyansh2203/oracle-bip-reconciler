"""The documentation, asserted rather than trusted.

Two things drift silently here. A field is added to a Pydantic model and the README keeps
describing the old surface, so a caller reads a field that no longer exists or misses one that
does. A relative link is renamed or deleted and the README keeps pointing at it, which is
only discovered by whoever follows the link next. Both are cheap to check and neither shows
up in review, so they are checked here.

Extending tests/test_deploy_contract.py would have buried these among the deployment
assertions, where a reader would not think to look. Nothing here reaches the network.
"""

import re
import sys
from pathlib import Path

import pytest

if sys.version_info >= (3, 11):
    import tomllib
else:  # pragma: no cover - the pinned toolchain is 3.12
    import tomli as tomllib

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



def test_every_documented_field_exists_on_a_model():
    # A field documented here but not on a model is the worst kind of documentation error:
    # it is wrong, and a caller who trusts it writes code against a field that is silently
    # discarded.
    documented = _documented_fields(_read("README.md"))
    real = set(ReconciliationRequest.model_fields) | set(InvoiceItem.model_fields)

    invented = sorted(documented - real)
    assert not invented, f"README.md documents fields that do not exist: {invented}"


def test_every_model_field_is_documented():
    # The other direction: a field that exists and is populated but is not in the reference
    # is a field nobody can rely on, because they have no way to know it is there.
    documented = _documented_fields(_read("README.md"))

    undocumented = sorted(set(ReconciliationRequest.model_fields) - documented)
    assert not undocumented, f"ReconciliationRequest fields missing from the README: {undocumented}"

    undocumented = sorted(set(InvoiceItem.model_fields) - documented)
    assert not undocumented, f"InvoiceItem fields missing from the README: {undocumented}"


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

    for (_number, title), rule_title in zip(readme_tiers[1:], rule_titles):
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
