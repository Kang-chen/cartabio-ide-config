#!/usr/bin/env python3
"""Seed and verify the report infographic's spec from the run's own evidence.

The sibling ``target-infographic`` skill hand-authors a JSON spec and asks the
prompt politely not to fabricate. That works there because a human is filling in
Open Targets scores they are looking at. It does not work here: this skill's
whole premise is that every delivered claim carries a verbatim quote with a
resolvable locator, and an infographic bolted on top with unchecked numbers
would be the one part of the report nobody could audit — sitting, by design, on
page one above everything that IS checked.

So the spec is:

  * **seeded** from the report model — title, question, axes, support tiers,
    independent-study counts and the real source citations are copied in, not
    retyped; and
  * **verified** — every number, identifier and citation the author adds must
    appear in an accepted evidence row or the reference list. ``--verify`` fails
    otherwise, and the builders refuse to embed an unverified infographic.

What is NOT automated: the three panel DESCRIPTIONS. A schematic cannot be
generated from claim text — someone has to decide what to draw. Seeding gives
them the facts; verification checks what they wrote.

    python infographic_spec.py --root "$RUN" --seed
    python infographic_spec.py --root "$RUN" --write-tool-request
    # Load and call Biomni GenerateImage with the saved request arguments.
    python infographic_spec.py --root "$RUN" \
        --install-image /mnt/results/infographic.png
    python infographic_spec.py --root "$RUN" --verify   # gate spec + tool receipt + image
"""
from __future__ import annotations

import argparse
import datetime
import hashlib
import json
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from report_model import build_model, load_contract, read_jsonl  # noqa: E402
from infographic_overlay import compose  # noqa: E402
from support_policy import SUPPORT_LABEL  # noqa: E402
from scientific_semantics import assertion_errors  # noqa: E402

SPEC_NAME = "infographic_spec.json"
IMAGE_NAME = "infographic.png"
TEMPLATE = SCRIPTS.parent / "references" / "infographic_prompt_template.txt"
TOOL_NAME = "GenerateImage"
TOOL_SEARCH_QUERY = f"select:{TOOL_NAME}"
TOOL_ASPECT_RATIO = "3:2"
TOOL_DESCRIPTION = "Generating the Phylo-styled literature-review infographic"
TOOL_REQUEST_SCHEMA_VERSION = 1
RESULTS_DIR = pathlib.Path("/mnt/results")
TOOL_REQUEST_PATH = pathlib.Path("state/infographic_generate_image_request.json")
GENERATION_RECEIPT_PATH = pathlib.Path("state/infographic_generation.json")
MEDIA_CHECK_RECEIPT_PATH = pathlib.Path("state/infographic_media_check.json")
MEDIA_CHECK_PASS = "pass"
MEDIA_CHECK_NOT_APPLICABLE = "not_applicable"
ANTIBODY_RE = re.compile(
    r"\b(?:antibod|bispecific|t[- ]cell engager|adc\b)", re.IGNORECASE
)

# Lines of the template that address the operator rather than the image model:
# the header explaining what the file is, and the end marker. Everything between
# is the prompt itself.
_TEMPLATE_COMMENT = re.compile(r"^\s*#")

# The renderer never sees a Python format string, so placeholders are {{NAME}}
# and a stray brace in an authored description cannot blow up substitution.
_PLACEHOLDER = re.compile(r"\{\{([A-Z_0-9]+)\}\}")

# Fields the author must write; seeding leaves them as an explicit TODO so a
# half-filled spec cannot be mistaken for a finished one.
AUTHORED_FIELDS = (
    "PANEL_A_TITLE", "PANEL_A_DESCRIPTION",
    "PANEL_B_TITLE", "PANEL_B_DESCRIPTION",
    "PANEL_C_TITLE", "PANEL_C_DESCRIPTION",
    "DIRECTION",
)
TODO = "TODO — author this; see references/infographic_prompt_template.txt"

# A review is a TARGET review when its question is about whether to drug
# something. Everything else takes the general profile — mechanism questions,
# biomarkers, modality comparisons, direction-of-effect controversies.
_TARGET_QUESTION = re.compile(
    r"\b(therapeutic target|drug target|as a target|target validation|"
    r"druggab|tractab|target for)\b", re.IGNORECASE)


def detect_profile(model: dict) -> str:
    """"target" or "general", from the question the review was asked."""
    text = f"{model.get('question') or ''} {model.get('title') or ''}"
    return "target" if _TARGET_QUESTION.search(text) else "general"


def _axis_rows(model: dict) -> list[dict]:
    return list(model.get("synthesis_table") or [])


def _counterweight(model: dict) -> str:
    """The review's strongest disconfirming or limiting finding.

    Its own bullet in the evidence strip, and not optional: a review with no
    counterweight has not looked, and an infographic that shows only supporting
    evidence misrepresents a document whose contract requires a contradiction
    axis.
    """
    for row in model["claims"]:
        if row["support_state"] in {"C_CONFLICTED", "C_REFUTED"}:
            return (f"Counter-evidence: {row['support_label'].lower()} — "
                    f"{_first_sentence(row['claim_text'])}")
    for row in model["claims"]:
        if row["contradicting"]:
            cite = row["contradicting"][0].get("citation") or ""
            return (f"Counter-evidence: {_first_sentence(row['claim_text'])} "
                    f"— contradicted by {cite}").strip()
    weak = [r for r in model["claims"] if r["support_state"] == "C1_INDIRECT"]
    if weak:
        return (f"Evidence gap: {len(weak)} claim(s) rest on indirect or "
                f"background support only ({weak[0]['display_id']}"
                f"{' and others' if len(weak) > 1 else ''})")
    notes = model.get("coverage_notes") or []
    return f"Evidence gap: {notes[0]}" if notes else ""


def _first_sentence(text: str, limit: int = 120) -> str:
    sentence = re.split(r"(?<=[.!?])\s", str(text or "").strip())[0]
    return sentence if len(sentence) <= limit else sentence[:limit].rstrip() + "…"


def _axis_bullet(row: dict) -> str:
    """One evidence-strip bullet: axis, tier, study count, real sources."""
    sources = ", ".join(s["citation"] for s in row.get("sources") or [])
    tier = row["support_label"]
    bullet = f"{row.get('axis_label') or row['axis']}: {tier.lower()}"
    if sources:
        bullet += f" — {sources}"
    return bullet


def seed(root: pathlib.Path) -> dict:
    """Build a spec pre-filled with everything the report already knows."""
    model = build_model(root, load_contract())
    profile = detect_profile(model)
    axes = _axis_rows(model)
    manifest = model.get("manifest") or {}

    strongest = axes[0] if axes else {}
    second = axes[1] if len(axes) > 1 else strongest

    n_studies = sum((model.get("panel_studies") or {}).values())
    tiers = {r["support_state"] for r in model["claims"]}
    headline_bits = []
    if "C2_CONVERGENT" in tiers:
        headline_bits.append("convergent evidence on ≥1 axis")
    headline_bits.append(f"{len(model['claims'])} grounded claims")
    headline_bits.append(f"{n_studies} independent primary studies")

    spec = {
        "_comment": [
            "Seeded by scripts/infographic_spec.py from this run's own evidence.",
            "Fields marked TODO are yours to author — a schematic cannot be",
            "generated from claim text. Everything else was copied from the",
            "report model and should not be retyped.",
            "Run --verify before rendering: every number, identifier and",
            "citation you add must appear in an accepted evidence row.",
        ],
        "PROFILE": profile,
        "SUBJECT": manifest.get("subject") or _guess_subject(model),
        "SUBJECT_LONG": manifest.get("subject_long") or "",
        "SUBJECT_CLASS_SHORT": manifest.get("subject_class") or "",
        "CONTEXT": manifest.get("context") or "",
        "REVIEW_QUESTION": _first_sentence(model.get("question") or "", 160),
        "HEADLINE_TAG": " · ".join(headline_bits),
        "MODALITY": "" if profile == "general" else TODO,
        **{field: TODO for field in AUTHORED_FIELDS},
        "EVIDENCE_1": _axis_bullet(strongest) if strongest else "",
        "EVIDENCE_2": _axis_bullet(second) if second else "",
        "EVIDENCE_3": _counterweight(model),
    }
    claims = list(model.get("claims") or [])
    assertions = []
    for index, panel in enumerate(("A", "B", "C")):
        claim = claims[min(index, len(claims) - 1)] if claims else {}
        anchors = list(claim.get("supporting") or claim.get("contradicting") or [])
        assertions.append({
            "assertion_id": f"INFO-{panel}-01",
            "panel": panel,
            "text": TODO,
            "subject": spec["SUBJECT"] or TODO,
            "relation": TODO,
            "object": TODO,
            "direction": "not_applicable",
            "model": "unspecified",
            "outcome": "unspecified",
            "claim_ids": [claim["claim_id"]] if claim.get("claim_id") else [],
            "evidence_ids": [
                str(anchor.get("evidence_id")) for anchor in anchors
                if anchor.get("evidence_id")
            ][:3],
        })
    spec["SCIENTIFIC_ASSERTIONS"] = assertions
    return spec


def _guess_subject(model: dict) -> str:
    """A gene-like token from the title, else the first capitalised word.

    Only a starting point — the author overrides it in the spec, and the header
    is the most visible text on the page so it is worth a second look.
    """
    title = str(model.get("title") or "")
    gene = re.search(r"\b([A-Z][A-Z0-9]{2,7})\b", title)
    return gene.group(1) if gene else (title.split(" ")[0] if title else "")


# --- the prompt -------------------------------------------------------------

def prompt(root: pathlib.Path) -> tuple[str, list[str]]:
    """(prompt text, unresolved placeholders) for this run's infographic.

    The template used to be reachable only by prose: SKILL.md said "full prompt
    in references/infographic_prompt_template.txt" and no code read it. The
    operator therefore rendered from whatever it could recall, and the modality
    shape guide — 60 lines whose single most emphatic rule is that an antibody
    binds through its Fab arms and never its Fc stem — never entered the context
    at the moment the image was generated. Every delivered infographic drew the
    antibody backwards. Instructions one indirection away from the moment they
    are needed are instructions that do not exist, so this emits the whole thing,
    substituted and ready to send.
    """
    spec = _load_spec(root)
    body = "\n".join(line for line in TEMPLATE.read_text(encoding="utf-8").splitlines()
                     if not _TEMPLATE_COMMENT.match(line))

    missing: list[str] = []

    def fill(match: re.Match) -> str:
        key = match.group(1)
        value = str(spec.get(key) or "").strip()
        if not value or value.startswith("TODO"):
            missing.append(key)
            return match.group(0)
        return value

    return _PLACEHOLDER.sub(fill, body).strip() + "\n", sorted(set(missing))


def _load_spec(root: pathlib.Path) -> dict:
    path = root / "deliverables" / SPEC_NAME
    if not path.exists():
        raise SystemExit(f"no spec at {path} — run --seed first")
    return json.loads(path.read_text(encoding="utf-8"))


def _sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def build_tool_request(root: pathlib.Path) -> dict:
    """Exact deferred-tool request for Biomni's required infographic call."""
    failures = verify(root, require_image=False)
    if failures:
        raise SystemExit("infographic spec is not ready for GenerateImage: "
                         + "; ".join(failures))
    prompt_text, missing = prompt(root)
    if missing:
        raise SystemExit("infographic spec has unauthored fields: "
                         + ", ".join(missing))
    expected_path = RESULTS_DIR / IMAGE_NAME
    spec = _load_spec(root)
    antibody_required = _antibody_required(spec)
    required_panel_content = "; ".join(
        f"{spec.get(f'PANEL_{panel}_TITLE')}: "
        f"{spec.get(f'PANEL_{panel}_DESCRIPTION')}"
        for panel in ("A", "B", "C")
    )
    assertion_content = "; ".join(
        f"{row.get('assertion_id')} panel {row.get('panel')}: "
        f"{row.get('subject')} {row.get('relation')} {row.get('object')} "
        f"[direction={row.get('direction')}; model={row.get('model')}; "
        f"outcome={row.get('outcome')}] — {row.get('text')}"
        for row in spec.get("SCIENTIFIC_ASSERTIONS") or []
    )
    prompt_text += (
        "\nSCIENTIFIC ASSERTION CONTRACT — render no causal arrow or outcome "
        "stronger than these evidence-linked statements. Preserve each stated "
        "direction, tested model, and outcome exactly:\n"
        + assertion_content
        + "\n"
    )
    return {
        "schema_version": TOOL_REQUEST_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "load_tool": {
            "tool": "ToolSearch",
            "arguments": {
                "query": TOOL_SEARCH_QUERY,
                "description": "Loading Biomni image generation for the report infographic",
            },
        },
        "arguments": {
            "prompt": prompt_text,
            "file_name": IMAGE_NAME,
            "aspect_ratio": TOOL_ASPECT_RATIO,
            "description": TOOL_DESCRIPTION,
        },
        "expected_result_path": str(expected_path),
        "prompt_sha256": _sha256_bytes(prompt_text.encode("utf-8")),
        "style": {
            "name": "Phylo three-panel scientific schematic",
            "body_font": "DieGrotesk",
            "heading_font": "Signifier",
            "background": "#FAF9F3",
            "subject_accent": "#CC2FB2",
        },
        "media_output_check": {
            "tool": "Read",
            "arguments": {
                "file_path": str(image_path(root)),
                "mode": "media_output_check",
                "media_output_check_prompt": (
                    "Pass only if this is a readable Phylo-styled scientific "
                    "infographic with a warm off-white background, three "
                    "side-by-side panels, the specified evidence strip, clean "
                    "flat line art, and no clipped, garbled, fabricated, or "
                    "misspelled labels. Confirm disputed mechanisms remain "
                    "visibly labelled as proposed/debated and that no population "
                    "is labelled 'only' unless the prompt explicitly grounds "
                    "that exclusivity. Fail on bracketed or numeric citation "
                    "markers such as [1], invented citations, malformed labels, "
                    "or a placeholder/generic non-Phylo image. Compare the "
                    "rendered panels against this required content and fail if "
                    f"a named mechanism, intervention, or outcome is omitted: "
                    f"{required_panel_content}. SCIENTIFIC ASSERTIONS ARE A "
                    "HARD GATE: trace every causal arrow and outcome glyph and "
                    "fail if its direction, tested model, evidence level, or "
                    "outcome differs from these exact assertions; fail any "
                    "extra stronger outcome not listed here (for example tumour "
                    "regression from cell-viability evidence): "
                    f"{assertion_content}. "
                    + (
                        "ANTIBODY ANATOMY IS A HARD GATE: trace each antibody "
                        "as one connected Y. The antigen/target and pink binding "
                        "halo must contact a variable domain at the extreme tip "
                        "of a Fab arm. The Fc constant-region stem must point "
                        "away and contact neither antigen, target, nor membrane. "
                        "Fail if the Fc stem is the binding end, if the halo is "
                        "on Fc, or if Fab arms are detached. "
                        if antibody_required else ""
                    )
                ),
            },
            "required_checks": {
                "panel_content_complete": "pass",
                "safe_margins": "pass",
                "scientific_assertions_correct": "pass",
                "model_and_outcome_scope_correct": "pass",
                "antibody_binding_orientation": (
                    "pass" if antibody_required else "not_applicable"
                ),
            },
        },
    }


def write_tool_request(root: pathlib.Path) -> pathlib.Path:
    """Persist the exact request so the active Biomni agent can execute it."""
    destination = root / TOOL_REQUEST_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(
        json.dumps(build_tool_request(root), indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return destination


def image_path(root: pathlib.Path, contract: dict | None = None) -> pathlib.Path:
    """The canonical image path declared by the report contract."""
    visual = ((contract if contract is not None else load_contract())
              .get("visual_abstract") or {})
    return root / str(visual.get("image") or f"deliverables/{IMAGE_NAME}")


def image_failure(path: pathlib.Path) -> str | None:
    """Return why ``path`` is not a usable image, or ``None`` when it is."""
    if not path.exists():
        return f"no infographic image at {path}"
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
        if width <= 0 or height <= 0:
            return f"infographic image has zero extent: {path}"
    except Exception as exc:  # noqa: BLE001 - every decode failure is actionable
        return (f"infographic image is unreadable at {path} "
                f"({type(exc).__name__}: {exc})")
    return None


def install_image(root: pathlib.Path, source: pathlib.Path) -> pathlib.Path:
    """Validate a GenerateImage result and install it at the contract path."""
    request_path = root / TOOL_REQUEST_PATH
    if not request_path.exists():
        raise SystemExit(
            f"no GenerateImage tool request at {request_path} — run "
            "--write-tool-request, then execute the actual Biomni tool call")
    try:
        recorded_request = json.loads(request_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise SystemExit(f"GenerateImage tool request is invalid JSON: {exc.msg}")
    if not isinstance(recorded_request, dict):
        raise SystemExit("GenerateImage tool request must be a JSON object")
    current_request = build_tool_request(root)
    if recorded_request != current_request:
        raise SystemExit(
            "GenerateImage tool request is stale for the current infographic "
            "spec — rewrite the request and regenerate the image")

    source = source.resolve()
    if source.parent != RESULTS_DIR.resolve():
        raise SystemExit(
            f"infographic source must be the /mnt/results path returned by the "
            f"Biomni {TOOL_NAME} call, got {source}")
    failure = image_failure(source)
    if failure:
        raise SystemExit(failure)
    destination = image_path(root)
    destination.parent.mkdir(parents=True, exist_ok=True)
    compose(source, destination, _load_spec(root))
    receipt = {
        "schema_version": TOOL_REQUEST_SCHEMA_VERSION,
        "tool": TOOL_NAME,
        "source_path": str(source),
        "installed_path": str(destination),
        "request_sha256": _sha256_bytes(request_path.read_bytes()),
        "prompt_sha256": recorded_request["prompt_sha256"],
        "image_sha256": _sha256_bytes(destination.read_bytes()),
    }
    receipt_path = root / GENERATION_RECEIPT_PATH
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(
        json.dumps(receipt, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    # A visual check applies to exact image bytes. Installing or regenerating an
    # image invalidates any earlier attestation.
    (root / MEDIA_CHECK_RECEIPT_PATH).unlink(missing_ok=True)
    return destination


def _antibody_required(spec: dict) -> bool:
    return bool(ANTIBODY_RE.search(" ".join(
        str(spec.get(key) or "")
        for key in ("MODALITY", "PANEL_A_DESCRIPTION", "PANEL_B_DESCRIPTION",
                    "PANEL_C_DESCRIPTION")
    )))


def record_media_check(
    root: pathlib.Path,
    status: str,
    detail: str,
    *,
    panel_content_complete: str,
    safe_margins: str,
    scientific_assertions_correct: str,
    model_and_outcome_scope_correct: str,
    antibody_binding_orientation: str,
) -> pathlib.Path:
    """Persist the result of Biomni Read(media_output_check) for exact bytes."""
    if status not in {"pass", "fail"}:
        raise ValueError("media check status must be 'pass' or 'fail'")
    checks = {
        "panel_content_complete": panel_content_complete,
        "safe_margins": safe_margins,
        "scientific_assertions_correct": scientific_assertions_correct,
        "model_and_outcome_scope_correct": model_and_outcome_scope_correct,
        "antibody_binding_orientation": antibody_binding_orientation,
    }
    allowed = {MEDIA_CHECK_PASS, "fail", MEDIA_CHECK_NOT_APPLICABLE}
    if any(value not in allowed for value in checks.values()):
        raise ValueError(
            "media checks must be pass, fail, or not_applicable"
        )
    spec = _load_spec(root)
    required = {
        "panel_content_complete": MEDIA_CHECK_PASS,
        "safe_margins": MEDIA_CHECK_PASS,
        "scientific_assertions_correct": MEDIA_CHECK_PASS,
        "model_and_outcome_scope_correct": MEDIA_CHECK_PASS,
        "antibody_binding_orientation": (
            MEDIA_CHECK_PASS if _antibody_required(spec)
            else MEDIA_CHECK_NOT_APPLICABLE
        ),
    }
    if status == "pass" and checks != required:
        raise ValueError(
            "media check cannot pass until every required structured check passes"
        )
    image = image_path(root)
    failure = image_failure(image)
    if failure:
        raise SystemExit(failure)
    destination = root / MEDIA_CHECK_RECEIPT_PATH
    destination.parent.mkdir(parents=True, exist_ok=True)
    destination.write_text(json.dumps({
        "schema_version": 1,
        "tool": "Read",
        "mode": "media_output_check",
        "status": status,
        "detail": str(detail or "").strip(),
        "checks": checks,
        "image_path": str(image),
        "image_sha256": _sha256_bytes(image.read_bytes()),
        "checked_at": datetime.datetime.now(datetime.timezone.utc).isoformat(),
    }, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    return destination


def _generation_failures(root: pathlib.Path, image: pathlib.Path) -> list[str]:
    """Validate the durable request/receipt around the Biomni tool boundary."""
    request_path = root / TOOL_REQUEST_PATH
    receipt_path = root / GENERATION_RECEIPT_PATH
    if not request_path.exists():
        return [f"no GenerateImage tool request at {request_path}; a PNG alone "
                "does not prove that Biomni executed GenerateImage"]
    if not receipt_path.exists():
        return [f"no GenerateImage generation receipt at {receipt_path}; install "
                "the path returned by the actual tool call"]
    try:
        recorded_request = json.loads(request_path.read_text(encoding="utf-8"))
        receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"GenerateImage request/receipt is invalid JSON ({exc.msg})"]
    if not isinstance(recorded_request, dict) or not isinstance(receipt, dict):
        return ["GenerateImage request and receipt must be JSON objects"]

    failures: list[str] = []
    try:
        current_request = build_tool_request(root)
    except SystemExit as exc:
        return [str(exc)]
    if recorded_request != current_request:
        failures.append(
            "GenerateImage tool request is stale for the current infographic spec")
    if recorded_request.get("tool") != TOOL_NAME:
        failures.append(f"infographic tool request must name {TOOL_NAME}")
    if receipt.get("tool") != TOOL_NAME:
        failures.append(f"infographic generation receipt must name {TOOL_NAME}")
    if receipt.get("request_sha256") != _sha256_bytes(request_path.read_bytes()):
        failures.append("GenerateImage request hash does not match the generation receipt")
    if receipt.get("prompt_sha256") != recorded_request.get("prompt_sha256"):
        failures.append("GenerateImage prompt hash does not match the generation receipt")
    if receipt.get("image_sha256") != _sha256_bytes(image.read_bytes()):
        failures.append("infographic image hash does not match the generation receipt")
    source = pathlib.Path(str(receipt.get("source_path") or "")).resolve()
    if source.parent != RESULTS_DIR.resolve():
        failures.append("generation receipt does not name a Biomni results path")
    if receipt.get("installed_path") != str(image):
        failures.append("generation receipt does not name the installed infographic")
    media_path = root / MEDIA_CHECK_RECEIPT_PATH
    if not media_path.exists():
        failures.append(
            "no durable media output check receipt; inspect the final installed "
            "image with Biomni Read(media_output_check), then record the result"
        )
    else:
        try:
            media = json.loads(media_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError as exc:
            failures.append(f"media output check receipt is invalid JSON ({exc.msg})")
        else:
            if media.get("tool") != "Read" or media.get("mode") != "media_output_check":
                failures.append("media output check receipt does not identify Biomni Read")
            if media.get("status") != "pass":
                failures.append("media output check did not pass")
            required_checks = {
                "panel_content_complete": MEDIA_CHECK_PASS,
                "safe_margins": MEDIA_CHECK_PASS,
                "scientific_assertions_correct": MEDIA_CHECK_PASS,
                "model_and_outcome_scope_correct": MEDIA_CHECK_PASS,
                "antibody_binding_orientation": (
                    MEDIA_CHECK_PASS if _antibody_required(_load_spec(root))
                    else MEDIA_CHECK_NOT_APPLICABLE
                ),
            }
            if media.get("checks") != required_checks:
                failures.append(
                    "media output check lacks the required panel, margin, or "
                    "antibody-binding anatomy verdicts"
                )
            if media.get("image_sha256") != _sha256_bytes(image.read_bytes()):
                failures.append("media output check applies to stale infographic bytes")
    return failures


# --- verification -----------------------------------------------------------

# Tokens worth checking: numbers with a decimal or 3+ digits, p-values, ORs,
# percentages, rsIDs, DOIs, and CamelCase/UPPER identifiers. Deliberately NOT
# every integer — "3 independent studies" is a count this script itself wrote,
# and "two" and "50%" are ordinary prose.
_CHECKABLE = re.compile(
    r"\b(?:rs\d{3,}|GCST\d+|PMID:?\s*\d+|10\.\d{4,}/\S+"
    r"|[Pp]\s*[<>=]\s*[\d.]+(?:e-?\d+)?"
    r"|\d+\.\d+(?:e-?\d+)?"
    r"|\d{3,}"
    r"|\d+(?:\.\d+)?\s*(?:%|-fold|×|x)\b)")

# Citation shapes the author might type: "Ward et al. 2024", "Finch and Baker 2009".
_CITATION = re.compile(
    r"\b([A-Z][A-Za-zÀ-ɏ-]+)\s+(?:et al\.?|and\s+[A-Z][A-Za-z-]+)\s+(\d{4})\b")

# "Works only in X" turns an untested population into a negative result. The
# visual abstract may use exclusive wording only when accepted evidence uses
# that same wording; selected/enriched contexts should otherwise stay selected.
_EXCLUSIVE_WORDING = re.compile(
    r"\b(?:works?|responds?|effective|benefits?|dependency)\s+only\b"
    r"|\bonly\s+in\s+the\b",
    re.IGNORECASE,
)


def _evidence_haystack(root: pathlib.Path, model: dict) -> str:
    """Everything the review actually grounded, as one searchable string."""
    parts: list[str] = []
    for row in read_jsonl(root / "evidence" / "evidence.jsonl"):
        parts += [str(row.get("quote") or ""), str(row.get("source_locator") or ""),
                  str(row.get("doi") or ""), str(row.get("scope_note") or "")]
    for claim in model["claims"]:
        parts += [claim["claim_text"], claim["scope"]]
        for facet in (claim.get("narrative") or {}).values():
            parts.append(str(facet.get("text") or ""))
    for statements in (model.get("sections") or {}).values():
        parts += [s["text"] for s in statements]
    for ref in model["references"]:
        parts += [ref["citation"], ref["title"], str(ref["year"]), ref["doi"],
                  ref["authors"]]
    for row in model.get("synthesis_table") or []:
        parts += [row["bottom_line"], row["support_label"]]
        parts += [s["citation"] for s in row.get("sources") or []]
    # Counts this script computed are legitimate even though no quote states
    # them; include them so a seeded HEADLINE_TAG verifies against itself.
    parts.append(f"{len(model['claims'])} grounded claims")
    parts.append(str(sum((model.get("panel_studies") or {}).values())))
    parts += list(SUPPORT_LABEL.values())
    return "\n".join(parts)


def verify(root: pathlib.Path, *, require_image: bool = True,
           require_generation: bool = False) -> list[str]:
    """Failures in the evidence-backed spec and its required rendered image."""
    spec_path = root / "deliverables" / SPEC_NAME
    if not spec_path.exists():
        return [f"no infographic spec at {spec_path} — run --seed, author the "
                "panel descriptions, then --verify"]
    try:
        spec = json.loads(spec_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return [f"{SPEC_NAME} is not valid JSON ({exc.msg})"]

    failures: list[str] = []
    contract = load_contract()
    model = build_model(root, contract)
    haystack = _evidence_haystack(root, model)
    haystack_norm = re.sub(r"\s+", " ", haystack)

    outstanding = [k for k, v in spec.items()
                   if isinstance(v, str) and v.strip().startswith("TODO")]
    if outstanding:
        failures.append(
            f"{len(outstanding)} field(s) still unauthored: "
            f"{', '.join(sorted(outstanding))}. The infographic is the first "
            "thing a reader sees; a placeholder there is worse than no image.")

    if spec.get("PROFILE") not in {"target", "general"}:
        failures.append(
            f"PROFILE must be 'target' or 'general', got {spec.get('PROFILE')!r}")

    # The third bullet is the counterweight — the safety signal, contradiction,
    # null result or evidence gap. It is not optional: this report's contract
    # requires the review to have looked for disconfirming evidence, so an
    # infographic that shows only supporting bullets misrepresents the document
    # it introduces. Seeding leaves it empty only when the run genuinely has
    # nothing of the kind, which is itself worth stopping on.
    if not str(spec.get("EVIDENCE_3") or "").strip():
        failures.append(
            "EVIDENCE_3 is empty. The third bullet carries the counterweight — "
            "the contradiction, safety signal, null result or evidence gap. A "
            "summary graphic showing only supporting evidence misrepresents a "
            "review whose contract requires a contradiction axis; if the run "
            "truly found none, that absence is the thing to state.")

    evidence_by_id = {
        str(row.get("evidence_id") or ""): row
        for row in read_jsonl(root / "evidence" / "evidence.jsonl")
        if str(row.get("evidence_id") or "")
    }
    assertions = spec.get("SCIENTIFIC_ASSERTIONS")
    if not isinstance(assertions, list):
        failures.append(
            "SCIENTIFIC_ASSERTIONS must be a list of atomic, evidence-linked "
            "panel assertions with direction, model, and outcome fields"
        )
        assertions = []
    panels = {
        str(row.get("panel") or "") for row in assertions
        if isinstance(row, dict)
    }
    for panel in ("A", "B", "C"):
        if panel not in panels:
            failures.append(
                f"SCIENTIFIC_ASSERTIONS has no assertion for panel {panel}"
            )
    known_claims = {
        str(row.get("claim_id") or "") for row in model.get("claims") or []
    }
    for index, assertion in enumerate(assertions, 1):
        where = f"SCIENTIFIC_ASSERTIONS[{index}]"
        if not isinstance(assertion, dict):
            failures.append(f"{where}: expected an object")
            continue
        if any(
            isinstance(value, str) and value.strip().startswith("TODO")
            for value in assertion.values()
        ):
            failures.append(f"{where}: contains an unauthored TODO field")
        failures += assertion_errors(assertion, evidence_by_id, where=where)
        unknown_claims = sorted(
            {str(value) for value in assertion.get("claim_ids") or []}
            - known_claims
        )
        if unknown_claims:
            failures.append(
                f"{where}: unknown claim_ids: {', '.join(unknown_claims)}"
            )

    for key, value in sorted(spec.items()):
        if key.startswith("_") or not isinstance(value, str):
            continue
        for match in _CHECKABLE.finditer(value):
            token = match.group(0).strip()
            if _normalized_in(token, haystack_norm):
                continue
            failures.append(
                f"{key}: {token!r} appears nowhere in this run's accepted "
                "evidence, claims, sections or references. The infographic sits "
                "above a report where every number is quotable; either ground "
                "it or remove it.")
        for match in _CITATION.finditer(value):
            citation = f"{match.group(1)} {match.group(2)}"
            if match.group(1).lower() in haystack_norm.lower() and \
                    match.group(2) in haystack_norm:
                continue
            failures.append(
                f"{key}: cites {citation!r}, which is not in this review's "
                "reference list — an infographic may only cite what the report "
                "cites.")
        if key in {*AUTHORED_FIELDS, "MODALITY"}:
            for match in _EXCLUSIVE_WORDING.finditer(value):
                wording = match.group(0)
                if _normalized_in(wording, haystack_norm):
                    continue
                failures.append(
                    f"{key}: exclusive wording {wording!r} appears nowhere in "
                    "accepted evidence. State the observed enriched/selected "
                    "context and mark untested populations as untested; do not "
                    "turn missing evidence into 'only'."
                )

    visual = contract.get("visual_abstract") or {}
    image = image_path(root, contract)
    image_required = model.get("mode") in set(visual.get("required_modes") or [])
    if require_image and (image_required or image.exists()):
        failure = image_failure(image)
        if failure:
            failures.append(failure)
        elif require_generation:
            failures += _generation_failures(root, image)
    return failures


def _normalized_in(token: str, haystack: str) -> bool:
    """Substring test tolerant of the spacing variants extraction produces."""
    squashed = re.sub(r"\s+", "", token)
    if squashed and squashed in re.sub(r"\s+", "", haystack):
        return True
    return token in haystack


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", required=True)
    parser.add_argument("--seed", action="store_true",
                        help="write a spec pre-filled from the report model")
    parser.add_argument("--verify", action="store_true",
                        help="fail if the spec states anything the evidence does not")
    parser.add_argument("--prompt", action="store_true",
                        help="print the complete image prompt, ready to send")
    parser.add_argument("--write-tool-request", action="store_true",
                        help="write the exact ToolSearch and GenerateImage request")
    parser.add_argument("--install-image", default=None,
                        help="validate and copy a GenerateImage result to the "
                             "contract-declared deliverable path")
    parser.add_argument("--record-media-check", choices=("pass", "fail"),
                        help="record Biomni Read(media_output_check) for the "
                             "final installed image")
    parser.add_argument("--media-check-detail", default="",
                        help="short result from the visual inspection")
    parser.add_argument("--panel-content-check",
                        choices=("pass", "fail", "not_applicable"))
    parser.add_argument("--safe-margins-check",
                        choices=("pass", "fail", "not_applicable"))
    parser.add_argument("--scientific-assertions-check",
                        choices=("pass", "fail", "not_applicable"))
    parser.add_argument("--model-outcome-scope-check",
                        choices=("pass", "fail", "not_applicable"))
    parser.add_argument("--antibody-binding-check",
                        choices=("pass", "fail", "not_applicable"))
    parser.add_argument("--force", action="store_true",
                        help="overwrite an existing spec when seeding")
    args = parser.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    if not (args.seed or args.verify or args.prompt or args.write_tool_request
            or args.install_image or args.record_media_check):
        parser.error("choose --seed, --verify, --prompt, --write-tool-request "
                     "--install-image, or --record-media-check")

    if args.seed:
        out = root / "deliverables" / SPEC_NAME
        if out.exists() and not args.force:
            print(f"SPEC EXISTS: {out} (use --force to overwrite)")
        else:
            out.parent.mkdir(parents=True, exist_ok=True)
            spec = seed(root)
            out.write_text(json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                           encoding="utf-8")
            todo = sum(1 for v in spec.values()
                       if isinstance(v, str) and v.startswith("TODO"))
            print(f"INFOGRAPHIC-SPEC: profile={spec['PROFILE']} "
                  f"authored_fields_remaining={todo} -> {out}")

    if args.prompt:
        failures = verify(root, require_image=False)
        if failures:
            for failure in failures:
                print(f"FAIL: {failure}", file=sys.stderr)
            print("REFUSING: infographic prompt is not grounded in the verified "
                  "review artifacts.", file=sys.stderr)
            return min(255, len(failures))
        text, missing = prompt(root)
        if missing:
            print(f"REFUSING: {len(missing)} placeholder(s) unauthored: "
                  f"{', '.join(missing)}. Author them in {SPEC_NAME}, then "
                  "re-run --prompt.", file=sys.stderr)
            return 1
        print(text)
        return 0

    if args.write_tool_request:
        destination = write_tool_request(root)
        print(f"INFOGRAPHIC-TOOL-REQUEST: {destination}")
        print("AGENT TOOL REQUIRED: load ToolSearch query "
              f"{TOOL_SEARCH_QUERY!r}, wait, then call the loaded {TOOL_NAME} "
              "tool with request.arguments. Text describing the call does not "
              "execute it.")
        return 0

    if args.install_image:
        destination = install_image(root, pathlib.Path(args.install_image))
        print(f"INFOGRAPHIC-IMAGE: installed -> {destination}")
        return 0

    if args.record_media_check:
        destination = record_media_check(
            root, args.record_media_check, args.media_check_detail,
            panel_content_complete=args.panel_content_check or "fail",
            safe_margins=args.safe_margins_check or "fail",
            scientific_assertions_correct=(
                args.scientific_assertions_check or "fail"
            ),
            model_and_outcome_scope_correct=(
                args.model_outcome_scope_check or "fail"
            ),
            antibody_binding_orientation=args.antibody_binding_check or "fail",
        )
        print(f"INFOGRAPHIC-MEDIA-CHECK: {args.record_media_check} -> {destination}")
        return 0

    if args.verify:
        failures = verify(root, require_generation=True)
        # Stamp the result INTO the spec. The PDF caption tells the reader
        # whether the numbers above them were checked, and it can only do that
        # if the check leaves a trace the builder can read.
        spec_path = root / "deliverables" / SPEC_NAME
        if spec_path.exists():
            try:
                spec = json.loads(spec_path.read_text(encoding="utf-8"))
                spec["_verified"] = not failures
                spec_path.write_text(
                    json.dumps(spec, indent=2, ensure_ascii=False) + "\n",
                    encoding="utf-8")
            except json.JSONDecodeError:
                pass
        for failure in failures:
            print(f"FAIL: {failure}")
        print(f"INFOGRAPHIC-SPEC: failures={len(failures)} "
              f"result={'pass' if not failures else 'fail'}")
        return min(255, len(failures))
    return 0


if __name__ == "__main__":
    sys.exit(main())
