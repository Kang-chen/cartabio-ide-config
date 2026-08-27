#!/usr/bin/env python3
"""Gate a Biomni skill package before it ships.

Stdlib only. Two modes:

    check_skill.py <package-dir> [--contract A|B] [--require-run-receipt]
    check_skill.py --measure <skills-dir>      # fire rates for every rule over a corpus

Exit codes: 0 pass | 1 blocking finding | 2 warnings only | 3 a check degraded (never a pass).
1 outranks 3: a real failure is more actionable than "some rules did not run".

Severity is drawn on FALSE-POSITIVE RISK, not importance. Byte-exact and structural checks FAIL;
every heuristic WARNs. A check that fires on a large fraction of known-good packages gets deleted by
the first person who runs it, taking its real catches with it.

Use --measure against any directory of packages to see each rule's fire rate before trusting it.
"""

from __future__ import annotations

import argparse
import ast
import collections
import importlib.util
import json
import pathlib
import re
import sys

sys.dont_write_bytecode = True

# --- the contract -------------------------------------------------------------------------------

CANONICAL_KEY_ORDER = ("id", "name", "description", "category", "visibility", "starting-prompt")
REQUIRED_KEYS = set(CANONICAL_KEY_ORDER[:-1])          # legacy contract; evidence-v1 requires prompt
SYSTEM_VISIBILITIES = {"internal", "public", "shared"}
ID_RE = re.compile(r"^skill_[0-9a-f]{32}$")
SLUG_RE = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")
FM_LINE_RE = re.compile(r"^([A-Za-z0-9_-]+):\s*(.*)$")  # mirrors the CI validator's per-line parse
# The quoting grammar that per-line parse cannot see. Deliberately YAML 1.2's whole escape set, not
# JSON's, so a FAIL only fires on a value no parser would load — never on legal-but-unusual escaping.
# A raw tab is printable YAML inside a quoted scalar; the rest of C0 and DEL are not.
FM_DQ_RE = re.compile(r'"(?:[^"\\\x00-\x08\x0a-\x1f\x7f]'
                      r'|\\(?:[0abtnvfre "/\\N_LP\t]|x[0-9a-fA-F]{2}|u[0-9a-fA-F]{4}'
                      r'|U[0-9a-fA-F]{8}))*"')
FM_SQ_RE = re.compile(r"'(?:[^'\x00-\x08\x0a-\x1f\x7f]|'')*'")

CATEGORIES = {
    "data_analysis", "data_discovery", "drug_discovery", "epigenomics", "experimental_design",
    "functional_analysis", "functional_genomics", "general", "genomics_genetics", "integration",
    "literature", "molecular_design", "multi_omics", "pathway_analysis",
    "proteomics_metabolomics", "reporting", "transcriptomics",
}

# Legacy Step-5 outcomes retained so pre-style-contract packages remain reviewable.
RECEIPT_KEYS = ("bundled_files_ran", "outputs_appeared", "report_at_results_root",
                "figures_present_and_nonblank", "report_branded")

# Must equal report_qc.RECEIPT_SCHEMA. report_qc is copied into every generated skill and has to run
# standalone, so it cannot import this file; a cross-file test asserts the two strings agree. The
# marker is what separates a receipt the QC module produced from five booleans someone typed.
RECEIPT_SCHEMA = "phylo-run-receipt/1"
RECEIPT_SCHEMA_V2 = "phylo-run-receipt/2"
RECEIPT_SCHEMA_V3 = "phylo-run-receipt/3"
EVIDENCE_SCHEMA = "phylo-skill-evidence/1"
QC_RUN_LOG_SCHEMA = "phylo-qc-run-log/1"
STYLE_SOURCE_PREFIXES = (
    "/mnt/skills/system/",
    "/mnt/skills/user/",
    "/mnt/skills/personal/",
)
STYLE_SOURCE_KINDS = {
    "provider_profile": "assets/report_style.json",
    "installed_skill_markdown": "SKILL.md",
}
STYLE_DERIVATION_SCHEMA = "biomni-report-style-derivation/1"
TRANSCRIPT_RELATIVE_PATH = "execution_trace/transcript.jsonl"
SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
EVIDENCE_RECEIPT_KEYS_V2 = (
    "execution_contract_satisfied", "outputs_appeared", "report_at_results_root",
    "figure_contract_satisfied", "facts_artifact_verified", "report_branded",
    "text_extracted", "pages_rendered",
    "visual_review_attested", "report_sections_present", "source_assertions_verified",
    "infographic_lineage_verified",
)
EVIDENCE_RECEIPT_KEYS = (
    "execution_contract_satisfied", "outputs_appeared", "report_at_results_root",
    "figure_contract_satisfied", "facts_artifact_verified", "report_style_verified",
    "text_extracted", "pages_rendered",
    "visual_review_attested", "report_sections_present", "source_assertions_verified",
    "infographic_lineage_verified",
)
REPORT_RECEIPT_KEYS = {
    "report_at_results_root", "report_style_verified", "text_extracted", "pages_rendered",
    "visual_review_attested", "report_sections_present",
}
REPORT_RECEIPT_KEYS_V2 = {
    "report_at_results_root", "report_branded", "text_extracted", "pages_rendered",
    "visual_review_attested", "report_sections_present",
}

# The embedding verdict is a tri-state, not a boolean, and it is deliberately NOT in RECEIPT_KEYS:
# those are outcomes that must be the literal true, and "the check could not run" is a third answer
# that a boolean cannot carry. Must match report_qc's states; a cross-file test asserts it.
EMBED_STATES = ("pass", "fail", "not_evaluable", "not_applicable")

# The platform validator hard-errors on .DS_Store and __pycache__ only. The tool caches are here
# because the whole package tree is rglobbed and staged to S3, so a cache directory left behind by a
# local lint or test run ships with the skill. One was found in this package during review.
CACHE_NAMES = {".DS_Store", "__pycache__", ".ruff_cache", ".pytest_cache", ".mypy_cache", ".ipynb_checkpoints"}
# macOS tar writes AppleDouble sidecars (._foo) beside every file. They are invisible in
# Finder, they ship to S3, and no platform validator rejects them.
APPLEDOUBLE_PREFIX = "._"
CACHE_SUFFIXES = {".pyc", ".pyo"}
STRAY_NAMES = {"uv.lock", "pyproject.toml", "poetry.lock", ".venv", ".pixi", "node_modules"}

_HERE = pathlib.Path(__file__).resolve().parent
_SENTENCE_FILE = _HERE.parent / "assets" / "contract" / "delegation_sentence.txt"
_LEGACY_SENTENCE_FILE = _HERE.parent / "assets" / "contract" / "delegation_sentence_legacy.txt"

FORBIDDEN = [
    ("RC007a", "When the user requests a PDF report"),
    ("RC007b", "if pdf-report-generation"),
]

BUNDLED_RE = re.compile(r"(?<![\w/.])((?:scripts|references|assets|templates)/[A-Za-z0-9_./-]+)")
SUBFOLDER_PDF_RE = re.compile(r"/mnt/results/[A-Za-z0-9_<>-]+/[A-Za-z0-9_<>.-]*\.pdf")
# An import names a bundled file too, and BUNDLED_RE's path prefixes cannot see it.
FENCE_PY_RE = re.compile(r"^ {0,3}```(?:python|py)\b[^\n]*\n(.*?)^ {0,3}```", re.S | re.M)
IMPORT_RE = re.compile(r"^\s*(?:from|import)\s+([A-Za-z_][A-Za-z0-9_]*)", re.M)

# A numbered workflow step, and which of them actually analyse something. Loading, exporting and the
# terminal report step have nothing of their own to plot, and counting them warns on correct packages.
# "Generate figures" is here because it is not a scientific step either: it renders what an earlier
# step computed, so demanding a figure of it inflates the count by one.
#
# Module scope because scaffold_skill.py --figures-from-steps derives its rows from the same two
# patterns: two spellings of "which steps need a figure" is exactly the drift this package exists to
# catch, so the scaffolder loads these rather than restating them.
STEP_TITLE_RE = re.compile(r"^(?:\*\*|#{3,4}\s*)Step\s+(\d+)\s*[—–-]\s*([^\n*]*)", re.M)
NON_ANALYSIS = re.compile(
    r"load|import|read|validat|export|final report|deliver|report|figure|plot|visuali", re.I)

STYLED_GATE = "assert_report_styled"
# write_receipt() runs the style gate as one of the outcomes it records, so a terminal step calling it
# is wired just as surely as one naming the gate directly. The
# DEF exclusion below still applies to both: report_qc.py defines the gate, so a call inside it never
# credits the package, which is what keeps this rule failable.
RECEIPT_WRITER = "write_receipt"
STYLED_CALL_RE = re.compile(rf"\b(?:{STYLED_GATE}|{RECEIPT_WRITER})\s*\(")
STYLED_DEF_RE = re.compile(rf"^\s*def\s+{STYLED_GATE}\b", re.M)
EVIDENCE_MARKER = "<!-- contract: evidence-v1 -->"
_EVIDENCE_GATE = _HERE / "evidence_contract.py"

Finding = collections.namedtuple("Finding", "rule severity message")

EXPLAIN = {
    "FM001": "id must match ^skill_[0-9a-f]{32}$ — a fresh token, never derived from the slug.",
    "FM002": "name must equal the containing folder name exactly.",
    "FM003": "Frontmatter keys must be drawn only from the six allowed keys.",
    "FM004": "Keys must appear in canonical order. This is a hard CI error that no repo doc states.",
    "FM005": "id, name, description, category, visibility are required and non-empty.",
    "FM006": "visibility must be internal|public|shared. 'private' is legal at runtime and fails CI.",
    "FM007": "starting-prompt, if present, must be non-empty and must not end ' . .'.",
    "FM008": "category should be one of the 17 live values.",
    "FM009": "Single-line double-quoted values only. CI parses frontmatter with a per-line regex, so a "
             "YAML block scalar (| or >) passes CI and silently degrades every repo-side reader.",
    "FM010": "description over 500 chars risks truncation in runtime catalogs — the skill may never trigger.",
    "FM011": "A quoted value must be a well-formed single-line YAML scalar. An unescaped \" or a "
             "dangling backslash satisfies the per-line regex above and every static check in CI, "
             "then the platform's real YAML loader refuses the skill. Grammar, not heuristic, so it "
             "FAILs. The scaffolder escapes correctly; this rule is what covers a hand-written file. "
             "Accepts YAML 1.2's whole escape set, so it fires on nothing 486 shipped values do.",
    "PK001": f"No {', '.join(sorted(CACHE_NAMES))}, *.pyc or *.pyo anywhere in the package. The "
             "platform validator rejects only .DS_Store and __pycache__; the rest are here because the "
             "tree is staged to S3 whole, so a cache left by a local lint or test run ships with it.",
    "PK002": "The whole tree ships to S3. Lockfiles, venvs and node_modules must not be in a skill folder.",
    "PK003": "Exactly one SKILL.md, at the package root.",
    "PK004": "SKILL.md length. No platform limit exists; over ~400 lines agents skim.",
    "BF001": "Every scripts|references|assets|templates/ path named in SKILL.md must exist on disk.",
    "BF002": "A module imported in a fenced python block must be shipped. BF001 matches only "
             "prefixed paths, so `from report_qc import ...` with no report_qc.py on disk is "
             "invisible to it — that blindness is why a guidance package carrying a borrowed report "
             "block passed. Narrow on purpose: only modules templates/ can ship are checked.",
    "RC001": "The delegation sentence must appear exactly twice (whitespace-normalised).",
    "RC004": "A report filename matching report_<slug>.pdf must appear in the package.",
    "RC005": "No subfolder PDF path anywhere — the report goes to the results root.",
    "RC007": "Forbidden phrasing that makes the mandated report conditional.",
    "RC008": f"The terminal step must CALL {STYLED_GATE}() or write_receipt(). RC001 checks that "
             f"the generated skill resolves a default or explicitly selected compatible style "
             f"provider; prose does not prove the finished artifact used that provider's markers. "
             f"A hand-styled PDF passes assert_report_exists, report_embeds_figures and the "
             f"provenance gate alike. Twin of "
             f"BF002: a gate that is named but never wired is documentation. Evidence is a call in a "
             f"python fence or in a shipped script, never in the module that DEFINES the gate — "
             f"report_qc.py is copied into every generated package, so crediting the "
             f"definition would make this rule unfailable.",
    "CV001": "Each caveat must name an artifact field or a number. An unbound caveat is prose, not a gate.",
    "TF001": "Unresolved TODO(author) markers mean the interview is incomplete. Answer the question "
             "or delete the whole section — deleting only the marker hides an unanswered question.",
    "ST001": "Copied boilerplate from a deprecated authoring guide. Write the section instead.",
    "ST002": "Copied boilerplate from a deprecated authoring guide. Write the section instead.",
    "ST003": "Copied boilerplate that contradicts report delegation.",
    "FG001": "Every numbered analysis step should name one representative figure showing its "
             "result. WARN, not FAIL: some steps genuinely have nothing to plot, so the rule "
             "sets the default and the author states the exception.",
    "FG002": "Declared figures should be read into the report from the facts artifact, so the "
             "report cannot claim a figure it never produced or silently drop one.",
    "FG003": "Figures belong under figures/. Only the report itself and GenerateImage "
             "schematics land at the results root.",
    "PK005": "macOS AppleDouble sidecars (._*) ship to S3 and no platform validator rejects them. Build archives with COPYFILE_DISABLE=1.",
    "RR001": f"run_receipt.json must record every schema-required outcome as the boolean true. An "
             f"absent key is not a pass, and a truthy non-boolean is not proof. Report style is a "
             f"claim about the artifact, so it is answered from the selected provider's marker "
             f"contract and the finished PDF (RC008), never merely by loading a styling skill.",
    "RR002": f"The receipt must be report_qc.write_receipt()'s output, carrying schema "
             f"{RECEIPT_SCHEMA!r} and an `evidence` entry for every outcome recorded true. RR001 "
             f"checks the five booleans are all there and all true; it cannot tell a measurement from "
             f"a claim, and the package used to print a copy-pasteable all-true block in the very "
             f"step that told the run to record one — so the cheapest way to pass was to paste it. "
             f"Evidence means the artifact the outcome was decided from: a resolved path and byte "
             f"count, the provider markers read out of the PDF, the transcript record that matched. "
             f"This raises the cost of a false receipt; it does not make one impossible, and it "
             f"should not be described as proof against an author willing to write the JSON by hand.",
    "RR003": f"figures_embedded records whether the declared figures reached the PDF, as one of "
             f"{', '.join(EMBED_STATES)}. It is separate from figures_present_and_nonblank because "
             f"the two are checked to different strengths: the artifacts are proved with the stdlib, "
             f"the embedding needs pypdf and may not be evaluable. One boolean used to answer for "
             f"both and came back true while embedding had not been evaluated at all. 'fail' blocks "
             f"— the check ran and disagreed. 'not_evaluable' warns rather than blocks, because "
             f"blocking would make the receipt unobtainable wherever pypdf is absent, and a gate "
             f"nobody can pass is one somebody deletes. Note what the passing case does and does not "
             f"prove: it counts embedded images against the declared figure count and does NOT match "
             f"figure identity. Identity matching is not attempted; a V1 heuristic that says what it "
             f"is beats a V2 promise that is not kept.",
    "RC009": f"The {RECEIPT_WRITER}() call in the terminal step must name real files: leaving "
             f"bundled_files or outputs as [] (or omitting them) makes those outcomes unprovable, "
             f"and `[...]` is worse than unprovable — it is a list containing Ellipsis, valid Python "
             f"that passes every static check here and then raises TypeError inside the call. The "
             f"scaffolder emits both lists empty with a TODO(author) beside them; this is what still "
             f"blocks once the marker is deleted, which TF001's own message warns people not to do. "
             f"Only the known-bad forms are rejected: a variable or any non-empty literal passes.",
    "RC010": "The report must be rendered to a fresh workspace file and published with "
             "staged_copy(). Directly reopening an existing PDF on the object-backed results mount "
             "can fail because the mount does not support ordinary truncation semantics.",
    "RC011": "When facts are required, the generated runtime must bind its figure inventory, derive "
             "the complete payload from the contract-declared artifact, and call "
             "write_facts_from_artifact() before the receipt. A facts promise without this ordered "
             "executable writer is not a deliverable contract.",
    "RC012": "When figures apply, the generated runtime must assign figures from assert_figures() "
             "before passing that value to write_receipt(). A Figures section alone cannot make an "
             "undefined runtime variable valid.",
    "OP001": "An analysis workflow must name the machine-readable result file it writes — a "
             ".csv/.tsv/.parquet/.rds and not report_facts.json, which carries the report's numbers "
             "rather than the result. The scaffolder used to invent results_<slug>.csv for every "
             "package regardless of what its run produced, promising an artifact the workflow may "
             "never write; the requirement is real, so it is checked here, and the filename is the "
             "author's because only the run knows its shape.",
    "LC001": "A licence section that claims 'permissive-licensed sources only' (or similar) and names "
             "no dataset, package, URL or licence is making an assertion, not a check. Name what the "
             "claim covers. WARN because it fires on shipped packages whose licence prose is "
             "otherwise fine — and because the alternative was leaving SKILL.md advertising a "
             "licence gate that did not exist.",
    "DG001": "A rule's data did not load, so the rule matched nothing and the package was never "
             "held to it. Repair this authoring package — assets/contract/known_stanzas.json feeds "
             "ST001-ST003, templates/*.py feeds BF002. Exits 3, because a check that quietly "
             "disables itself is the one failure mode this whole gate exists to prevent.",
}

_EV_EXPLAINS = {
    "EV001": "Requires the versioned skill_contract.json and readable schema.",
    "EV002": "Separates a short user-facing research question from the internal subject, objective, decision context, and PDF deliverable contract.",
    "EV003": "Warns when a syntactically complete starting prompt still uses vague stand-ins.",
    "EV004": "Requires an explicit facts-artifact decision and a reason when facts do not apply.",
    "EV005": "Binds headline facts, accounting partitions, and known-answer evaluations.",
    "EV006": "Requires computation-critical source assertions or a justified non-applicability decision.",
    "EV007": "Binds each source assertion to a runtime artifact witness.",
    "EV008": "Maps every single- or multi-select clarification choice to implementation, outputs, and evals.",
    "EV009": "Separates validated runtime inputs, steps, caveats, sources, and materials from author interview prose.",
    "EV010": "Allows catalog claims only for capabilities with implementation and evaluation evidence.",
    "EV011": "Records auto and guided validation independently, including branch coverage.",
    "EV012": "Requires design-identifiability gates when statistical inference applies.",
    "EV013": "Bounds external services and requires tested failure fixtures without crashing on malformed entries.",
    "EV014": "Requires PDF review plus explicit figure and bundled-execution applicability.",
    "EV015": "Prevents maturity labels from outrunning completed validation.",
    "EV016": "Offers a private preview while keeping Personal Skill registration behind explicit confirmation outside the package.",
    "EV017": "Keeps deliverable applicability and derived SKILL/eval/source projections synchronized.",
}
EXPLAIN.update(_EV_EXPLAINS)

# Each FORBIDDEN phrase reports under its own id, so each needs its own --explain entry. Derived
# rather than restated: hand-written entries for RC007a/RC007b were simply missing, so `--explain
# RC007a` answered "no such rule" about a rule that fires. Deriving them means adding a phrase to
# FORBIDDEN documents it too, and the coverage test below can never go stale.
for _rid, _phrase in FORBIDDEN:
    EXPLAIN.setdefault(_rid, f"{EXPLAIN['RC007']} This id covers the phrase {_phrase!r}.")


# --- helpers ------------------------------------------------------------------------------------

def normalise(text: str) -> str:
    return " ".join(text.split())


def load_stanzas() -> tuple[list[dict], str]:
    """Boilerplate blocklist, and why it is empty. An empty blocklist matches nothing, so ST001-ST003
    would then pass every package; the reason comes back so the caller can say so instead."""
    p = _HERE.parent / "assets" / "contract" / "known_stanzas.json"
    try:
        import json
        stanzas = [s for s in json.loads(p.read_text(encoding="utf-8")).get("stanzas", [])
                   if s.get("text") and s.get("id")]
    except (ValueError, OSError) as exc:
        return [], f"{p} did not load ({type(exc).__name__})"
    return stanzas, "" if stanzas else f"{p} lists no usable stanza"


def template_modules() -> tuple[set[str], str]:
    """Modules templates/ can copy into a generated package, and why the set is empty. Read from disk,
    never listed literally, so the rule stays true as templates are added — which also means BF002 is
    only ever as complete as templates/, and checks nothing at all once it is empty."""
    d = _HERE.parent / "templates"
    if not d.is_dir():
        return set(), f"{d} is absent"
    mods = {p.stem for p in d.glob("*.py")}
    return mods, "" if mods else f"{d} ships no python module"


def degradations() -> list[Finding]:
    """Rules whose data did not load. Unfalsifiable is not clean, so this is a finding, not a log line."""
    out: list[Finding] = []
    for rules, (_, why) in (("ST001-ST003", load_stanzas()), ("BF002", template_modules())):
        if why:
            out.append(Finding("DG001", "DEGRADED",
                               f"{rules} never ran: {why} — the package was not held to them"))
    return out


def evidence_module():
    """Load the sibling validator without creating a cache inside a package."""
    if not _EVIDENCE_GATE.is_file():
        return None, f"{_EVIDENCE_GATE} is absent"
    try:
        spec = importlib.util.spec_from_file_location("_evidence_contract", _EVIDENCE_GATE)
        mod = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(mod)
    except (ImportError, OSError, AttributeError) as exc:
        return None, f"{_EVIDENCE_GATE} did not load ({type(exc).__name__})"
    return mod, ""


def receipt_reason(data: dict, key: str) -> str:
    """A false flag without a reason is a finding nobody can act on."""
    return data.get(f"{key}_reason") or data.get("reason") or "no reason recorded"


def style_receipt_errors(report_policy: dict, style: object) -> list[str]:
    """Validate the provider/selection binding carried by a style-aware receipt."""
    if not isinstance(style, dict):
        return ["report_style_verified evidence must be an object"]
    errors = []
    provider = style.get("provider")
    default_provider = report_policy.get("default_style_provider")
    if style.get("contract_default_provider") != default_provider:
        errors.append("report style evidence disagrees with the contract default provider")
    if not isinstance(provider, str) or not SLUG_RE.fullmatch(provider):
        errors.append("report style evidence has no valid provider slug")
    source = style.get("style_source") if isinstance(style.get("style_source"), dict) else {}
    source_kind = source.get("kind")
    source_path = source.get("path")
    expected_suffix = STYLE_SOURCE_KINDS.get(source_kind)
    if expected_suffix is None:
        errors.append("report style evidence has an invalid installed provider source kind")
    if not isinstance(source_path, str) or not source_path.startswith(STYLE_SOURCE_PREFIXES):
        errors.append("report style evidence does not name an installed provider source")
    elif isinstance(provider, str) and expected_suffix and not source_path.endswith(
        f"/{provider}/{expected_suffix}"
    ):
        errors.append("report style evidence source path disagrees with its provider slug")
    if not isinstance(source.get("sha256"), str) or not SHA256_RE.fullmatch(
        source["sha256"]
    ):
        errors.append("report style evidence has no valid provider source hash")
    if not isinstance(source.get("bytes"), int) or isinstance(source.get("bytes"), bool) or (
        source["bytes"] <= 0
    ):
        errors.append("report style evidence has no positive provider source byte count")
    if source.get("derivation_schema") != STYLE_DERIVATION_SCHEMA:
        errors.append("report style evidence has an invalid provider derivation schema")
    if not isinstance(source.get("marker_set_sha256"), str) or not SHA256_RE.fullmatch(
        source["marker_set_sha256"]
    ):
        errors.append("report style evidence has no valid derived marker-set hash")

    selection = style.get("selection")
    if selection == "contract_default":
        if provider != default_provider:
            errors.append("contract-default style evidence does not use the contract default")
        if style.get("activation") != "default":
            errors.append("contract-default style evidence does not use a default-eligible provider")
        if isinstance(source_path, str) and not source_path.startswith("/mnt/skills/system/"):
            errors.append("contract-default style evidence must come from the system skill root")
        if "selection_evidence" in style:
            errors.append("contract-default style evidence must not claim an enterprise selection")
    elif selection == "explicit_override":
        if provider == default_provider:
            errors.append("explicit style evidence redundantly names the contract default")
        if style.get("activation") != "explicit_only":
            errors.append("explicit style evidence does not use an explicit-only provider")
        selection_evidence = style.get("selection_evidence")
        if not isinstance(selection_evidence, dict):
            errors.append("explicit style evidence has no immutable user-selection record")
        else:
            if selection_evidence.get("source") != "user_message":
                errors.append("explicit style selection was not derived from a user message")
            if selection_evidence.get("transcript_path") != TRANSCRIPT_RELATIVE_PATH:
                errors.append("explicit style selection does not name the fixed execution transcript")
            for key in ("transcript_sha256", "message_sha256"):
                if not isinstance(selection_evidence.get(key), str) or not SHA256_RE.fullmatch(
                    selection_evidence[key]
                ):
                    errors.append(f"explicit style selection has no valid {key}")
            locator = selection_evidence.get("message_locator")
            if (
                not isinstance(locator, dict)
                or locator.get("kind") not in {"id", "index"}
                or not isinstance(locator.get("value"), str)
                or not locator["value"].strip()
            ):
                errors.append("explicit style selection has no immutable message locator")
            if not isinstance(selection_evidence.get("matched_alias"), str) or not (
                selection_evidence["matched_alias"].strip()
            ):
                errors.append("explicit style selection has no matched provider alias")
            allowed_selection_keys = {
                "source", "transcript_path", "transcript_sha256", "message_locator",
                "message_sha256", "matched_alias",
            }
            if set(selection_evidence).difference(allowed_selection_keys):
                errors.append("explicit style selection evidence contains unapproved message data")
    else:
        errors.append("report style evidence has an invalid selection state")
    return errors


def sentence(*, evidence: bool = True) -> str:
    source = _SENTENCE_FILE if evidence else _LEGACY_SENTENCE_FILE
    if source.exists():
        return normalise(source.read_text(encoding="utf-8"))
    return normalise(
        "Generate the PDF report with `pdf-report-generation` by default. When the user explicitly "
        "selects a compatible report-styling skill, use that provider instead for presentation only; "
        "keep every report, evidence, artifact, infographic, and review requirement unchanged. "
        "Include a Biomni GenerateImage infographic when required, task context, methods or sources, "
        "results, conclusions, figures where applicable, references, and next steps"
    )


def split_frontmatter(text: str):
    """Return (ordered_keys, values, body, raw_block) mirroring the CI validator's line parse."""
    lines = text.splitlines()
    if not lines or lines[0].strip() != "---":
        return None, {}, text, []
    for i in range(1, len(lines)):
        if lines[i].strip() == "---":
            raw = lines[1:i]
            keys, values = [], {}
            for line in raw:
                m = FM_LINE_RE.match(line)
                if m:
                    keys.append(m.group(1))
                    values[m.group(1)] = m.group(2).strip()
            return keys, values, "\n".join(lines[i + 1:]), raw
    return None, {}, text, []


def unquote(v: str) -> str:
    v = v.strip()
    if len(v) >= 2 and v[0] == v[-1] == '"':
        try:
            return ast.literal_eval(v)
        except (SyntaxError, ValueError):
            return v[1:-1]
    if len(v) >= 2 and v[0] == v[-1] == "'":
        return v[1:-1].replace("''", "'")
    return v


ARCHETYPES = {
    "analysis-workflow", "evidence-synthesis", "protocol-workflow",
    "correctness-guidance", "format-utility", "meta-tooling",
}
DECLARED_RE = re.compile(r"<!--\s*archetype:\s*([a-z-]+)\s*-->")


def derive_archetype(pkg: pathlib.Path, body: str) -> str:
    """Report rules apply only to analysis-workflow.

    Prefer the package's own declaration; the scaffolder emits it. Derivation is a fallback for
    legacy packages, and it must be conservative in the direction of NOT claiming
    analysis-workflow: a skill that merely *discusses* reports (this package does) is not one that
    produces one. The discriminator is an Outputs section naming a concrete deliverable.
    """
    m = DECLARED_RE.search(body)
    if m and m.group(1) in ARCHETYPES:
        return m.group(1)

    scripts = list((pkg / "scripts").glob("*.*")) if (pkg / "scripts").is_dir() else []

    out = re.search(r"^#{2,3}\s*(Outputs|Deliverables)\b.*$", body, re.M | re.I)
    concrete = False
    if out:
        seg = body[out.end():]
        nxt = re.search(r"^#{2,3}\s", seg, re.M)
        seg = seg[: nxt.start()] if nxt else seg
        concrete = bool(re.search(r"[\w<>-]+\.(pdf|csv|rds|h5ad|xlsx|png|svg)\b", seg))

    if concrete and scripts:
        return "analysis-workflow"
    if concrete and len(re.findall(r"^#{2,4}\s*(?:Step\s*)?\d+[.)\s]", body, re.M)) >= 3:
        return "analysis-workflow"
    if not scripts and len(body.splitlines()) < 150:
        return "correctness-guidance"
    return "other"


def wires_styled_gate(pkg: pathlib.Path, body: str) -> bool:
    """Does anything in the package actually CALL the report-style gate?

    Evidence is a call in a fenced python block or in a shipped script — never in the module that
    DEFINES it. report_qc.py is copied into every generated package, so once it carries the
    gate its definition (and its own usage docstring) sits in all of them: crediting that would make
    this rule unfailable, which is the defect the whole gate exists to prevent. Prose does not count
    either — a mention is not a call.
    """
    if any(STYLED_CALL_RE.search(b) for b in FENCE_PY_RE.findall(body)):
        return True
    for d in ("scripts", "templates"):
        sub = pkg / d
        if not sub.is_dir():
            continue
        for p in sorted(sub.rglob("*.py")):
            try:
                src = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if not STYLED_DEF_RE.search(src) and STYLED_CALL_RE.search(src):
                return True
    return False


def strip_code(text: str) -> str:
    """Drop fenced blocks and inline code so a rule that QUOTES a forbidden phrase is not a hit."""
    text = re.sub(r"```.*?```", " ", text, flags=re.S)
    return re.sub(r"`[^`\n]*`", " ", text)


def blank_fences(text: str) -> str:
    """Blank out fenced blocks while PRESERVING line count, for line-anchored section scans.

    A skill that shows an example '## Figures' table inside a fence must not be credited with having
    a Figures section — same false positive as quoting a forbidden phrase, but line-anchored regexes
    need the line structure intact, so this cannot use strip_code().
    """
    out, in_fence = [], False
    for line in text.splitlines():
        if line.lstrip().startswith("```"):
            in_fence = not in_fence
            out.append("")
            continue
        out.append("" if in_fence else line)
    return "\n".join(out)


def report_contract_findings(pkg: pathlib.Path, text: str, body: str, *, evidence: bool,
                             contract_data: dict | None = None) -> list[Finding]:
    """Apply the declared deliverable and receipt contract from one implementation."""
    findings: list[Finding] = []
    policy = (contract_data or {}).get("deliverable_policy", {})
    report_required = not evidence or (
        isinstance(policy, dict)
        and isinstance(policy.get("report"), dict)
        and policy["report"].get("required") is True
    )
    expected = sentence(evidence=evidence)
    count = normalise(text).count(expected)
    required_count = 2 if report_required else 0
    if count != required_count:
        findings.append(Finding("RC001", "FAIL",
                                f"delegation sentence appears {count}x (need exactly {required_count})"))
    if report_required and not re.search(r"report[_A-Za-z0-9-]*\.pdf", body):
        findings.append(Finding("RC004", "FAIL", "no report_*.pdf filename named anywhere in SKILL.md"))
    for report_path in SUBFOLDER_PDF_RE.findall(text) if report_required else []:
        findings.append(Finding("RC005", "FAIL", f"report path in a subfolder: {report_path}"))
    prose = strip_code(text).lower()
    for rule, phrase in FORBIDDEN if report_required else []:
        if phrase.lower() in prose:
            findings.append(Finding(rule, "FAIL",
                                    f"forbidden phrasing makes the report conditional: {phrase!r}"))
    if report_required and not wires_styled_gate(pkg, body):
        findings.append(Finding("RC008", "FAIL",
                                f"the terminal step never calls {STYLED_GATE}() or write_receipt()"))
    report_names = set(re.findall(r"report[_A-Za-z0-9-]*\.pdf", body))
    receipt_reports = set(re.findall(
        rf"\b{RECEIPT_WRITER}\s*\(.*?\breport_name\s*=\s*[\"']"
        r"(report[_A-Za-z0-9-]*\.pdf)[\"']", body, re.S
    ))
    staged_reports = set(re.findall(
        r"\bstaged_copy\s*\([^,\n]+,\s*[\"'](report[_A-Za-z0-9-]*\.pdf)[\"']\s*\)", body
    ))
    expected_reports = receipt_reports or report_names
    if report_required and (not expected_reports or not expected_reports.issubset(staged_reports)):
        findings.append(Finding(
            "RC010", "FAIL",
            "the terminal step does not publish its declared PDF with staged_copy()",
        ))

    runtime_nodes: list[tuple[int, int, ast.AST]] = []
    for fence_index, fence in enumerate(FENCE_PY_RE.findall(body)):
        try:
            tree = ast.parse(fence)
        except SyntaxError:
            continue
        runtime_nodes.extend(
            (fence_index, int(getattr(node, "lineno", 0)), node) for node in ast.walk(tree)
        )

    def direct_call(node: ast.AST, name: str) -> bool:
        return (
            isinstance(node, ast.Call)
            and isinstance(node.func, ast.Name)
            and node.func.id == name
        )

    def keyword(call: ast.Call, name: str) -> ast.AST | None:
        return next((item.value for item in call.keywords if item.arg == name), None)

    def string_value(node: ast.AST | None) -> str | None:
        return node.value if isinstance(node, ast.Constant) and isinstance(node.value, str) else None

    receipt_calls = [
        (fence, line, node) for fence, line, node in runtime_nodes
        if direct_call(node, "write_receipt")
    ]
    facts = (contract_data or {}).get("facts", {})
    if isinstance(facts, dict) and facts.get("requirement") == "required":
        facts_source = facts.get("runtime_payload_artifact")
        facts_calls = []
        for fence, line, node in runtime_nodes:
            if not direct_call(node, "write_facts_from_artifact"):
                continue
            call = node
            output = string_value(call.args[0]) if call.args else None
            if (
                output == "report_facts.json"
                and string_value(keyword(call, "source")) == facts_source
                and isinstance(keyword(call, "figures"), ast.Name)
                and keyword(call, "figures").id == "figures"
                and string_value(keyword(call, "contract")) == "skill_contract.json"
            ):
                facts_calls.append((fence, line, call))
        figure_bindings = [
            (fence, line, node) for fence, line, node in runtime_nodes
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "figures"
                    for target in node.targets)
            and (
                direct_call(node.value, "assert_figures")
                or isinstance(node.value, ast.List) and not node.value.elts
            )
        ]
        facts_wired = any(
            any(binding[:2] < call[:2] for binding in figure_bindings)
            and any(call[:2] < receipt[:2] for receipt in receipt_calls)
            for call in facts_calls
        )
        if not facts_wired:
            findings.append(Finding(
                "RC011", "FAIL",
                "facts are required but the runtime has no ordered contract-bound "
                "write_facts_from_artifact() call with its declared source and figure inventory",
            ))

    figures = (contract_data or {}).get("figures", {})
    if isinstance(figures, dict) and figures.get("applicable") is True:
        figure_bindings = [
            (fence, line, node) for fence, line, node in runtime_nodes
            if isinstance(node, ast.Assign)
            and any(isinstance(target, ast.Name) and target.id == "figures"
                    for target in node.targets)
            and direct_call(node.value, "assert_figures")
        ]
        receipt_uses = [
            (fence, line, node) for fence, line, node in receipt_calls
            if isinstance(keyword(node, "figures"), ast.Name)
            and keyword(node, "figures").id == "figures"
        ]
        if not any(
            binding[:2] < receipt[:2]
            for binding in figure_bindings for receipt in receipt_uses
        ):
            findings.append(Finding(
                "RC012", "FAIL",
                "figures apply but the runtime does not initialize and pass the validated manifest",
            ))

    execution = (contract_data or {}).get("execution", {})
    bundled_not_applicable = (
        evidence and execution.get("bundled_commands_applicable") is False
        and bool(str(execution.get("not_applicable_reason", "")).strip())
    )
    for fence in FENCE_PY_RE.findall(body):
        if f"{RECEIPT_WRITER}(" not in fence:
            continue
        for arg in ("bundled_files", "outputs"):
            match = re.search(rf"\b{arg}\s*=\s*([^,\n#]*)", fence)
            got = (match.group(1).strip() if match else "").rstrip(",")
            invalid = match is None or got in ("[]", "[...]", "()", "(...)", "...")
            if invalid and not (arg == "bundled_files" and got in ("[]", "()")
                                and bundled_not_applicable):
                findings.append(Finding(
                    "RC009", "FAIL",
                    f"the {RECEIPT_WRITER}() call leaves {arg} as {got or 'unset'!r} — name the "
                    "real paths or record a validated bundled-execution non-applicability decision",
                ))
    return findings


# --- the checks ---------------------------------------------------------------------------------

def check_package(pkg: pathlib.Path, contract: str = "A", require_receipt: bool = False):
    out: list[Finding] = []
    add = out.append

    # A blind rule rides along with the findings: no path out of this function may describe a package
    # as clean while one of its checks was not running.
    out.extend(degradations())

    md = pkg / "SKILL.md"
    if not md.is_file():
        return out + [Finding("PK003", "FAIL", "no SKILL.md at package root")], "none"
    text = md.read_text(encoding="utf-8", errors="replace")
    keys, values, body, raw = split_frontmatter(text)

    if keys is None:
        add(Finding("PK003", "FAIL", "SKILL.md does not open with a '---' frontmatter block"))
        return out, "none"

    # ---- frontmatter
    unknown = [k for k in keys if k not in CANONICAL_KEY_ORDER]
    if unknown:
        add(Finding("FM003", "FAIL", f"unsupported frontmatter key(s): {', '.join(unknown)}"))

    known = [k for k in keys if k in CANONICAL_KEY_ORDER]
    expected = [k for k in CANONICAL_KEY_ORDER if k in known]
    if known != expected:
        add(Finding("FM004", "FAIL", f"key order {known} != canonical {expected}"))

    missing = REQUIRED_KEYS - set(known)
    if missing:
        sev = "FAIL" if contract == "A" else "WARN"
        add(Finding("FM005", sev, f"missing required key(s): {', '.join(sorted(missing))}"
                                  f"{' (check-in blocker)' if contract == 'B' else ''}"))

    sid = unquote(values.get("id", ""))
    if sid and not ID_RE.match(sid):
        add(Finding("FM001", "FAIL", f"id {sid!r} does not match ^skill_[0-9a-f]{{32}}$"))

    name = unquote(values.get("name", ""))
    if name and name != pkg.name:
        add(Finding("FM002", "FAIL", f"name {name!r} != folder {pkg.name!r}"))
    if name and not SLUG_RE.match(name):
        add(Finding("FM002", "FAIL", f"name {name!r} is not lowercase-kebab"))

    vis = unquote(values.get("visibility", ""))
    if vis and vis not in SYSTEM_VISIBILITIES:
        note = " ('private' is legal at runtime, fatal in CI)" if vis == "private" else ""
        add(Finding("FM006", "FAIL", f"visibility {vis!r} not in {sorted(SYSTEM_VISIBILITIES)}{note}"))

    if "starting-prompt" in values:
        sp = unquote(values["starting-prompt"])
        if not sp:
            add(Finding("FM007", "FAIL", "starting-prompt present but empty"))
        elif re.search(r"\s+\.\s+\.$", sp):
            add(Finding("FM007", "FAIL", "starting-prompt ends with the now-rejected ' . .' suffix"))

    cat = unquote(values.get("category", ""))
    if cat and cat not in CATEGORIES:
        add(Finding("FM008", "WARN", f"category {cat!r} is not one of the 17 live values"))

    for line in raw:
        m = FM_LINE_RE.match(line)
        if not m:
            continue
        key, val = m.group(1), m.group(2).strip()
        if val in {"|", ">", "|-", ">-"}:
            add(Finding("FM009", "WARN", f"{key} uses a YAML block scalar; CI cannot see it"))
        # unquote() above strips the delimiters without asking whether what is between them parses,
        # and nothing else in the toolchain asks either — a broken escape reaches the platform's real
        # loader, which refuses the skill. A value that closes but carries trailing content, or does
        # not close on this line, loads yet still reads differently here: one rule, both readings.
        elif val[:1] == '"' and not FM_DQ_RE.fullmatch(val):
            add(Finding("FM011", "FAIL",
                        f"{key} value is not a valid single-line double-quoted YAML scalar "
                        f"(unescaped \", dangling \\, or content past the closing quote) — CI's "
                        f"per-line regex accepts it, the platform's YAML loader reads a different "
                        f"value or refuses the skill outright"))
        elif val[:1] == "'" and not FM_SQ_RE.fullmatch(val):
            add(Finding("FM011", "FAIL",
                        f"{key} value is not a valid single-line single-quoted YAML scalar (a "
                        f"literal ' must be doubled) — CI's per-line regex accepts it, the "
                        f"platform's YAML loader reads a different value or refuses the skill"))

    desc = unquote(values.get("description", ""))
    if len(desc) > 500:
        add(Finding("FM010", "WARN", f"description is {len(desc)} chars (>500 risks truncation)"))

    # ---- package hygiene
    for p in pkg.rglob("*"):
        if p.name.startswith(APPLEDOUBLE_PREFIX):
            add(Finding("PK005", "FAIL",
                        f"AppleDouble sidecar would ship: {p.relative_to(pkg)} — re-create the archive with COPYFILE_DISABLE=1 tar, or delete it"))
        elif p.name in CACHE_NAMES or p.suffix in CACHE_SUFFIXES:
            add(Finding("PK001", "FAIL", f"cache artifact in tree: {p.relative_to(pkg)}"))
        elif p.name in STRAY_NAMES:
            add(Finding("PK002", "WARN", f"non-skill file ships to S3: {p.relative_to(pkg)}"))

    nested = [p for p in pkg.rglob("SKILL.md") if p != md]
    for p in nested:
        add(Finding("PK003", "WARN", f"nested SKILL.md is invisible to CI: {p.relative_to(pkg)}"))

    n_lines = len(text.splitlines())
    if n_lines > 500:
        add(Finding("PK004", "WARN", f"SKILL.md is {n_lines} lines; over ~400 agents skim it"))

    # ---- template-filling: unresolved scaffolder markers, and pasted boilerplate
    # Count markers in prose only. A skill that *documents* the marker (this one does) mentions it
    # inside backticks, and flagging that is the same false positive as quoting a forbidden phrase.
    n_todo = len(re.findall(r"TODO\(author\)", strip_code(text)))
    if n_todo:
        add(Finding("TF001", "FAIL",
                    f"{n_todo} unresolved TODO(author) marker(s) — the interview is incomplete. "
                    f"Answer the question or delete the section; do not delete just the marker."))

    for st in load_stanzas()[0]:
        if st["text"].lower() in text.lower():
            add(Finding(st["id"], "FAIL",
                        f"copied boilerplate: {st['text']!r} — {st['why']}"))

    # ---- bundled-file existence: the highest-yield check in the design
    for ref in sorted(set(BUNDLED_RE.findall(body))):
        rel = ref.rstrip(".,;:)`\"'")
        if rel.endswith("/") or "*" in rel or "<" in rel:
            continue
        if not (pkg / rel).exists():
            add(Finding("BF001", "FAIL", f"SKILL.md names {rel!r}, which is not in the package"))

    # Only the modules templates/ hands out. Checking every import would flag stdlib and the platform
    # runtime's packages, absent from every package by design, and IMPORT_RE cannot tell an import
    # from prose opening a line with "from" — a shipped SKILL.md has "from matching inside common
    # English words" in a docstring. A rule that fires on `import json` is deleted within the week.
    imported = {mod for block in FENCE_PY_RE.findall(body) for mod in IMPORT_RE.findall(block)}
    for mod in sorted(imported & template_modules()[0]):
        # templates/ counts as shipped: the package that hands the module out is prescribing the
        # import for the skills it generates, not running it.
        if not any((pkg / d / f"{mod}.py").exists() for d in ("scripts", "templates")):
            add(Finding("BF002", "FAIL",
                        f"a python block imports {mod!r} but the package ships no scripts/{mod}.py — "
                        f"the import fails the moment an agent runs the block"))

    # ---- versioned evidence contract (all newly scaffolded packages)
    archetype = derive_archetype(pkg, body)
    evidence_contract = bool(re.search(rf"^{re.escape(EVIDENCE_MARKER)}$", blank_fences(body), re.M))
    contract_data: dict = {}
    if evidence_contract:
        contract_path = pkg / "skill_contract.json"
        if not contract_path.is_file():
            add(Finding("EV001", "FAIL", "evidence-v1 package has no skill_contract.json"))
        else:
            try:
                contract_data = json.loads(contract_path.read_text(encoding="utf-8"))
            except (OSError, ValueError) as exc:
                add(Finding("EV001", "FAIL", f"skill_contract.json is unreadable ({type(exc).__name__})"))
            else:
                evidence, why = evidence_module()
                if evidence is None:
                    add(Finding("DG001", "DEGRADED", f"EV001-EV017 never ran: {why}"))
                else:
                    parsed_values = {key: unquote(value) for key, value in values.items()}
                    for rule, severity, message in evidence.validate_contract(pkg, contract_data, parsed_values):
                        add(Finding(rule, severity, message))

    # ---- declared report/deliverable contract
    if archetype == "analysis-workflow" or evidence_contract:
        out.extend(report_contract_findings(
            pkg, text, body, evidence=evidence_contract, contract_data=contract_data
        ))

    # ---- analysis-only report additions
    if archetype == "analysis-workflow":
        # OP001: an analysis workflow has to hand back something a later skill can read. The exact
        # filename remains author-supplied because only the workflow defines its result shape.
        out_sec = re.search(r"^#{2,3}\s*Outputs\b.*$", body, re.M | re.I)
        if out_sec:
            seg = body[out_sec.end():]
            nxt = re.search(r"^#{2,3}\s", seg, re.M)
            seg = seg[: nxt.start()] if nxt else seg
            tabular = [f for f in re.findall(r"[\w/<>*.-]+\.(?:csv|tsv|parquet|rds|jsonl|h5ad|xlsx)",
                                             seg, re.I)
                       if "report_facts" not in f.lower()]
            if not tabular:
                add(Finding("OP001", "FAIL",
                            "'## Outputs' promises no machine-readable result file — name the table "
                            "this run writes (.csv/.tsv/.parquet/.rds/...), so a downstream skill has "
                            "something to read and the run has something to be checked against. "
                            "report_facts.json does not count: it carries the report's numbers, not "
                            "the result"))

        # Figures: one per analysis step. Deliberately WARN-only — nothing static can know which
        # steps genuinely have nothing to plot, so the rule sets the default and the author carries
        # the exception. Construction (the scaffolder writing the manifest) does the real work.
        prose_body = blank_fences(body)
        fig_sec = re.search(r"^#{2,3}\s*Figures\b.*$", prose_body, re.M | re.I)
        declared: set[str] = set()
        if fig_sec:
            seg = prose_body[fig_sec.end():]
            nxt = re.search(r"^#{2,3}\s", seg, re.M)
            declared = set(re.findall(r"[\w/<>-]+\.(?:png|svg|pdf|jpg)",
                                      seg[: nxt.start()] if nxt else seg))
        step_titles = STEP_TITLE_RE.findall(prose_body)
        n_steps = len({n for n, title in step_titles if not NON_ANALYSIS.search(title)})

        if not fig_sec:
            add(Finding("FG001", "WARN",
                        "no '## Figures' section — an analysis step with no figure is a number the "
                        "reader has to take on trust"))
        elif not declared:
            add(Finding("FG001", "WARN", "'## Figures' section declares no figure files"))
        elif n_steps and len(declared) < n_steps:
            add(Finding("FG001", "WARN",
                        f"{n_steps} analysis step(s) but {len(declared)} declared figure(s) — "
                        f"add one per step, or state why a step has nothing to plot"))

        if declared and not re.search(r"report_facts|\"figures\"|'figures'", text):
            add(Finding("FG002", "WARN",
                        "figures are declared but nothing reads them into the report; write a "
                        "`figures` array into the facts artifact so the report derives its inventory"))

        for f in sorted(declared):
            if re.match(r"^(figures?/|<)", f) is None and not f.startswith("infographic"):
                add(Finding("FG003", "WARN",
                            f"{f} is not under figures/ — only the report and GenerateImage "
                            f"schematics belong at the results root"))

        # Caveat binding. Only meaningful once the package HAS a facts artifact to bind to. Run
        # unconditionally it fires on perfectly good prose caveats — measured at roughly 2 in 5
        # known-good packages, almost all of them substantive multi-line bullets. Gate on the
        # declaration instead, which makes it precise and forward-looking.
        declares_facts = bool(re.search(r"report_facts\.json|facts artifact", text, re.I))
        m = re.search(r"^#{2,3}\s*.*caveat.*$", prose_body, re.M | re.I)
        if declares_facts and m:
            seg = prose_body[m.end():]
            nxt = re.search(r"^#{2,3}\s", seg, re.M)
            seg = seg[: nxt.start()] if nxt else seg
            # Whole bullet, not just its first line: a caveat's binding often sits two lines down.
            bullets = re.split(r"^\s*(?:[-*]|\d+\.)\s+", seg, flags=re.M)[1:]
            for b in bullets[:8]:
                if not re.search(r"`[^`]+`|\d", b):
                    one = normalise(b)[:70]
                    add(Finding("CV001", "WARN", f"caveat names no artifact field or number: {one!r}"))

    # LC001: SKILL.md advertised "the licence gate" and no licence rule existed — a documented,
    # absent check, in a package whose whole argument is that unenforced rules do not hold. Review
    # raised the related point that Q6 becomes a blanket assertion with nothing verified per
    # dependency. A blanket claim is only a claim: name the sources it is a claim ABOUT. WARN, not
    # FAIL — measured across the shipped fleet before choosing, and it fires on packages whose
    # licence prose is otherwise fine.
    # Searched AND sliced on the same string. blank_fences does not preserve offsets, so matching on
    # the blanked copy and slicing the original lands in a different section entirely — which is how
    # this rule read the wrong text and stayed silent on a package that should have tripped it.
    lic_body = blank_fences(body)
    lic = re.search(r"^#{2,3}\s*.*(?:licen[cs]|data sources).*$", lic_body, re.M | re.I)
    if lic:
        seg = lic_body[lic.end():]
        nxt = re.search(r"^#{2,3}\s", seg, re.M)
        seg = seg[: nxt.start()] if nxt else seg
        blanket = re.search(r"permissive[- ]licen[cs]ed?\s+(?:sources?|only)|all\s+(?:data\s+)?"
                            r"(?:sources?|dependenc\w+|packages?)\s+are\s+permissive|"
                            r"permissive[- ]only", seg, re.I)
        named = re.search(r"https?://|`[\w.-]+`|\b(?:CC[- ]BY|MIT|BSD|Apache|GPL|LGPL|AGPL|"
                          r"CC0|ODbL|proprietary)\b", seg)
        if blanket and not named:
            add(Finding("LC001", "WARN",
                        "the licence section makes a blanket claim and names no source: "
                        f"{normalise(blanket.group(0))!r}. Name each dataset, package or URL the "
                        "claim covers, with its terms — an unattributed 'permissive only' is the "
                        "assertion, not the check"))

    if require_receipt:
        # Neither existence nor "every boolean it happens to carry is true" is evidence: a receipt
        # that names its own outcomes can always pass, which is exactly the gate-that-cannot-fail
        # defect this package warns about. Every RECEIPT_KEYS entry is required by name.
        rr = pkg / "run_receipt.json"
        if not rr.exists():
            add(Finding("RR001", "FAIL", "run_receipt.json missing — the skill was never run (Step 5)"))
        else:
            try:
                data = json.loads(rr.read_text(encoding="utf-8"))
            except (ValueError, OSError) as exc:
                add(Finding("RR001", "FAIL", f"run_receipt.json is unreadable ({type(exc).__name__})"))
                data = None
            if isinstance(data, dict):
                policy = contract_data.get("deliverable_policy", {}) if evidence_contract else {}
                report_policy = policy.get("report", {}) if isinstance(policy, dict) else {}
                style_contract = (
                    isinstance(report_policy, dict)
                    and isinstance(report_policy.get("default_style_provider"), str)
                    and report_policy.get("explicit_style_override_allowed") is True
                )
                if evidence_contract and style_contract:
                    required_receipt_keys = EVIDENCE_RECEIPT_KEYS
                    expected_schema = RECEIPT_SCHEMA_V3
                    report_receipt_keys = REPORT_RECEIPT_KEYS
                elif evidence_contract:
                    required_receipt_keys = EVIDENCE_RECEIPT_KEYS_V2
                    expected_schema = RECEIPT_SCHEMA_V2
                    report_receipt_keys = REPORT_RECEIPT_KEYS_V2
                else:
                    required_receipt_keys = RECEIPT_KEYS
                    expected_schema = RECEIPT_SCHEMA
                    report_receipt_keys = {"report_at_results_root", "report_branded"}
                # The receipt has to be the QC module's OUTPUT, not five booleans about itself. A
                # hand-written all-true block satisfied every check below it, so the marker and the
                # per-key evidence are what separate a measurement from a claim. This does not stop a
                # determined author forging the file — nothing in-band can — it stops the cheap path.
                if data.get("schema") != expected_schema:
                    add(Finding("RR002", "FAIL",
                                f"run receipt is not {expected_schema!r} (got "
                                f"{data.get('schema')!r}) — generate it with "
                                f"report_qc.write_receipt() rather than writing the booleans by "
                                f"hand; the keys are its return, not a checklist"))
                ev = data.get("evidence")
                if not isinstance(ev, dict):
                    add(Finding("RR002", "FAIL",
                                "run receipt carries no `evidence` map — an outcome with nothing "
                                "behind it is a claim. write_receipt() records the artifact each "
                                "outcome was decided from: path, byte count, colours read, "
                                "transcript record matched"))
                    ev = {}
                if style_contract and data.get("report_style_verified") is True:
                    for error in style_receipt_errors(
                        report_policy,
                        ev.get("report_style_verified"),
                    ):
                        add(Finding("RR002", "FAIL", f"run receipt: {error}"))
                report_required = policy.get("report", {}).get("required") is True
                infographic_required = policy.get("infographic", {}).get("required") is True
                facts = contract_data.get("facts", {}) if evidence_contract else {}
                facts_required = facts.get("requirement") == "required"
                allowed_not_applicable = set()
                if evidence_contract and not report_required:
                    allowed_not_applicable.update(report_receipt_keys)
                if evidence_contract and not infographic_required:
                    allowed_not_applicable.add("infographic_lineage_verified")
                if evidence_contract and not facts_required:
                    allowed_not_applicable.add("facts_artifact_verified")
                for k in required_receipt_keys:
                    if data.get(k) in (True, "not_applicable") and not ev.get(k):
                        add(Finding("RR002", "FAIL",
                                    f"run receipt: {k} has no evidence under "
                                    f"evidence[{k!r}] — that is the checklist this gate rejects"))
                    if data.get(k) == "not_applicable":
                        detail = ev.get(k, {}) if isinstance(ev, dict) else {}
                        if k not in allowed_not_applicable:
                            add(Finding("RR001", "FAIL",
                                        f"run receipt: {k} cannot be not_applicable under the contract"))
                        elif not isinstance(detail, dict) or not str(detail.get("reason", "")).strip():
                            add(Finding("RR002", "FAIL",
                                        f"run receipt: {k} is not_applicable without an evidence reason"))
                # RR003 keeps the embedding claim separate from the figure-artifact claim. The
                # explicit state says whether embedding passed, failed, or could not be evaluated.
                #
                # "fail" blocks: the check ran and disagreed, which is real evidence. "not_evaluable"
                # does not block, because it means pypdf was absent, and a rule that made the receipt
                # unobtainable in any runtime without pypdf is a rule somebody deletes. It is still
                # reported, so it reaches a reader instead of passing silently.
                emb = data.get("figures_embedded")
                if emb not in EMBED_STATES:
                    add(Finding("RR003", "FAIL",
                                f"run receipt: figures_embedded is {emb!r}, expected one of "
                                f"{', '.join(EMBED_STATES)} — write_receipt() records it; a receipt "
                                f"without it cannot say whether the figures reached the report"))
                elif emb == "fail":
                    add(Finding("RR003", "FAIL",
                                "run receipt: figures_embedded is 'fail' — the report was checked "
                                "and does not carry the declared figures"))
                elif emb == "not_evaluable":
                    add(Finding("RR003", "WARN",
                                "run receipt: figures_embedded is 'not_evaluable' — the figures "
                                "exist and are non-blank, but nothing checked whether they reached "
                                "the report (pypdf absent). Do not read this run as having "
                                "demonstrated an illustrated report"))

                for k in required_receipt_keys:
                    if k not in data:
                        add(Finding("RR001", "FAIL",
                                    f"run receipt: {k} is absent — that outcome was never recorded, "
                                    f"so the run never proved it (Step 5 lists all "
                                    f"{len(required_receipt_keys)} keys)"))
                    elif data[k] is False:
                        add(Finding("RR001", "FAIL",
                                    f"run receipt: {k} is false ({receipt_reason(data, k)})"))
                    elif data[k] == "not_applicable" and k in allowed_not_applicable:
                        pass
                    elif data[k] is not True:
                        add(Finding("RR001", "FAIL",
                                    f"run receipt: {k} is {data[k]!r}, not the boolean true — "
                                    f"truthiness is not proof"))
                for k, v in sorted(data.items()):
                    if k not in required_receipt_keys and v is False:
                        add(Finding("RR001", "FAIL",
                                    f"run receipt: {k} is false ({receipt_reason(data, k)})"))
            elif data is not None:
                add(Finding("RR001", "FAIL", "run_receipt.json must be a JSON object of named outcomes"))

    return out, archetype


# --- corpus measurement -------------------------------------------------------------------------

def measure(root: pathlib.Path) -> int:
    pkgs = sorted(p for p in root.iterdir() if p.is_dir() and (p / "SKILL.md").is_file())
    fired: dict[str, set[str]] = collections.defaultdict(set)
    sev_of: dict[str, str] = {}
    arche: collections.Counter = collections.Counter()

    degraded: set[str] = set()
    for pkg in pkgs:
        findings, archetype = check_package(pkg, contract="A")
        arche[archetype] += 1
        for f in findings:
            # Identical for every package, because it is a property of this checkout and not of the
            # corpus. Left in the table it would be the noisiest "defect" row in it.
            if f.severity == "DEGRADED":
                degraded.add(f.message)
                continue
            fired[f.rule].add(pkg.name)
            sev_of[f.rule] = f.severity

    n = len(pkgs)
    print(f"corpus: {n} packages in {root}")
    print(f"archetypes: {dict(arche)}\n")

    # A high fire rate means two very different things, and conflating them deletes good checks.
    # NEW_RULE checks encode the team's new mandates: legacy skills SHOULD fail them, and a skill
    # authored through the scaffolder passes by construction. Only DEFECT checks are calibrated by
    # their fire rate, because there a high rate means false positives.
    # ST00x belongs here too: the stanzas are byte-exact, so a package either pasted them or did
    # not. A high rate is inherited debt from a deprecated guide, never a false positive.
    # FM011 and BF002 stay OUT, despite being new: the fleet already loads on the platform and its
    # imports already resolve, so every hit is either a genuinely broken package or a bug in the
    # rule's grammar. That is falsifiable by fire rate, which is exactly what the defect class is for.
    NEW_RULE = {"RC001", "RC004", "RC005", "RC007a", "RC007b", "RC008", "RC009", "RC010", "RR001", "RR002", "RR003",
                "ST001", "ST002", "ST003", "TF001", "OP001", "LC001"}

    print(f"{'rule':8} {'sev':5} {'pkgs':>5}  {'%':>5}  class      verdict")
    print("-" * 82)
    for rule in sorted(fired, key=lambda r: -len(fired[r])):
        hits = len(fired[rule])
        pct = 100.0 * hits / n
        if rule in NEW_RULE:
            kind = "new-rule"
            verdict = f"expected legacy debt ({hits} to retrofit)"
        else:
            kind = "defect"
            if pct >= 40:
                verdict = "TOO NOISY — recalibrate or drop"
            elif pct >= 15:
                verdict = "WARN only"
            else:
                verdict = "keep as FAIL" if sev_of[rule] == "FAIL" else "keep"
        print(f"{rule:8} {sev_of[rule]:5} {hits:5}  {pct:5.1f}  {kind:9}  {verdict}")
    print("\nexamples")
    print("-" * 78)
    for rule in sorted(fired, key=lambda r: -len(fired[r])):
        ex = ", ".join(sorted(fired[rule])[:4])
        print(f"{rule:8} {ex}")
    if degraded:
        # A rule that never ran is absent from the table above, and absence reads as a 0% fire rate.
        print("\nDEGRADED — rules missing from the table above, not measured at 0%:")
        for m in sorted(degraded):
            print(f"  {m}")
        return 3
    return 0


# --- cli ----------------------------------------------------------------------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("package", nargs="?", help="package directory to check")
    ap.add_argument("--contract", choices=["A", "B"], default="A")
    ap.add_argument("--require-run-receipt", action="store_true")
    ap.add_argument("--measure", metavar="SKILLS_DIR", help="report fire rates over a corpus")
    ap.add_argument("--explain", metavar="RULE_ID")
    args = ap.parse_args()

    if args.explain:
        print(EXPLAIN.get(args.explain, f"no such rule: {args.explain}"))
        return 0

    if args.measure:
        return measure(pathlib.Path(args.measure).expanduser().resolve())

    if not args.package:
        ap.error("give a package directory, or --measure SKILLS_DIR")

    pkg = pathlib.Path(args.package).expanduser().resolve()
    findings, archetype = check_package(pkg, args.contract, args.require_run_receipt)

    fails = [f for f in findings if f.severity == "FAIL"]
    degraded = [f for f in findings if f.severity == "DEGRADED"]
    warns = [f for f in findings if f.severity == "WARN"]

    print(f"{pkg.name}  (contract {args.contract}, archetype {archetype})\n")
    for f in fails + degraded + warns:
        print(f"  {f.severity:8} {f.rule}  {f.message}")
    if not findings:
        print("  no findings")

    print()
    if fails:
        print(f"RESULT: not written — {len(fails)} blocking finding(s). Do not install or publish this package.")
        return 1
    if degraded:
        # A blocking finding is the more actionable answer, so it takes 1 and this takes 3. Deliberately
        # not GATE PASSED: the package satisfied the rules that ran, which is a different claim.
        print(f"RESULT: not written — {len(degraded)} check(s) never ran, so a pass here would cover "
              f"only the rules that did. Repair this authoring package (--explain DG001) and re-run. "
              f"Do not install or publish this package.")
        return 3
    print("GATE PASSED — the package is well-formed. This says nothing about whether the science is right.")
    print("Not checked: whether the analysis suits this biology; whether the skill will trigger; whether")
    print("a threshold is defensible; whether a caveat is true; whether the report's prose is honest.")
    return 2 if warns else 0


if __name__ == "__main__":
    sys.exit(main())
