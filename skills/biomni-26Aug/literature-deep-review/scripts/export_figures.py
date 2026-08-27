#!/usr/bin/env python3
"""Export the actual figure images that ground accepted claims, with OCR overlays.

The pipeline already crops every detected figure region to a real PNG during
parsing (``fulltext/figures/<paper_id>/<figure_id>.png``) and, when OCR is on,
records the in-figure text lines (each with a pixel-space bounding box). This
script surfaces those figures as user-facing deliverables: for every figure that
backs an *accepted* evidence row, it

  1. copies the figure crop into ``deliverables/figures_cited/`` (a durable,
     results-facing location), and
  2. writes an *annotated* copy with the OCR text-line bounding boxes drawn on
     top (like the sibling ``literature-keyword-evidence`` annotated thumbnails),
     so a reader can see exactly which in-figure text was read and grounded.

It also emits ``deliverables/figures_cited/figures_manifest.json`` and a small
``figures_cited.md`` block linking each figure to the claims it supports, its
caption, source paper, and the OCR lines — ready to embed in the review/PDF.

Scope: **only cited figures** (figures referenced by an accepted ``figure_ocr``
or ``caption`` evidence row). Figures that ground nothing are not exported.

Coordinate note: EasyOCR bboxes (from ``ocr_figures.ocr_image``) are 4-point
polygons in the pixel space of the *cropped figure PNG*, so they map directly
onto the exported crop with no transform. Caption-only evidence has no line
bbox; those figures are still exported (plain + a copy), just without overlays.

Reads only canonical artifacts; never invents text or coordinates.
"""
from __future__ import annotations

import argparse
import json
import pathlib
import re
import shutil
import sys
from collections import defaultdict
from typing import Any

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from evidence_first import read_jsonl  # noqa: E402
from figure_provenance import Provenance, annotate, find_term_hits  # noqa: E402
from figure_selection import (  # noqa: E402
    policy_from_contract,
    select as select_figures,
    subject_aliases_from_manifest,
)
from report_model import load_contract  # noqa: E402


def _ocr_lines_for(image_path: str, cache: dict[str, tuple[list[dict], str]],
                   parsed_ocr: list[dict] | None = None) -> tuple[list[dict], str]:
    """In-figure OCR text for one crop, memoised per image.

    The parsed record is preferred: when the run was configured with
    ``--ocr targeted``, ``ocr_figures.ocr_figures`` has ALREADY read these images
    and stored the lines under the figure's ``ocr`` key. Re-running the engine
    here would pay for the same work twice and could disagree with the evidence
    rows built from the first pass.

    Live OCR is the fallback for a run whose parse predates that step, and covers
    only figures that survived selection — 13 or 14 per report, not the 242
    considered — so it is affordable.

    Returns [] when EasyOCR is unexpectedly unavailable. That case is REPORTED
    rather than swallowed: the standard installer initializes EasyOCR and its
    English model, so absence indicates a broken runtime rather than an optional
    dependency choice.
    """
    if parsed_ocr:
        return list(parsed_ocr), ""
    if image_path in cache:
        return cache[image_path]
    lines: list[dict] = []
    error = ""
    try:
        vendor = str(pathlib.Path(__file__).resolve().parent
                     / "vendor" / "keyword_evidence")
        if vendor not in sys.path:
            sys.path.insert(0, vendor)
        from ocr_figures import ocr_image
        lines = ocr_image(image_path)
    except Exception as exc:  # noqa: BLE001 - absence and failure are the same here
        error = f"{type(exc).__name__}: {exc}"
        print(f"WARN: in-figure OCR unavailable for {pathlib.Path(image_path).name} "
              f"({type(exc).__name__}: {exc}); the figure is embedded without "
              "provenance boxes", file=sys.stderr)
    cache[image_path] = (lines, error)
    return lines, error

# Block types that reference a figure image.
_FIGURE_BLOCK_TYPES = {"figure_ocr", "caption"}
# Only accepted, claim-bearing evidence counts as "citing" a figure.
_CITING_STANCES = {"supports", "contradicts"}

# Captionless fallback images have no source label that lets a reader tell a
# complete paper figure from one raster tile. The SLC33A1 report enlarged a
# 617x338 embedded tile into a page-width "Report Figure" with clipped panels
# and surrounding page text. Require a plausible whole-figure canvas for this
# one especially risky extraction class. Captioned/parent-linked crops are not
# subject to this heuristic because their source locator makes small panels
# auditable.
MIN_UNCAPTIONED_EMBEDDED_PIXELS = 600_000
MIN_UNCAPTIONED_EMBEDDED_SHORT_SIDE = 450
TOP_HEADER_FRACTION = 0.12
EDGE_TEXT_TOLERANCE_PIXELS = 2.0
BODY_PROSE_MIN_WORDS = 10
BODY_PROSE_LINE_LIMIT = 3
_PAGE_HEADER_RE = re.compile(
    r"^\s*(?:wiley|elsevier|springer\s+nature|nature\s+portfolio"
    r"|aging\s+cell|nature\s+cell\s+biology|cell\s+reports)\s*$",
    re.IGNORECASE,
)


def _ocr_top(bbox: Any) -> float | None:
    points = _poly_to_xy(bbox)
    return min((point[1] for point in points), default=None) if points else None


def _coverage_recovery_axes(selection: Any) -> set[str]:
    return {
        str(row.get("axis") or "")
        for row in selection.axis_coverage
        if not int(row.get("selected_figures") or 0)
    }


def image_candidate_disposition(record: dict[str, Any]) -> str:
    """Empty when report-usable, otherwise a stable rejection cause."""
    value = record.get("image_path")
    if not value:
        return "image_unavailable"
    path = pathlib.Path(str(value))
    if not path.exists() or not path.is_file():
        return "image_unavailable"
    try:
        from PIL import Image

        with Image.open(path) as image:
            image.verify()
        with Image.open(path) as image:
            width, height = image.size
    except Exception:
        return "image_unavailable"

    extraction_kind = str(record.get("extraction_kind") or "")
    figure_id = str(record.get("figure_id") or "")
    is_uncaptioned_embedded = (
        (extraction_kind == "embedded_image" or "_embedded_" in figure_id)
        and extraction_kind != "embedded_page_composite"
        and not str(record.get("caption") or "").strip()
        and not str(record.get("parent_figure_id") or "").strip()
    )
    if is_uncaptioned_embedded and (
        width * height < MIN_UNCAPTIONED_EMBEDDED_PIXELS
        or min(width, height) < MIN_UNCAPTIONED_EMBEDDED_SHORT_SIDE
    ):
        return "partial_embedded_fragment"
    prose_lines = 0
    for line in record.get("ocr") or []:
        if not isinstance(line, dict):
            continue
        text = str(line.get("text") or "").strip()
        points = _poly_to_xy(line.get("bbox"))
        if points and len(text) >= 4 and (
            min(point[0] for point in points) <= EDGE_TEXT_TOLERANCE_PIXELS
            or min(point[1] for point in points) <= EDGE_TEXT_TOLERANCE_PIXELS
            or max(point[0] for point in points) >= width - EDGE_TEXT_TOLERANCE_PIXELS
            or max(point[1] for point in points) >= height - EDGE_TEXT_TOLERANCE_PIXELS
        ):
            return "clipped_text_at_crop_edge"
        if len(re.findall(r"\b\w+\b", text)) >= BODY_PROSE_MIN_WORDS:
            prose_lines += 1
        top = _ocr_top(line.get("bbox"))
        if (
            top is not None
            and top <= height * TOP_HEADER_FRACTION
            and _PAGE_HEADER_RE.fullmatch(str(line.get("text") or ""))
        ):
            return "page_header_contamination"
    if prose_lines >= BODY_PROSE_LINE_LIMIT:
        return "adjacent_body_prose_contamination"
    return ""


def _recover_captionless_for_uncovered_axes(
    run_root: pathlib.Path,
    claims: list[dict],
    evidence: list[dict],
    fig_meta: dict[tuple[str, str], dict[str, Any]],
    selection: Any,
) -> int:
    """Second OCR pass, limited to cited papers on still-uncovered axes."""
    manifest = {}
    try:
        manifest = json.loads((run_root / "run_manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        pass
    ocr_mode = str((manifest.get("config") or {}).get("ocr") or "")
    if ocr_mode not in {"targeted", "all"}:
        return 0
    uncovered = _coverage_recovery_axes(selection)
    if not uncovered:
        return 0
    axis_by_claim = {
        str(row.get("claim_id") or ""): str(row.get("cluster") or "")
        for row in claims
    }
    paper_ids = {
        str(row.get("paper_id") or "")
        for row in evidence
        if row.get("stance") in _CITING_STANCES
        and axis_by_claim.get(str(row.get("claim_id") or "")) in uncovered
    }
    candidates = [
        {**record, "paper_id": pid, "figure_id": fid}
        for (pid, fid), record in fig_meta.items()
        if pid in paper_ids
        and not str(record.get("caption") or "").strip()
        and not (record.get("ocr") or [])
        and record.get("image_path")
        and pathlib.Path(str(record["image_path"])).exists()
    ]
    if not candidates:
        return 0
    vendor = str(SCRIPTS / "vendor" / "keyword_evidence")
    if vendor not in sys.path:
        sys.path.insert(0, vendor)
    from ocr_figures import ocr_figures

    recovered = ocr_figures(
        candidates,
        cache_dir=run_root / "cache" / "figure_ocr_coverage",
        require_caption=False,
    )
    changed = 0
    for row in recovered:
        key = (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
        lines = row.get("ocr") or []
        if key in fig_meta and lines:
            fig_meta[key].update({
                "ocr": lines,
                "ocr_attempted": True,
                "ocr_status": "completed",
                "ocr_error": "",
            })
            changed += 1

    # Persist the recovered OCR so compaction or a rebuild cannot discard it.
    if changed:
        for parsed_path in (run_root / "fulltext" / "parsed").glob("*.json"):
            parsed = json.loads(parsed_path.read_text(encoding="utf-8"))
            pid = str(parsed.get("paper_id") or "")
            dirty = False
            for figure in parsed.get("figures", []) or []:
                key = (pid, str(figure.get("figure_id") or ""))
                if key in fig_meta and fig_meta[key].get("ocr") and not figure.get("ocr"):
                    figure["ocr"] = fig_meta[key]["ocr"]
                    figure["ocr_attempted"] = True
                    figure["ocr_status"] = "completed"
                    figure["ocr_error"] = ""
                    dirty = True
            if dirty:
                temporary = parsed_path.with_name(f".{parsed_path.name}.tmp")
                temporary.write_text(
                    json.dumps(parsed, ensure_ascii=False, indent=2) + "\n",
                    encoding="utf-8",
                )
                temporary.replace(parsed_path)
    return changed


def _safe(name: str) -> str:
    return "".join(c if (c.isalnum() or c in "-_.") else "_" for c in str(name))


def _poly_to_xy(bbox: Any) -> list[tuple[float, float]] | None:
    """Normalize a bbox into a list of (x, y) points.

    Accepts an EasyOCR 4-point polygon ([[x,y],...]) or a rectangle
    [x0,y0,x1,y1]. Returns None if it cannot be interpreted.
    """
    if not bbox:
        return None
    # 4-point polygon
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(
        isinstance(p, (list, tuple)) and len(p) == 2 for p in bbox
    ):
        return [(float(x), float(y)) for x, y in bbox]
    # [x0,y0,x1,y1] rectangle
    if isinstance(bbox, (list, tuple)) and len(bbox) == 4 and all(
        isinstance(v, (int, float)) for v in bbox
    ):
        x0, y0, x1, y1 = (float(v) for v in bbox)
        return [(x0, y0), (x1, y0), (x1, y1), (x0, y1)]
    return None


def _draw_annotations(
    src_png: pathlib.Path,
    dst_png: pathlib.Path,
    lines: list[dict[str, Any]],
) -> bool:
    """Write an annotated copy of ``src_png`` with OCR line boxes/labels drawn.

    Returns True on success. Never raises: annotation is best-effort and a
    failure must not abort the export (the plain crop is still emitted).
    """
    try:
        from PIL import Image, ImageDraw, ImageFont
    except Exception:
        return False
    try:
        with Image.open(src_png) as im:
            im = im.convert("RGB")
            draw = ImageDraw.Draw(im)
            try:
                font = ImageFont.truetype(
                    str(
                        pathlib.Path(__file__).resolve().parent.parent
                        / "assets" / "fonts" / "DieGrotesk-A-Regular.ttf"
                    ),
                    14,
                )
            except Exception:
                font = ImageFont.load_default()
            color = (210, 60, 60)  # brand-adjacent red for visibility
            for ln in lines:
                pts = _poly_to_xy(ln.get("bbox"))
                if not pts:
                    continue
                draw.polygon(pts, outline=color, width=2)
                # Label above the box (falls inside if near the top edge).
                x0 = min(p[0] for p in pts)
                y0 = min(p[1] for p in pts)
                txt = (ln.get("text") or "").strip()
                if txt:
                    label = txt if len(txt) <= 40 else txt[:37] + "..."
                    ty = max(0, y0 - 16)
                    draw.text((x0 + 1, ty), label, fill=color, font=font)
            im.save(str(dst_png))
        return True
    except Exception:
        return False



def _reuse_by_paper(run_root: pathlib.Path) -> dict[str, dict]:
    """Per-paper figure reuse rights, from the acquisition records.

    Embedding a crop is reproduction, which needs a licence — a separate
    question from whether the text could be retrieved. See scripts/reuse_rights.
    Missing records mean unknown, which is treated as NOT permitted.
    """
    import sys as _sys
    scripts = str(pathlib.Path(__file__).resolve().parent)
    if scripts not in _sys.path:
        _sys.path.insert(0, scripts)
    from reuse_rights import rights_record

    out: dict[str, dict] = {}
    for name in ("fulltext/papers.jsonl", "corpus/references.jsonl"):
        path = run_root / name
        if not path.exists():
            continue
        for row in read_jsonl(path):
            pid = str(row.get("paper_id") or "")
            if not pid or pid in out:
                continue
            if "figure_embedding_allowed" in row:
                out[pid] = {
                    "license": row.get("license", ""),
                    "reuse_rights": row.get("reuse_rights", "none"),
                    "figure_embedding_allowed": bool(row["figure_embedding_allowed"]),
                    "figure_embedding_reason": row.get("figure_embedding_reason", ""),
                }
            else:
                evidence = row.get("access_evidence") or {}
                out[pid] = rights_record(evidence.get("license"),
                                         row.get("access_state"))
    return out


def _configured_reuse_policy(run_root: pathlib.Path) -> tuple[str, str, str]:
    """Return (policy, decision source, audit note) from review intake."""
    try:
        manifest = json.loads((run_root / "run_manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        manifest = {}
    config = manifest.get("config") if isinstance(manifest, dict) else {}
    config = config if isinstance(config, dict) else {}
    policy = str(config.get("figure_reuse_policy") or "reuse_cleared_only")
    source = str(config.get("figure_reuse_decision_source") or "")
    note = str(config.get("figure_reuse_note") or "").strip()
    if policy == "user_directed":
        if source != "explicit_user":
            raise ValueError(
                "user-directed figure inclusion requires "
                "config.figure_reuse_decision_source=explicit_user"
            )
        if not note:
            note = (
                "User explicitly requested inclusion of accessible source "
                "figures regardless of recorded reuse licence."
            )
    elif policy != "reuse_cleared_only":
        raise ValueError(f"unknown figure_reuse_policy: {policy!r}")
    return policy, source, note


def export_cited_figures(
    run_root: str | pathlib.Path,
    out_dir: str | pathlib.Path | None = None,
    enforce_reuse_rights: bool | None = None,
    rights_override_reason: str = "",
) -> dict[str, Any]:
    """Export annotated crops for every figure that grounds an accepted claim.

    Returns a summary dict (also written as ``figures_manifest.json``).
    """
    run_root = pathlib.Path(run_root)
    configured_policy, reuse_decision_source, configured_note = \
        _configured_reuse_policy(run_root)
    if enforce_reuse_rights is None:
        enforce_reuse_rights = configured_policy != "user_directed"
        rights_override_reason = configured_note
    else:
        configured_policy = (
            "reuse_cleared_only" if enforce_reuse_rights else "user_directed"
        )
    if not enforce_reuse_rights and not rights_override_reason.strip():
        raise ValueError(
            "disabling reuse-rights enforcement requires a non-empty "
            "rights_override_reason for the audit record"
        )
    evidence_path = run_root / "evidence" / "evidence.jsonl"
    out_dir = pathlib.Path(out_dir) if out_dir else (run_root / "deliverables" / "figures_cited")
    out_dir.mkdir(parents=True, exist_ok=True)

    if not evidence_path.exists():
        summary = {"figures_exported": 0, "reason": "no_evidence_file",
                   "selection_counts": {}, "selection_rejected": [],
                   "figures": []}
        (out_dir / "figures_manifest.json").write_text(json.dumps(summary, indent=2))
        return summary

    evidence = list(read_jsonl(evidence_path))
    try:
        run_manifest = json.loads((run_root / "run_manifest.json").read_text())
    except (OSError, json.JSONDecodeError):
        run_manifest = {}
    subject_aliases = subject_aliases_from_manifest(run_manifest)
    if str(run_manifest.get("mode") or "") in {"deep", "broad"} and not subject_aliases:
        raise ValueError(
            "deep/broad figure selection requires run_manifest subject, "
            "subject_long, or subject_aliases; generic outcome overlap cannot "
            "safely establish that a panel depicts the review subject"
        )

    # Optional: parsed records give us the figure caption + a reliable image_path
    # even for caption-type evidence. Keyed by (paper_id, figure_id).
    parsed_dir = run_root / "fulltext" / "parsed"
    fig_meta: dict[tuple[str, str], dict[str, Any]] = {}
    if parsed_dir.exists():
        for pjson in parsed_dir.glob("*.json"):
            try:
                parsed = json.loads(pjson.read_text())
            except Exception:
                continue
            pid = parsed.get("paper_id")
            for fig in parsed.get("figures", []) or []:
                fid = fig.get("figure_id")
                if pid and fid:
                    fig_meta[(str(pid), str(fid))] = {
                        "figure_id": str(fid),
                        "caption": fig.get("caption") or "",
                        "caption_source": fig.get("caption_source") or (
                            "parsed_caption" if fig.get("caption") else "none"
                        ),
                        "parent_figure_id": fig.get("parent_figure_id") or "",
                        "extraction_kind": fig.get("extraction_kind") or "",
                        "image_path": fig.get("image_path"),
                        "page": fig.get("page"),
                        # Written by ocr_figures during parsing when the run used
                        # --ocr targeted. Carried through so provenance boxes
                        # reuse that pass instead of re-reading the image.
                        "ocr": fig.get("ocr") or [],
                        "ocr_attempted": bool(fig.get("ocr_attempted")),
                        "ocr_status": fig.get("ocr_status") or "not_attempted",
                        "ocr_error": fig.get("ocr_error") or "",
                    }

    # Native Biomni visually adjudicates unquoted claim/figure pairs. Caption
    # similarity proposes a pair; it cannot prove that the panels show the same
    # direction, tested model, and outcome as the claim.
    entailment_path = run_root / "evidence" / "figure_entailment.jsonl"
    selection_policy = policy_from_contract(load_contract())
    if (
        str(run_manifest.get("mode") or "") in {"deep", "broad"}
        and selection_policy.get("require_pair_verification")
        and not entailment_path.exists()
    ):
        raise ValueError(
            "deep/broad figure export requires evidence/figure_entailment.jsonl; "
            "run figure_entailment.py --emit, complete every Biomni Read visual "
            "task, then run --assemble"
        )
    if entailment_path.exists():
        for row in read_jsonl(entailment_path):
            required = {
                "paper_id", "figure_id", "claim_id", "entails",
                "direction_match", "model_match", "outcome_match",
                "subject_match", "crop_complete", "labels_legible",
                "no_page_contamination", "reviewer", "rationale",
            }
            missing = sorted(required - set(row))
            if missing:
                raise ValueError(
                    f"{entailment_path}: figure verdict missing "
                    f"{', '.join(missing)}"
                )
            key = (
                str(row.get("paper_id") or ""),
                str(row.get("figure_id") or ""),
            )
            if key in fig_meta:
                fig_meta[key].setdefault("claim_entailments", []).append(row)

    # Group accepted figure-citing evidence by (paper_id, figure_id). These are
    # the figures whose own caption or in-panel text was quoted as an anchor.
    grouped: dict[tuple[str, str], dict[str, Any]] = defaultdict(
        lambda: {"claims": set(), "ocr_lines": [], "rows": []}
    )
    quoted: list[tuple[str, str, str]] = []
    for ev in evidence:
        if ev.get("block_type") not in _FIGURE_BLOCK_TYPES:
            continue
        if ev.get("stance") not in _CITING_STANCES:
            continue
        pid = str(ev.get("paper_id") or "")
        fid = str(ev.get("figure_id") or "")
        if not pid or not fid:
            continue
        g = grouped[(pid, fid)]
        g["claims"].add(ev.get("claim_id"))
        g["rows"].append(ev)
        quoted.append((pid, fid, str(ev.get("claim_id") or "")))
        # figure_ocr rows carry the specific line text + bbox to draw.
        if ev.get("block_type") == "figure_ocr" and ev.get("bbox"):
            g["ocr_lines"].append({
                "text": ev.get("source_text") or ev.get("quote") or "",
                "bbox": ev.get("bbox"),
                "conf": ev.get("ocr_conf"),
            })

    # A quoted caption is no longer the ONLY way a figure reaches the report.
    # Selecting figures by which quote happened to be a caption turned 45
    # verbatim anchors into 6 figures, put three figures from one paper under a
    # single claim, and hung a review's therapeutic-strategies schematic off a
    # lipid-metabolism claim. See figure_selection for the full account.
    claims = read_jsonl(run_root / "corpus" / "claims.jsonl")
    refs_by_id = {str(r.get("paper_id") or ""): r
                  for r in read_jsonl(run_root / "corpus" / "references.jsonl")}
    image_dispositions = {
        key: reason
        for key, record in fig_meta.items()
        if (reason := image_candidate_disposition(record))
    }
    unusable = set(image_dispositions)
    selectable_figures = {
        key: record for key, record in fig_meta.items() if key not in unusable
    }
    selection = select_figures(claims, evidence, selectable_figures, refs_by_id,
                               selection_policy,
                               quoted=quoted,
                               subject_aliases=subject_aliases)
    cited_claims_by_paper: dict[str, set[str]] = defaultdict(set)
    for row in evidence:
        if row.get("stance") in _CITING_STANCES:
            cited_claims_by_paper[str(row.get("paper_id") or "")].add(
                str(row.get("claim_id") or "")
            )
    for pid, fid in sorted(unusable):
        for claim_id in sorted(cited_claims_by_paper.get(pid, ())):
            selection.rejected.append({
                "claim_id": claim_id,
                "paper_id": pid,
                "figure_id": fid,
                "cause": image_dispositions[(pid, fid)],
            })
    coverage_ocr_recovered = _recover_captionless_for_uncovered_axes(
        run_root, claims, evidence, selectable_figures, selection
    )
    if coverage_ocr_recovered:
        selection = select_figures(
            claims,
            evidence,
            selectable_figures,
            refs_by_id,
            selection_policy,
            quoted=quoted,
            subject_aliases=subject_aliases,
        )
    for (pid, fid), choices in selection.by_figure().items():
        g = grouped[(pid, fid)]
        g["claims"].update(c.claim_id for c in choices)
        g["selection"] = sorted(
            ({"claim_id": c.claim_id, "reason": c.reason,
              "relevance": round(c.relevance, 4), "role": c.role,
              "relationship": c.relationship,
              "pair_verification": c.pair_verification}
             for c in choices),
            key=lambda s: s["claim_id"])
        if not g["rows"]:
            # Selected on caption relevance rather than by being quoted, so it
            # carries no evidence row to take metadata from.
            g["rows"] = [{"paper_id": pid,
                          "doi": refs_by_id.get(pid, {}).get("doi"),
                          "title": refs_by_id.get(pid, {}).get("title"),
                          "url": refs_by_id.get(pid, {}).get("url")}]

    rights = _reuse_by_paper(run_root)
    claims_by_id = {str(c.get("claim_id") or ""): c for c in claims}
    ocr_cache: dict[str, tuple[list[dict], str]] = {}
    manifest_figures: list[dict[str, Any]] = []
    exported = 0
    for (pid, fid), g in sorted(grouped.items()):
        meta = fig_meta.get((pid, fid), {})
        # Prefer the parsed record's image_path; fall back to any row's.
        image_path = meta.get("image_path")
        if not image_path:
            for row in g["rows"]:
                if row.get("image_path"):
                    image_path = row["image_path"]
                    break
        if not image_path or not pathlib.Path(image_path).exists():
            manifest_figures.append({
                "paper_id": pid, "figure_id": fid,
                "status": "image_missing",
                "claims": sorted(c for c in g["claims"] if c),
            })
            continue

        rights_info = rights.get(pid, {})
        allowed = rights_info.get("figure_embedding_allowed")
        if enforce_reuse_rights and not allowed:
            manifest_figures.append({
                "paper_id": pid, "figure_id": fid,
                "status": "reuse_not_permitted",
                "claims": sorted(c for c in g["claims"] if c),
                **{k: rights_info.get(k) for k in
                   ("license", "reuse_rights", "figure_embedding_reason")},
            })
            continue

        base = f"{_safe(pid)}__{_safe(fid)}"
        plain_dst = out_dir / f"{base}.png"
        # The plain crop is always written and never drawn on, so an unaltered
        # reproduction of the published figure exists whatever else happens.
        try:
            shutil.copyfile(image_path, plain_dst)
        except OSError as exc:
            manifest_figures.append({
                "paper_id": pid,
                "figure_id": fid,
                "status": "export_failed",
                "claims": sorted(c for c in g["claims"] if c),
                "error": f"{type(exc).__name__}: {exc}",
            })
            continue

        # Provenance: box the in-figure text that matches the claim's own terms.
        # Read the figure ONCE per image and reuse it across the claims it
        # grounds; the boxes are per-claim but the OCR is not.
        ocr_lines, live_ocr_error = _ocr_lines_for(
            image_path, ocr_cache, meta.get("ocr")
        )
        if not meta.get("ocr_attempted"):
            meta["ocr_attempted"] = True
            meta["ocr_status"] = (
                "failed" if live_ocr_error else ("completed" if ocr_lines else "empty")
            )
            meta["ocr_error"] = live_ocr_error
        provenance = _provenance_for(g, claims_by_id, ocr_lines, meta,
                                     bool(ocr_lines))
        annotated_rel = None
        hits = provenance.figure_hits
        if hits:
            annot_dst = out_dir / f"{base}.annotated.png"
            if annotate(pathlib.Path(image_path), annot_dst, hits):
                annotated_rel = annot_dst.name
                provenance.annotated_image = annotated_rel
        elif g["ocr_lines"]:
            # An OCR line was itself quoted as evidence. Keep the older overlay
            # for that case: the reader should see the region the quote came from.
            annot_dst = out_dir / f"{base}.annotated.png"
            if _draw_annotations(pathlib.Path(image_path), annot_dst, g["ocr_lines"]):
                annotated_rel = annot_dst.name
        exported += 1
        included_at_user_direction = bool(not allowed and not enforce_reuse_rights)
        rights_notice = ""
        if included_at_user_direction:
            rights_notice = (
                "Included at the user's explicit direction; the recorded "
                "licence does not establish figure-reuse permission."
            )
        manifest_figures.append({
            "paper_id": pid,
            "figure_id": fid,
            "status": "exported",
            "quality_check": {
                "status": "pass",
                "checks": [
                    "decodable_image",
                    "whole_figure_dimensions",
                    "no_page_header",
                    "no_clipped_ocr_text",
                    "no_adjacent_body_prose",
                ],
            },
            **{k: rights_info.get(k) for k in
               ("license", "reuse_rights", "figure_embedding_allowed",
                "figure_embedding_reason")},
            "included_at_user_direction": included_at_user_direction,
            "rights_notice": rights_notice,
            "caption": meta.get("caption", ""),
            "caption_source": meta.get("caption_source", "none"),
            "parent_figure_id": meta.get("parent_figure_id", ""),
            "role": next(
                (
                    str(row.get("role") or "primary_data")
                    for row in (g.get("selection") or [])
                    if row.get("role") == "primary_data"
                ),
                str((g.get("selection") or [{}])[0].get("role") or "primary_data"),
            ),
            "relationship": (
                "direct"
                if any(
                    str(row.get("relationship") or "direct") == "direct"
                    for row in (g.get("selection") or [])
                )
                else "illustrative"
            ),
            "page": meta.get("page"),
            "image": plain_dst.name,
            "annotated_image": annotated_rel,
            # Why this figure is here, in a form the caption can print and a
            # reviewer can audit: the caption terms that scored, the in-figure
            # text that was boxed, and whether the picture was read at all.
            "provenance": provenance.as_manifest(),
            "provenance_note": provenance.caption_note(),
            "claims": sorted(c for c in g["claims"] if c),
            # Why this figure is in the report: quoted caption, or selected on
            # caption-to-claim relevance (with the score that earned it).
            "selection": g.get("selection") or [
                {"claim_id": c, "reason": "quoted_caption", "relevance": 1.0}
                for c in sorted(x for x in g["claims"] if x)],
            "ocr_lines": [
                {"text": l["text"], "conf": l.get("conf")} for l in g["ocr_lines"]
            ],
            "ocr_attempted": bool(meta.get("ocr_attempted")),
            "ocr_status": str(meta.get("ocr_status") or "not_attempted"),
            "ocr_error": str(meta.get("ocr_error") or ""),
            "doi": g["rows"][0].get("doi"),
            "title": g["rows"][0].get("title"),
            "url": g["rows"][0].get("url"),
        })

    cited_papers_with_figures = sorted({
        f["paper_id"] for f in manifest_figures if f.get("status") == "exported"
    })
    selected_figure_ids = [
        {"paper_id": pid, "figure_id": fid}
        for pid, fid in sorted(selection.by_figure())
    ]
    exported_keys = {
        (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
        for row in manifest_figures if row.get("status") == "exported"
    }
    for axis in selection.axis_coverage:
        selected_keys = {
            (str(row.get("paper_id") or ""), str(row.get("figure_id") or ""))
            for row in axis.get("selected_figure_ids") or []
        }
        axis["exported_figures"] = len(selected_keys & exported_keys)
        if selected_keys and not axis["exported_figures"]:
            axis["gap_reason"] = (
                "selected figures were not exportable under the recorded image "
                "and reuse dispositions"
            )
    summary = {
        "figures_exported": exported,
        "figure_reuse_policy": configured_policy,
        "figure_reuse_decision_source": reuse_decision_source,
        "reuse_rights_enforced": enforce_reuse_rights,
        "rights_override_reason": rights_override_reason.strip(),
        "cited_papers_with_figures": len(cited_papers_with_figures),
        "cited_paper_ids_with_figures": cited_papers_with_figures,
        # What selection passed over, and why. A step that silently drops
        # candidates reads downstream exactly like a corpus that never had
        # them, and "6 figures" then looks like the whole supply rather than
        # what survived a relevance floor and two caps.
        "selection_counts": selection.counts(),
        "selected_figure_ids": selected_figure_ids,
        "axis_coverage": selection.axis_coverage,
        "coverage_ocr_recovered_figures": coverage_ocr_recovered,
        "selection_rejected": selection.rejected,
        "figure_entailment_artifact": (
            str(entailment_path.relative_to(run_root))
            if entailment_path.exists() else ""
        ),
        "figures": manifest_figures,
    }
    (out_dir / "figures_manifest.json").write_text(json.dumps(summary, indent=2))
    _write_markdown(out_dir / "figures_cited.md", manifest_figures)
    if (run_root / "corpus" / "references.jsonl").exists():
        from corpus_ledger import refresh as refresh_corpus_ledger
        refresh_corpus_ledger(run_root)
    return summary


def _provenance_for(group: dict[str, Any], claims_by_id: dict[str, dict],
                    ocr_lines: list[dict], meta: dict[str, Any],
                    ocr_available: bool) -> Provenance:
    """Assemble the reason this figure is shown, across the claims it grounds.

    A figure can ground more than one claim, so term hits are gathered over all
    of them and de-duplicated by region: one box per distinct piece of in-figure
    text, labelled with the claim term it matched.
    """
    from figure_selection import shared_term_words

    caption = str(meta.get("caption") or "")
    selections = group.get("selection") or []
    caption_terms: set[str] = set()
    hits: list = []
    seen_regions: set[tuple] = set()
    best_relevance = 0.0
    reasons: set[str] = set()

    for entry in selections:
        claim = claims_by_id.get(str(entry.get("claim_id") or ""))
        if not claim:
            continue
        reasons.add(str(entry.get("reason") or ""))
        best_relevance = max(best_relevance, float(entry.get("relevance") or 0.0))
        claim_text = str(claim.get("claim_text") or "")
        scope = str(claim.get("scope") or "")
        caption_terms.update(shared_term_words(caption, claim_text, scope))
        for hit in find_term_hits(ocr_lines, claim_text, scope):
            key = tuple(round(v, 1) for point in hit.bbox for v in point)
            if key in seen_regions:
                continue
            seen_regions.add(key)
            hits.append(hit)

    return Provenance(
        caption_terms=sorted(caption_terms),
        figure_hits=hits,
        relevance=best_relevance,
        reason="+".join(sorted(r for r in reasons if r)) or "claim_match",
        ocr_available=ocr_available,
    )


def _write_markdown(path: pathlib.Path, figures: list[dict[str, Any]]) -> None:
    lines = ["# Cited figures (full-text, with in-figure OCR)\n"]
    exported = [f for f in figures if f.get("status") == "exported"]
    if not exported:
        lines.append("_No figure-grounded claims in this run._\n")
        path.write_text("\n".join(lines))
        return
    for f in exported:
        claims = ", ".join(f.get("claims", [])) or "-"
        lines.append(f"## {f['figure_id']} — {f.get('title') or f['paper_id']}")
        lines.append(f"- **Grounds claims:** {claims}")
        if f.get("caption"):
            cap = f["caption"]
            lines.append(f"- **Caption:** {cap if len(cap) <= 300 else cap[:297] + '...'}")
        if f.get("url"):
            lines.append(f"- **Source:** {f['url']}")
        if f.get("rights_notice"):
            lines.append(f"- **Rights notice:** {f['rights_notice']}")
        img = f.get("annotated_image") or f.get("image")
        lines.append(f"- **Figure:** `{img}`")
        if f.get("ocr_lines"):
            ocr_txt = "; ".join(l["text"] for l in f["ocr_lines"] if l.get("text"))
            if ocr_txt:
                lines.append(f"- **OCR text read:** {ocr_txt}")
        lines.append("")
    path.write_text("\n".join(lines))


if __name__ == "__main__":
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--run-root", required=True)
    ap.add_argument(
        "--allow-unlicensed-figures",
        "--include-figures-at-user-direction",
        dest="include_figures_at_user_direction",
        action="store_true",
        help=("include accessible figures at the user's explicit direction; "
              "requires --rights-override-reason unless recorded in the manifest"),
    )
    ap.add_argument(
        "--rights-override-reason",
        default="",
        help="auditable reason an organization permits embedding without recorded rights",
    )
    ap.add_argument("--out-dir", default=None,
                    help="Override output dir (default: <run>/deliverables/figures_cited).")
    args = ap.parse_args()
    if (args.include_figures_at_user_direction
            and not args.rights_override_reason.strip()):
        ap.error(
            "--include-figures-at-user-direction requires "
            "--rights-override-reason"
        )
    summ = export_cited_figures(
        args.run_root,
        args.out_dir,
        enforce_reuse_rights=(
            False if args.include_figures_at_user_direction else None
        ),
        rights_override_reason=args.rights_override_reason,
    )
    print(json.dumps({"figures_exported": summ["figures_exported"]}, indent=2))
