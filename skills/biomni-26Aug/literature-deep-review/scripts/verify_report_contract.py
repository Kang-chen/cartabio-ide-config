#!/usr/bin/env python3
"""Validate a delivered report against ``templates/report_contract.json``.

This is the gate that the older figure gates could not be. ``verify_pdf_assets``
asks "did the PDF embed the figures this run produced?" — which passes when the
run produced one figure. This asks "does the report meet the standard a
figure-level review is held to?", with thresholds that live in the contract and
do not move when a run underperforms.

Checks, in order of how badly they bite:

1. required sections are present AS HEADINGS — a line that is exactly the
   heading, never the word appearing somewhere in a paragraph;
2. the visual abstract, evidence-axis synthesis table, and synthesis panel are
   present for the modes that require them — and for the two that are images,
   their caption marker must sit on a page that actually embeds an image;
3. enough real paper figures are embedded — both an absolute floor by mode and
   a fraction of the figures that were actually *obtainable* (cited papers whose
   source was a PDF, so a crop was physically possible) — and the PDF carries at
   least that many images, so counting captions can never stand in for crops;
4. every grounded claim's verbatim anchor appears in the PDF text;
5. locators are clean (no "Unknown" section, no whole-heading section labels);
6. references carry no scraped page-chrome and none are uncited;
7. quotes carry no trailing fragments, merged words, or figure-label bleed.

Exit code is the failure count, so it drops straight into a shell gate.
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys
from typing import Any
from support_policy import UNGROUNDED_STATES as ungrounded  # noqa: E402

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

DEFAULT_CONTRACT = SCRIPTS.parent / "templates" / "report_contract.json"
FIGURE_FLOOR_CONFIG_KEY = "minimum_paper_figures"


def _norm(text: str) -> str:
    """Whitespace-insensitive, case-insensitive form for substring matching."""
    return re.sub(r"\s+", " ", (text or "")).strip().lower()


def _load_json(path: pathlib.Path) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    rows: list[dict] = []
    if not path.exists():
        return rows
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            rows.append(json.loads(line))
        except Exception:
            continue
    return rows


def requested_figure_floor(
    root: pathlib.Path, contract: dict, mode: str
) -> tuple[int, str]:
    """Resolve the user's run-level figure floor, then the mode default."""
    spec = contract.get("paper_figures", {}) if isinstance(contract, dict) else {}
    by_mode = spec.get("min_by_mode", {})
    if not isinstance(by_mode, dict):
        by_mode = {}
    default = int(by_mode.get(mode, 0) or 0)

    manifest = _load_json(root / "run_manifest.json")
    config = manifest.get("config", {}) if isinstance(manifest, dict) else {}
    raw = config.get(FIGURE_FLOOR_CONFIG_KEY) if isinstance(config, dict) else None
    policy = config.get("figure_count_policy") if isinstance(config, dict) else None
    if policy not in {None, "fixed", "adaptive"}:
        raise ValueError(
            "run_manifest.config.figure_count_policy must be fixed or adaptive"
        )
    if policy == "adaptive" and raw is None:
        raise ValueError(
            "adaptive figure minimum is unresolved; run intake_policy.py "
            "--resolve-adaptive after the figure inventory and before assembly"
        )
    if raw is None:
        return default, f"contract mode default ({mode}={default})"
    if isinstance(raw, bool) or not isinstance(raw, int) or raw < 0:
        raise ValueError(
            f"run_manifest.config.{FIGURE_FLOOR_CONFIG_KEY} must be a "
            f"non-negative integer or null, got {raw!r}"
        )
    source = (
        "adaptive resolved run minimum"
        if policy == "adaptive"
        else "user-selected run minimum"
    )
    return raw, f"{source} ({raw})"


def _pdf_pages(pdf_path: pathlib.Path) -> list[tuple[str, int]]:
    """[(page_text, images_on_page), ...] — page-local, because whole-document
    substring matching is what let a prose-only PDF pass every marker check."""
    import pypdf

    reader = pypdf.PdfReader(str(pdf_path), strict=True)
    pages: list[tuple[str, int]] = []
    for page in reader.pages:
        text = page.extract_text() or ""
        images = 0
        xobj = (page.get("/Resources", {}) or {}).get("/XObject")
        if xobj:
            try:
                for _, ref in xobj.get_object().items():
                    obj = ref.get_object()
                    if obj.get("/Subtype") == "/Image":
                        images += 1
            except Exception:
                pass
        pages.append((text, images))
    return pages


# --- individual checks -----------------------------------------------------

def _check_sections(contract: dict, pdf_lines: list[str],
                    failures: list[str], notes: list[str]) -> None:
    """A required section must appear as a HEADING — its own line.

    Substring matching over the whole document treated any occurrence of the
    word as proof: a single paragraph of prose containing "results" and
    "references" satisfied the entire section list, and a one-paragraph PDF
    with no headings at all passed clean.
    """
    present: list[str] = []
    for section in contract.get("required_sections", []):
        pattern = re.compile(
            r"^(?:\d+[.)]\s*)?" + re.escape(_norm(section)) + r":?$")
        if any(pattern.match(line) for line in pdf_lines):
            present.append(section)
        else:
            failures.append(
                f"required section missing from the report: {section!r} — no "
                "line in the extracted text is exactly this heading (a mention "
                "inside a paragraph is not a section)"
            )
    if present:
        notes.append(f"sections: {len(present)} required heading(s) found as headings")


def _check_marker(spec: dict, mode: str, pages: list[tuple[str, int]], label: str,
                  failures: list[str], notes: list[str], *,
                  require_image_on_page: bool = False) -> None:
    """The marker must appear; for image assets, on a page that HAS an image.

    A caption marker is only evidence that an asset shipped if the asset shipped
    with it. Matching the marker anywhere in the document meant the phrase
    "visual abstract" occurring in body prose reported "visual abstract:
    present" for a report containing no infographic.
    """
    raw_marker = spec.get("caption_marker", "")
    # The infographic's marker changed from "visual abstract" to "infographic".
    # Both are accepted so a run built before the change still verifies against
    # the current contract — a marker rename must not retroactively fail an
    # otherwise sound report.
    accepted = spec.get("accept_markers") or []
    marker = _norm(raw_marker)
    if not marker:
        # An empty marker used to take the else-branch and report "present",
        # so deleting the marker from the contract disabled the check silently.
        failures.append(
            f"{label}: report_contract.json defines no caption_marker, so its "
            "presence cannot be verified — this is a contract configuration "
            "error, not a passing check"
        )
        return
    if mode not in spec.get("required_modes", []):
        notes.append(f"{label}: not required for mode={mode}")
        return
    markers = [marker] + [_norm(m) for m in accepted if _norm(m)]
    hits = [(text, images) for text, images in pages
            if any(m in _norm(text) for m in markers)]
    if not hits:
        failures.append(
            f"{label} is required for mode={mode} but its caption marker "
            f"{raw_marker!r} was not found in the report text"
        )
        return
    if require_image_on_page and not any(images for _, images in hits):
        failures.append(
            f"{label} is required for mode={mode}: its caption marker "
            f"{raw_marker!r} appears in the text, but on no page that embeds an "
            "image — the caption shipped without the asset it describes"
        )
        return
    notes.append(f"{label}: present")


def croppable_supply(root: pathlib.Path) -> dict:
    """Everything the figure requirement's denominator depends on, measurable.

    A paper can yield a crop only if its parsed record carries at least one
    figure with a real ``image_path``. JATS-XML sources never do (parse_jats
    sets image_path=None), and vector-only PDFs often do not either — so this
    is the honest denominator for "did we use the figures we could have used".

    Two ways the denominator used to collapse to zero without saying anything:

    * ``image_path`` may be RELATIVE, and ``pathlib.Path(p).exists()`` resolves
      it against the process CWD. The same run measured croppable={'P1'} from
      the run root and croppable=set() from ``scripts/`` — the requirement
      silently dropped from 3 to 0 depending on where the gate was invoked.
    * A missing ``fulltext/parsed/`` yields zero croppable papers, which reads
      identically to "no supply was required". ``measurable`` distinguishes
      them so the caller can fail instead of pass.
    """
    evidence = _read_jsonl(root / "evidence" / "evidence.jsonl")
    cited = {str(r.get("paper_id")) for r in evidence
             if r.get("stance") in {"supports", "contradicts"} and r.get("paper_id")}

    from reuse_rights import rights_record

    rights_by_paper: dict[str, bool] = {}
    for relpath in ("fulltext/papers.jsonl", "corpus/references.jsonl"):
        for row in _read_jsonl(root / relpath):
            paper_id = str(row.get("paper_id") or "")
            if not paper_id or paper_id in rights_by_paper:
                continue
            if "figure_embedding_allowed" in row:
                rights_by_paper[paper_id] = bool(row["figure_embedding_allowed"])
            else:
                access_evidence = row.get("access_evidence") or {}
                rights_by_paper[paper_id] = bool(rights_record(
                    access_evidence.get("license") or row.get("license"),
                    row.get("access_state"),
                )["figure_embedding_allowed"])
    rights_eligible = {paper_id for paper_id in cited
                       if rights_by_paper.get(paper_id) is True}
    rights_ineligible = cited - rights_eligible
    run_manifest = _load_json(root / "run_manifest.json") or {}
    run_config = (run_manifest.get("config") or {}
                  if isinstance(run_manifest, dict) else {})
    reuse_policy = str(run_config.get("figure_reuse_policy")
                       or "reuse_cleared_only")
    reuse_source = str(run_config.get("figure_reuse_decision_source") or "")
    user_directed = (
        reuse_policy == "user_directed" and reuse_source == "explicit_user"
    )
    policy_eligible = set(cited) if user_directed else set(rights_eligible)

    croppable: set[str] = set()
    croppable_figures: set[tuple[str, str]] = set()
    parsed_dir = root / "fulltext" / "parsed"
    parsed_present = parsed_dir.exists()
    parsed_records = 0
    if parsed_present:
        for pjson in sorted(parsed_dir.glob("*.json")):
            parsed = _load_json(pjson)
            if not isinstance(parsed, dict):
                continue
            parsed_records += 1
            pid = str(parsed.get("paper_id") or "")
            if pid not in policy_eligible:
                continue
            for index, fig in enumerate(parsed.get("figures", []) or [], 1):
                image_path = fig.get("image_path")
                if not image_path:
                    continue
                candidate = pathlib.Path(str(image_path))
                if not candidate.is_absolute():
                    candidate = root / candidate
                if candidate.exists():
                    croppable.add(pid)
                    figure_id = str(fig.get("figure_id") or f"index-{index}")
                    croppable_figures.add((pid, figure_id))
    return {
        "cited": cited,
        "rights_eligible": rights_eligible,
        "rights_ineligible": rights_ineligible,
        "policy_eligible": policy_eligible,
        "figure_reuse_policy": (
            "user_directed" if user_directed else "reuse_cleared_only"
        ),
        "croppable": croppable,
        "croppable_figures": croppable_figures,
        "parsed_dir": parsed_dir,
        "parsed_dir_present": parsed_present,
        "parsed_records": parsed_records,
        # "we measured a supply of zero" vs "we could not measure the supply".
        "measurable": (not policy_eligible) or bool(croppable),
    }


def supply_failure(supply: dict) -> str | None:
    """The distinct 'supply not measurable' failure, or None when it is."""
    if supply["measurable"]:
        return None
    if not supply["parsed_dir_present"]:
        return (
            "croppable-figure supply is NOT MEASURABLE: "
            f"{len(supply['policy_eligible'])} policy-eligible cited paper(s) "
            f"but {supply['parsed_dir']} does not exist, so the "
            "figure requirement's denominator collapses to zero. This is not "
            "'no figures required' — reparse the full texts (with figure "
            "extraction) before trusting any figure count."
        )
    return (
        "croppable-figure supply is NOT MEASURABLE: "
        f"{len(supply['policy_eligible'])} policy-eligible cited paper(s) and "
        f"{supply['parsed_records']} parsed record(s), but none "
        "carries a figure with an existing image_path. Either figure extraction "
        "did not run, or the recorded image_path values do not resolve against "
        f"the run root ({supply['parsed_dir'].parent.parent}). The figure "
        "requirement cannot be computed from a supply of zero."
    )


def _check_paper_figures(contract: dict, mode: str, root: pathlib.Path,
                         pdf_norm: str, total_images: int,
                         failures: list[str], notes: list[str],
                         other_required_images: int = 0) -> dict:
    spec = contract.get("paper_figures", {})
    prefix = _norm(spec.get("caption_prefix", "Report Figure"))
    # Count DISTINCT figure numbers, not prefix occurrences: each figure is
    # named twice in a well-formed report (its caption under the claim it
    # grounds, and again in the Figures list), so counting occurrences would
    # report double the real number and let half the required figures go
    # missing unnoticed.
    observed = len(set(re.findall(re.escape(prefix) + r"\s*(\d+)", pdf_norm))) \
        if prefix else 0

    try:
        selected_floor, floor_source = requested_figure_floor(root, contract, mode)
    except (TypeError, ValueError) as exc:
        failures.append(f"invalid figure minimum: {exc}")
        selected_floor, floor_source = 0, "invalid run configuration"
    supply = croppable_supply(root)
    cited, croppable = supply["cited"], supply["croppable"]
    croppable_figures = supply["croppable_figures"]
    frac = float(spec.get("min_fraction_of_croppable", 0) or 0)
    paper_required = math.ceil(frac * len(croppable)) if croppable else 0
    figure_required = selected_floor
    required = max(figure_required, paper_required)
    manifest = _load_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    )
    manifest_figures = manifest.get("figures", []) if isinstance(manifest, dict) else []
    contributing_papers = {
        str(row.get("paper_id"))
        for row in manifest_figures
        if isinstance(row, dict)
        and row.get("status") == "exported"
        and row.get("paper_id")
    } & croppable

    not_measurable = supply_failure(supply)
    if not_measurable:
        failures.append(not_measurable)

    notes.append(
        f"paper figures: observed={observed}, required>={required} "
        f"(figure floor={figure_required} from {floor_source}; available="
        f"{len(croppable_figures)} policy-eligible crops under "
        f"{supply['figure_reuse_policy']}; paper coverage="
        f"{len(contributing_papers)}/"
        f"{paper_required}, requiring {frac:.0%} of {len(croppable)} croppable "
        f"cited papers; {len(cited)} cited papers total)"
    )
    if observed < required:
        missing = sorted(croppable)
        failures.append(
            f"too few real paper figures embedded: observed={observed} < required "
            f"{required}. {len(croppable)} cited paper(s) had crops available "
            f"({', '.join(missing[:8])}{'...' if len(missing) > 8 else ''}). "
            "Figure selection no longer requires a caption anchor (see scripts/figure_selection): a figure is chosen when its caption scores against the claim text. A shortfall now means the crops were not produced, the captions were not specific enough to any claim, or the selection caps are too tight — read selection_rejected in figures_manifest.json, which records the cause for every figure passed over."
        )
    if len(contributing_papers) < paper_required:
        unused = sorted(croppable - contributing_papers)
        failures.append(
            f"too few cited papers contribute real figures: observed="
            f"{len(contributing_papers)} < required {paper_required} "
            f"({frac:.0%} of {len(croppable)} policy-eligible croppable cited "
            f"papers). Papers with available crops but no exported figure: "
            f"{', '.join(unused[:8])}{'...' if len(unused) > 8 else ''}."
        )
    if required > 0 and observed >= required:
        # Labels are TEXT. Counting them alone is satisfied by a report that
        # printed "Report Figure 1..5" in its Figures list and embedded not one
        # image — which is exactly what a build that skipped every missing crop
        # produced, and both gates passed it at 5/4. The embedded-image count is
        # the backstop: every required paper figure is an image, and so is each
        # of the other assets this mode requires.
        min_images = required + other_required_images
        if total_images < min_images:
            failures.append(
                f"figure LABELS without IMAGES: the report names {observed} "
                f"'Report Figure' captions but embeds only {total_images} image(s), "
                f"and >={min_images} are required ({required} paper figure(s) + "
                f"{other_required_images} other required visual asset(s)). "
                "Counting captions cannot substitute for embedding the crops."
            )
    return {"observed": observed, "required": required,
            "figure_floor_required": figure_required,
            "figure_floor_source": floor_source,
            "paper_coverage_required": paper_required,
            "contributing_papers": len(contributing_papers),
            "croppable_figures": len(croppable_figures),
            "croppable_papers": len(croppable), "cited_papers": len(cited),
            "rights_ineligible_papers": len(supply["rights_ineligible"]),
            "figure_reuse_policy": supply["figure_reuse_policy"],
            "supply_measurable": supply["measurable"],
            "min_images": required + other_required_images}


def _check_claim_anchors(contract: dict, root: pathlib.Path, pdf_norm: str,
                         failures: list[str], notes: list[str]) -> None:
    if not contract.get("per_claim", {}).get("require_verbatim_anchor", True):
        return
    gq = _load_json(root / "deliverables" / "grounded_quotes.json")
    if not isinstance(gq, dict) or not gq:
        failures.append(
            "grounded_quotes.json missing or empty — cannot verify that the "
            "report embeds the verbatim anchors (run grounded_quotes.py --strict)"
        )
        return
    from support_policy import UNGROUNDED_STATES as ungrounded
    missing: list[str] = []
    for cid, entry in sorted(gq.items()):
        if entry.get("support_state") in ungrounded:
            continue
        anchors = (entry.get("supporting_anchors") or []) + \
                  (entry.get("contradicting_anchors") or [])
        if not anchors:
            continue
        if not any(_norm(a.get("quote", "")) in pdf_norm for a in anchors):
            missing.append(cid)
    if missing:
        failures.append(
            f"{len(missing)} grounded claim(s) have no verbatim anchor in the "
            f"report text: {', '.join(missing[:10])}"
            f"{'...' if len(missing) > 10 else ''}. The report paraphrased "
            "instead of quoting; rebuild Results from grounded_quotes.json."
        )
    else:
        notes.append(f"verbatim anchors: all {len(gq)} claim(s) represented")


def _check_locators(contract: dict, root: pathlib.Path,
                    failures: list[str], notes: list[str]) -> None:
    spec = contract.get("locators", {})
    forbidden = {str(v).strip().lower() for v in spec.get("forbid_section_values", [])}
    max_chars = int(spec.get("max_section_chars", 0) or 0)
    evidence = _read_jsonl(root / "evidence" / "evidence.jsonl")
    if not evidence:
        return
    bad_value: list[str] = []
    too_long: list[str] = []
    implausible: list[str] = []
    for row in evidence:
        section = str(row.get("section") or "").strip()
        bid = str(row.get("block_id") or "?")
        if section.lower() in forbidden:
            bad_value.append(bid)
        elif max_chars and len(section) > max_chars:
            too_long.append(bid)
        elif _section_cannot_reach(section, row.get("page")):
            implausible.append(f"{bid} ({section}, p. {row.get('page')})")
    if implausible:
        failures.append(
            f"{len(implausible)} locator(s) name a section that cannot reach "
            f"that page: {', '.join(implausible[:6])}"
            f"{'...' if len(implausible) > 6 else ''}. An abstract is not on "
            "page 18 and a competing-interests declaration does not own body "
            "text — a heading detected in page furniture has been inherited by "
            "every block after it. scripts/section_labels.section_for_page "
            "expires these labels at parse time; a run still showing them was "
            "parsed by an older version, so clear the parse cache "
            "(_PARSER_VERSION) and re-parse."
        )
    if bad_value:
        failures.append(
            f"{len(bad_value)} evidence row(s) carry an unusable section label "
            f"(e.g. 'Unknown'): {', '.join(bad_value[:6])}"
            f"{'...' if len(bad_value) > 6 else ''}. Resolve the real section "
            "(Abstract/Results/Discussion) at parse time."
        )
    if too_long:
        failures.append(
            f"{len(too_long)} evidence row(s) have a section label over "
            f"{max_chars} chars — a whole heading leaked into the locator: "
            f"{', '.join(too_long[:6])}{'...' if len(too_long) > 6 else ''}"
        )
    if not bad_value and not too_long:
        notes.append(f"locators: {len(evidence)} row(s) clean")


def _section_cannot_reach(section: str, page: Any) -> bool:
    """True when a short section's label has clearly travelled too far.

    The parser now expires these labels as it walks (it knows which page the
    heading was on). This gate sees only the final label and page, so it applies
    the same spans against the most generous assumption — that the heading sat on
    page 1. That is enough to catch every case the shipped reports carried, and
    it cannot fire on a section legitimately running long, because only sections
    with a declared span are checked at all.
    """
    from section_labels import MAX_PAGE_SPAN

    span = MAX_PAGE_SPAN.get(str(section or "").strip().lower())
    if span is None:
        return False
    try:
        page_number = int(page)
    except (TypeError, ValueError):
        return False
    # Pages in evidence rows are 1-based; a span of 0 means "page 1 only".
    return page_number > span + 1


def _link_targets(pdf_path: pathlib.Path | None) -> set[str] | None:
    """Every URI a link annotation in the PDF points at, or None if unreadable.

    None means "could not check", which is reported as such rather than as a
    pass: a gate that treats an unreadable PDF as clean is worse than no gate.
    """
    if not pdf_path or not pdf_path.exists():
        return None
    try:
        from pypdf import PdfReader
    except Exception:  # noqa: BLE001 - optional dependency at gate time
        return None
    try:
        reader = PdfReader(str(pdf_path), strict=True)
    except Exception:  # noqa: BLE001 - a corrupt PDF is a different failure
        return None
    targets: set[str] = set()
    for page in reader.pages:
        for annot in (page.get("/Annots") or []):
            try:
                action = annot.get_object().get("/A") or {}
                uri = action.get("/URI") if hasattr(action, "get") else None
            except Exception:  # noqa: BLE001 - skip a malformed annotation
                continue
            if uri:
                targets.add(str(uri))
    return targets


def _cited_references(root: pathlib.Path, contract: dict,
                      refs: list[dict]) -> list[dict]:
    """The subset of the corpus the report actually cites and numbers.

    Falls back to the full pool when the model cannot be built, so a broken
    model shows up as its own failure rather than as silently skipped checks.
    """
    try:
        from report_model import build_model
        cited = {r.get("paper_id") for r in build_model(root, contract)["references"]}
    except Exception:  # noqa: BLE001 - the model has its own gate
        return refs
    if not cited:
        return refs
    return [r for r in refs if r.get("paper_id") in cited]


def _check_reference_hyperlinks(spec: dict, refs: list[dict],
                                pdf_path: pathlib.Path | None,
                                failures: list[str], notes: list[str]) -> None:
    """Every cited reference must be reachable by clicking, not just readable.

    ``references.require_hyperlinks`` sat in the contract with NOTHING reading
    it — the same tautology this file exists to prevent, one level up: a declared
    requirement that no gate enforces is indistinguishable from no requirement.
    Checked against the PDF's real link annotations, so a renderer that emits
    citation text without an anchor is caught.
    """
    if not spec.get("require_hyperlinks", True):
        return
    targets = _link_targets(pdf_path)
    if targets is None:
        notes.append("reference hyperlinks: not checked (PDF unreadable or "
                     "pypdf unavailable)")
        return
    unlinked = []
    for ref in refs:
        doi = str(ref.get("doi") or "").strip().lower()
        url = str(ref.get("url") or "").strip().lower()
        if not doi and not url:
            continue
        if any(doi and doi in t.lower() or url and url == t.lower()
               for t in targets):
            continue
        unlinked.append(str(ref.get("paper_id") or "?"))
    if unlinked:
        failures.append(
            f"{len(unlinked)} reference(s) appear in the report with no clickable "
            f"link to the source: {', '.join(unlinked[:6])}"
            f"{'...' if len(unlinked) > 6 else ''}. Every citation — the "
            "reference list, each quote's attribution line, each Table 1 source "
            "and each figure caption — must render through build_pdf._link."
        )
    else:
        notes.append(f"reference hyperlinks: {len(targets)} link target(s), "
                     "every cited reference reachable")


def _check_references(contract: dict, root: pathlib.Path, pdf_text: str,
                      failures: list[str], notes: list[str],
                      pdf_path: pathlib.Path | None = None) -> None:
    spec = contract.get("references", {})
    refs = _read_jsonl(root / "corpus" / "references.jsonl")
    if not refs:
        return
    # Only the references the report PRINTS can carry a link in it. corpus/
    # references.jsonl is the whole discovered pool, most of which is never
    # cited, so checking it flagged 66 "unlinked references" for a report whose
    # 27 real citations were all linked correctly — a failure about documents
    # that are not in the document.
    _check_reference_hyperlinks(spec, _cited_references(root, contract, refs),
                                pdf_path, failures, notes)
    artifacts = spec.get("forbid_title_artifacts", [])
    dirty = [r.get("paper_id") for r in refs
             if any(a.lower() in str(r.get("title", "")).lower() for a in artifacts)]
    if dirty:
        failures.append(
            f"{len(dirty)} reference title(s) contain scraped page-chrome "
            f"(e.g. ' - PMC', '| Springer Nature Link'): "
            f"{', '.join(str(d) for d in dirty[:6])}"
            f"{'...' if len(dirty) > 6 else ''}. Clean titles in "
            "references_to_corpus.py."
        )
    if spec.get("forbid_uncited", True):
        evidence = _read_jsonl(root / "evidence" / "evidence.jsonl")
        cited = {str(r.get("paper_id")) for r in evidence
                 if r.get("stance") in {"supports", "contradicts"}}
        pdf_norm = _norm(pdf_text)
        listed_uncited = []
        for r in refs:
            pid = str(r.get("paper_id") or "")
            doi = str(r.get("doi") or "").lower()
            if pid in cited or not doi:
                continue
            if doi in pdf_norm:
                listed_uncited.append(pid)
        if listed_uncited:
            failures.append(
                f"{len(listed_uncited)} reference(s) are listed in the report but "
                f"ground no claim: {', '.join(listed_uncited[:6])}"
                f"{'...' if len(listed_uncited) > 6 else ''}. Drop them or cite them."
            )
    if not dirty:
        notes.append(f"references: {len(refs)} record(s), titles clean")


_TRAILING_FRAGMENT = re.compile(r"[a-z,;:]\s*$")
_LABEL_BLEED = re.compile(r"^\s*\d+[A-Z]")
_MERGED = re.compile(r"[a-z]\.[A-Z]")


def _check_quotes(contract: dict, root: pathlib.Path,
                  failures: list[str], notes: list[str]) -> None:
    spec = contract.get("quotes", {})
    evidence = _read_jsonl(root / "evidence" / "evidence.jsonl")
    frag, bleed, merged = [], [], []
    for row in evidence:
        quote = str(row.get("quote") or "")
        bid = str(row.get("block_id") or "?")
        is_sentence = str(row.get("block_type") or "sentence") == "sentence"
        if spec.get("forbid_trailing_fragment", True) and is_sentence \
                and _TRAILING_FRAGMENT.search(quote):
            frag.append(bid)
        if spec.get("forbid_leading_figure_label_bleed", True) and _LABEL_BLEED.match(quote):
            bleed.append(bid)
        if spec.get("forbid_merged_words", True) and _MERGED.search(quote):
            merged.append(bid)
    for label, bad, hint in (
        ("end mid-sentence", frag, "quote whole sentences"),
        ("start with figure-label bleed (e.g. '3Latozinemab')", bleed,
         "strip the leading figure number"),
        ("contain merged words (e.g. 'participants.CSF')", merged,
         "choose a clean anchor for that figure"),
    ):
        if bad:
            failures.append(
                f"{len(bad)} quote(s) {label}: {', '.join(bad[:5])}"
                f"{'...' if len(bad) > 5 else ''} — {hint}."
            )
    if not (frag or bleed or merged):
        notes.append(f"quotes: {len(evidence)} row(s) clean")


# --- driver ----------------------------------------------------------------

def verify(root: pathlib.Path, pdf_path: pathlib.Path, contract: dict,
           mode: str | None) -> tuple[list[str], list[str], dict]:
    failures: list[str] = []
    notes: list[str] = []

    if not pdf_path.exists():
        return [f"PDF not found: {pdf_path}"], notes, {}

    if mode is None:
        from report_model import resolve_review_mode
        mode = resolve_review_mode(root)

    pages = _pdf_pages(pdf_path)
    pdf_text = "\n".join(t for t, _ in pages)
    total_images = sum(n for _, n in pages)
    pdf_norm = _norm(pdf_text)
    pdf_lines = [_norm(line) for line in pdf_text.splitlines() if line.strip()]

    _check_sections(contract, pdf_lines, failures, notes)
    # The visual abstract and the synthesis panel are IMAGES; their captions
    # only prove anything on a page that embeds one. The synthesis table is a
    # drawn table, so its marker is a plain text check.
    _check_marker(contract.get("visual_abstract", {}), mode, pages,
                  "visual abstract", failures, notes, require_image_on_page=True)
    _check_marker(contract.get("synthesis_table", {}), mode, pages,
                  "evidence-axis synthesis table", failures, notes)
    _check_marker(contract.get("synthesis_chart", {}), mode, pages,
                  "synthesis panel", failures, notes, require_image_on_page=True)
    # Every other required visual asset is one more image the PDF must carry on
    # top of the paper figures.
    other_required_images = sum(
        1 for key in ("visual_abstract", "synthesis_chart")
        if mode in (contract.get(key, {}) or {}).get("required_modes", [])
    )
    fig_stats = _check_paper_figures(contract, mode, root, pdf_norm,
                                     total_images, failures, notes,
                                     other_required_images)
    _check_claim_anchors(contract, root, pdf_norm, failures, notes)
    _check_locators(contract, root, failures, notes)
    _check_references(contract, root, pdf_text, failures, notes, pdf_path)
    _check_quotes(contract, root, failures, notes)

    stats = {"mode": mode, "total_embedded_images": total_images, **fig_stats}
    return failures, notes, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Validate a report against templates/report_contract.json")
    ap.add_argument("--root", default=".")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--contract", default=str(DEFAULT_CONTRACT))
    ap.add_argument("--mode", default=None,
                    help="quick|deep|broad (default: read from run_manifest.json)")
    args = ap.parse_args(argv)

    contract = _load_json(pathlib.Path(args.contract))
    if not isinstance(contract, dict):
        print(f"FAIL: could not read contract: {args.contract}")
        return 1

    failures, notes, stats = verify(
        pathlib.Path(args.root).resolve(),
        pathlib.Path(args.pdf).resolve(),
        contract,
        args.mode,
    )
    for note in notes:
        print(f"NOTE: {note}")
    if stats:
        print(f"CONTRACT: mode={stats.get('mode')} "
              f"images={stats.get('total_embedded_images')}/"
              f"{stats.get('min_images')} "
              f"paper_figures={stats.get('observed')}/{stats.get('required')} "
              f"supply_measurable="
              f"{'yes' if stats.get('supply_measurable') else 'NO'}")
    for f in failures:
        print(f"FAIL: {f}")
    print(f"VERIFY-REPORT-CONTRACT: failures={len(failures)} "
          f"result={'pass' if not failures else 'fail'}")
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
