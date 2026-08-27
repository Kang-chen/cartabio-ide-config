#!/usr/bin/env python3
"""PDF asset gate: fail unless the finished PDF actually EMBEDS the required visual
assets — the conceptual visual-abstract infographic AND the real paper-figure crops.

Why this gate exists
--------------------
The other gates check *text* (verbatim grounded quotes) but nothing verified that
the rendered PDF actually contains the *images* a figure-level review promises. A
report that silently shipped with **no infographic** or **zero/too-few real paper
figures** could still pass every other gate. This is exactly the regression this
gate is designed to catch: it opens the final PDF, counts embedded images, and
hard-fails unless

  (1) the visual-abstract infographic is embedded (an image on the summary page
      together with the infographic caption marker), and
  (2) at least the expected number of real paper-figure crops are embedded.

Where the requirement comes from
--------------------------------
It used to come from ``deliverables/figures_cited/figures_manifest.json`` — the
count of figures the run itself exported. That made the gate tautological: a run
that exported one figure only ever had to embed one figure, so a report that
silently dropped from five paper figures to one passed with zero failures. The
requirement now comes from two sources that a bad run cannot move:

  * ``templates/report_contract.json`` — ``paper_figures.min_by_mode[mode]`` is
    an absolute floor for the review mode (read from ``run_manifest.json``);
  * the **croppable supply** — cited papers (an accepted supports/contradicts
    evidence row) whose parsed record carries a figure with a real, existing
    ``image_path``. ``paper_figures.min_fraction_of_croppable`` of those must
    contribute an embedded figure.

    required = max(mode floor, ceil(fraction x croppable cited papers))

``--min-figures`` still overrides the requirement outright, and
``--allow-no-figures`` still waives it for genuinely text-only runs. When the
gate fails it names the cited papers that had a crop available but contributed
no embedded figure, so the operator knows what to fix.

Scope
-----
Intended for figure-level / grounded PDF reviews (``deep``/``broad`` with figures).
For an explicitly text-only or ``quick`` review that legitimately ships no paper
figures, either skip this gate or pass ``--allow-no-figures`` (the infographic
check still applies unless ``--no-require-infographic`` is also passed).

Usage
-----
    python verify_pdf_assets.py --root <run-root> --pdf <path-to-report.pdf>
    # options:
    #   --min-figures N          require >= N embedded paper figures (overrides
    #                            the contract-derived requirement)
    #   --contract PATH          report contract to read the floors from
    #                            (default: templates/report_contract.json)
    #   --mode quick|deep|broad  review mode (default: from run_manifest.json)
    #   --infographic-marker STR caption text proving the infographic is present
    #                            (default: "visual abstract")
    #   --no-require-infographic do not require the infographic (not recommended
    #                            for figure-level reviews)
    #   --allow-no-figures       do not require any paper figures (text-only runs)
"""
from __future__ import annotations

import argparse
import json
import math
import pathlib
import re
import sys

SCRIPTS = pathlib.Path(__file__).resolve().parent
sys.path.insert(0, str(SCRIPTS))

from verify_report_contract import (  # noqa: E402
    croppable_supply,
    requested_figure_floor,
    supply_failure,
)

DEFAULT_CONTRACT = SCRIPTS.parent / "templates" / "report_contract.json"


def _figure_caption_prefix() -> str:
    """What the builders call an embedded paper figure, per the contract.

    Delegates to ``report_model`` so this gate, both renderers and
    ``verify_report_contract`` read one value. A hardcoded copy here is how a
    gate that counts labels comes to count a label nobody emits.
    """
    from report_model import figure_caption_prefix, load_contract

    return figure_caption_prefix(load_contract(DEFAULT_CONTRACT))


_WS = re.compile(r"\s+")


def _infographic_markers(cli_marker: str) -> list[str]:
    """Every caption marker that proves the infographic is embedded.

    The contract owns the vocabulary: ``visual_abstract.caption_marker`` plus
    any ``accept_markers`` kept for older reports. The CLI value is added rather
    than replacing them, so an explicit --infographic-marker still works while
    the default no longer contradicts the contract.
    """
    markers = [_norm(cli_marker)] if cli_marker else []
    try:
        from report_model import load_contract
        block = (load_contract() or {}).get("visual_abstract") or {}
        for value in [block.get("caption_marker"), *(block.get("accept_markers") or [])]:
            normed = _norm(str(value or ""))
            if normed and normed not in markers:
                markers.append(normed)
    except Exception:  # noqa: BLE001 - the contract has its own gate
        pass
    return [m for m in markers if m]


def _norm(text: str) -> str:
    """Collapse whitespace / lowercase for robust text-marker matching."""
    if not text:
        return ""
    return _WS.sub(" ", text).strip().lower()


def _load_pdf(pdf_path: pathlib.Path):
    try:
        from pypdf import PdfReader
    except Exception as exc:  # pragma: no cover
        raise SystemExit(f"pypdf is required to verify the PDF: {exc}")
    return PdfReader(str(pdf_path), strict=True)


def count_embedded_images(reader) -> tuple[int, list[tuple[int, int]]]:
    """Return (total_image_count, [(page_number_1based, images_on_page), ...])."""
    total = 0
    per_page: list[tuple[int, int]] = []
    for i, page in enumerate(reader.pages):
        res = page.get("/Resources")
        if res is None:
            continue
        try:
            res = res.get_object()
        except Exception:
            pass
        xobj = res.get("/XObject") if hasattr(res, "get") else None
        if xobj is None:
            continue
        try:
            xobj = xobj.get_object()
        except Exception:
            pass
        n = 0
        items = xobj.items() if hasattr(xobj, "items") else []
        for _name, ref in items:
            try:
                obj = ref.get_object()
            except Exception:
                continue
            if getattr(obj, "get", lambda *_: None)("/Subtype") == "/Image":
                n += 1
        if n:
            per_page.append((i + 1, n))
            total += n
    return total, per_page


def _load_json(path: pathlib.Path):
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return None


def _read_jsonl(path: pathlib.Path) -> list[dict]:
    """Tolerant JSONL reader — skips blank/malformed lines, returns [] if absent."""
    rows: list[dict] = []
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return rows
    for line in text.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            row = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(row, dict):
            rows.append(row)
    return rows


# The SCIENTIFIC (fatal) subset of the visual-entailment verdict — whether the
# figure actually supports the claim — mirrors figure_selection._visual_entailment.
# A cosmetic (legibility) failure is NOT grounds to reject a figure here, so it
# is deliberately excluded from the negative-verdict test below.
_FATAL_ENTAILMENT_CHECKS = (
    "entails", "direction_match", "model_match", "outcome_match", "subject_match",
)


def figure_verdict_failures(root: pathlib.Path) -> list[str]:
    """Reconcile exported figure crops against their visual-entailment verdicts.

    This is the check that catches a figure attached to a claim it does not
    support reaching the PDF. Three invariants, each a hard failure:

      1. every exported figure/claim mapping carries a verdict;
      2. every verdict corresponds to an exported figure;
      3. no exported figure whose verdict is NEGATIVE is shipped, where
         "negative" means a failure of a scientific/fatal check.

    Reads ``deliverables/figures_cited/figures_manifest.json`` (the exported
    crops) against ``evidence/figure_entailment.jsonl`` (the verdicts). A no-op
    for runs that exported no paper figures (text-only / legacy runs).
    """
    manifest = _load_json(
        root / "deliverables" / "figures_cited" / "figures_manifest.json"
    )
    if not isinstance(manifest, dict):
        return []

    exported: dict[tuple[str, str], dict] = {}
    exported_mappings: set[tuple[str, str, str]] = set()
    for fig in manifest.get("figures") or []:
        if not isinstance(fig, dict) or fig.get("status") != "exported":
            continue
        pid = str(fig.get("paper_id") or "")
        fid = str(fig.get("figure_id") or "")
        exported[(pid, fid)] = fig
        for mapping in fig.get("selection") or []:
            cid = str((mapping or {}).get("claim_id") or "")
            if cid:
                exported_mappings.add((pid, fid, cid))
    if not exported:
        return []

    verdicts: dict[tuple[str, str, str], dict] = {}
    for row in _read_jsonl(root / "evidence" / "figure_entailment.jsonl"):
        verdicts[(
            str(row.get("paper_id") or ""),
            str(row.get("figure_id") or ""),
            str(row.get("claim_id") or ""),
        )] = row

    failures: list[str] = []

    # (1) every exported figure/claim mapping must carry a verdict.
    for pid, fid, cid in sorted(exported_mappings):
        if (pid, fid, cid) not in verdicts:
            failures.append(
                f"exported figure {pid}/{fid} is shown for claim {cid} but has "
                "no verdict in evidence/figure_entailment.jsonl"
            )
    # An exported figure with no claim mapping at all cannot be reconciled.
    mapped_pairs = {(pid, fid) for pid, fid, _ in exported_mappings}
    for pid, fid in sorted(exported):
        if (pid, fid) not in mapped_pairs:
            failures.append(
                f"exported figure {pid}/{fid} carries no claim mapping, so its "
                "visual verdict cannot be reconciled"
            )

    # (2) every verdict must correspond to an exported figure.
    for pid, fid, cid in sorted(verdicts):
        if (pid, fid) not in exported:
            failures.append(
                f"evidence/figure_entailment.jsonl has a verdict for {pid}/{fid} "
                f"(claim {cid}) that is not an exported figure"
            )

    # (3) no exported figure with a negative (scientific-failing) verdict ships.
    for pid, fid, cid in sorted(exported_mappings):
        verdict = verdicts.get((pid, fid, cid))
        if not verdict:
            continue  # already reported by (1)
        failed = [c for c in _FATAL_ENTAILMENT_CHECKS if verdict.get(c) is not True]
        if failed:
            failures.append(
                f"exported figure {pid}/{fid} appears in the PDF for claim {cid} "
                f"but its verdict fails scientific check(s): {', '.join(failed)}. "
                "A figure whose verdict does not support the claim must not ship."
            )
    return failures


def _expected_paper_figures(root: pathlib.Path) -> tuple[int | None, str]:
    """What the run itself *produced*: figures with status==exported in the
    manifest, falling back to review_stats.json figures_exported.

    Reported for context only. This is deliberately NOT the requirement — a
    requirement read out of the run's own output can never detect a shortfall.
    """
    man = root / "deliverables" / "figures_cited" / "figures_manifest.json"
    if man.exists():
        data = _load_json(man)
        if isinstance(data, dict):
            figs = data.get("figures", []) or []
            exported = [f for f in figs if isinstance(f, dict)
                        and f.get("status") == "exported"]
            return len(exported), f"figures_manifest.json (exported={len(exported)})"
    stats = root / "deliverables" / "review_stats.json"
    if not stats.exists():
        stats = root / "review_stats.json"
    if stats.exists():
        data = _load_json(stats)
        if isinstance(data, dict):
            fe = data.get("figures_exported")
            if isinstance(fe, int):
                return fe, f"review_stats.json (figures_exported={fe})"
    return None, "no manifest/stats found"


def _run_mode(root: pathlib.Path) -> str:
    """Review mode for this run — delegates to the one canonical resolver.

    Kept as a thin alias so existing callers and tests keep working. Two gates
    resolving the mode independently is how they came to disagree about the
    same run; there must be exactly one implementation.
    """
    from report_model import resolve_review_mode
    return resolve_review_mode(root)


def _papers_with_embedded_figures(root: pathlib.Path) -> set[str]:
    """Cited papers that actually contributed an exported figure crop."""
    data = _load_json(root / "deliverables" / "figures_cited" / "figures_manifest.json")
    figs = data.get("figures", []) or [] if isinstance(data, dict) else []
    return {str(f.get("paper_id")) for f in figs
            if isinstance(f, dict) and f.get("status") == "exported" and f.get("paper_id")}


def _required_paper_figures(root: pathlib.Path, contract: dict, mode: str
                            ) -> tuple[int, str, dict]:
    """Require figures only up to the affirmative reusable crop supply.

    Neither term is derived from what the run exported, which is precisely what
    made the old expectation tautological.
    """
    spec = contract.get("paper_figures", {}) if isinstance(contract, dict) else {}
    try:
        selected_floor, floor_source = requested_figure_floor(root, contract, mode)
        config_failure = None
    except (TypeError, ValueError) as exc:
        selected_floor, floor_source = 0, "invalid run configuration"
        config_failure = f"invalid figure minimum: {exc}"
    try:
        frac = float(spec.get("min_fraction_of_croppable", 0) or 0)
    except (TypeError, ValueError):
        frac = 0.0
    supply = croppable_supply(root)
    cited, croppable = supply["cited"], supply["croppable"]
    croppable_figures = supply["croppable_figures"]
    paper_required = math.ceil(frac * len(croppable)) if croppable else 0
    figure_required = selected_floor
    required = max(figure_required, paper_required)
    src = (
        f"figure floor={figure_required} from {floor_source} (legal crop supply="
        f"{len(croppable_figures)}), plus {frac:.0%} paper coverage="
        f"{paper_required}/{len(croppable)}; both must hold"
    )
    detail = {
        "mode": mode,
        "figure_floor_source": floor_source,
        "figure_floor_required": figure_required,
        "paper_coverage_required": paper_required,
        "min_fraction_of_croppable": frac,
        "cited_papers": sorted(cited),
        "rights_ineligible_papers": sorted(supply["rights_ineligible"]),
        "croppable_papers": sorted(croppable),
        "croppable_figures": len(croppable_figures),
        "supply_measurable": supply["measurable"],
        "supply_failure": supply_failure(supply),
        "config_failure": config_failure,
    }
    return required, src, detail


def _shortfall_detail(root: pathlib.Path, detail: dict) -> str:
    """Name the cited papers that had a crop available but shipped no figure."""
    croppable = set(detail.get("croppable_papers", []))
    if not croppable:
        return (" No cited paper had a crop available (no parsed record carries a "
                "figure with an existing image_path) — check that full texts are "
                "being parsed with figure extraction before blaming the report.")
    unused = sorted(croppable - _papers_with_embedded_figures(root))
    if not unused:
        return (f" All {len(croppable)} croppable cited paper(s) already contributed "
                "a figure, so the shortfall is against the mode floor: widen the "
                "corpus or ground more figure-shown claims.")
    shown = ", ".join(unused[:8]) + ("..." if len(unused) > 8 else "")
    return (f" {len(unused)} of {len(croppable)} cited paper(s) with a crop available "
            f"contributed NO embedded figure: {shown}.")


def verify(root: pathlib.Path, pdf_path: pathlib.Path, *,
           require_infographic: bool, infographic_marker: str,
           allow_no_figures: bool, min_figures: int | None,
           contract: dict | None = None, mode: str | None = None
           ) -> tuple[list[str], list[str], dict]:
    failures: list[str] = []
    notes: list[str] = []

    if contract is None:
        contract = _load_json(DEFAULT_CONTRACT)
    if mode is None:
        mode = _run_mode(root)

    if not pdf_path.exists():
        failures.append(f"PDF not found: {pdf_path}")
        return failures, notes, {}

    reader = _load_pdf(pdf_path)
    total_images, per_page = count_embedded_images(reader)
    pdf_text = _norm("\n".join((p.extract_text() or "") for p in reader.pages))

    # --- Infographic check -------------------------------------------------
    # Accept every marker the CONTRACT declares, not one hardcoded string. This
    # gate looked for "visual abstract" while the contract said caption_marker
    # "infographic" and the builder wrote "Infographic" — so a correct report
    # failed on a word the contract had already renamed. A gate that carries its
    # own private copy of the spec is a second spec.
    markers = _infographic_markers(infographic_marker)
    marker_present = any(m in pdf_text for m in markers)
    shown = " or ".join(repr(m) for m in markers)
    if require_infographic:
        if total_images == 0:
            failures.append(
                "infographic required but the PDF embeds NO images at all "
                "(the opening infographic is missing)"
            )
        elif not marker_present:
            failures.append(
                f"infographic required but no caption marker ({shown}) was "
                "found in the PDF text (it appears missing or unlabeled)"
            )
        else:
            notes.append(
                f"infographic present (marker {shown} found; "
                f"{total_images} total embedded images)"
            )

    # --- Paper-figure count ------------------------------------------------
    # The caption prefix comes from the CONTRACT, not from a literal here. It was
    # hardcoded as "Report Figure" in this gate and in both builders, so renaming
    # it to the shorter "Figure" that a reader expects would have silently taken
    # this count to zero and passed a report with no figures at all.
    #
    # Count DISTINCT figure numbers, not occurrences: a well-formed report names
    # each figure twice — once in its caption under the claim it grounds, and
    # again in the Figures list — so counting occurrences reports double the
    # real number and lets half the required figures go missing unnoticed.
    figure_prefix = _figure_caption_prefix()
    report_figure_captions = len(set(re.findall(
        rf"{re.escape(figure_prefix.lower())}\s*(\d+)", pdf_text)))
    expected, exp_src = _expected_paper_figures(root)
    required = 0
    req_src = "waived"
    detail: dict = {}

    # Images the PDF must carry ON TOP of the paper figures: the visual-abstract
    # infographic when it is required here, and the synthesis panel when the
    # contract requires it for this mode. Used as the image floor below.
    panel_required = mode in (
        (contract.get("synthesis_chart", {}) or {}).get("required_modes", [])
        if isinstance(contract, dict) else []
    )
    other_required_images = int(bool(require_infographic)) + int(panel_required)

    if allow_no_figures:
        notes.append("paper-figure requirement waived (--allow-no-figures)")
    else:
        # The requirement comes from the external contract plus the croppable
        # supply — never from the count this run happened to export.
        required, req_src, detail = _required_paper_figures(root, contract, mode)
        if detail.get("config_failure"):
            failures.append(detail["config_failure"])
        if detail.get("supply_failure"):
            failures.append(detail["supply_failure"])
        if min_figures is not None:
            required, req_src = min_figures, f"--min-figures={min_figures}"
        elif not (isinstance(contract, dict) and contract.get("paper_figures")):
            failures.append(
                "could not read paper_figures thresholds from the report contract "
                f"({DEFAULT_CONTRACT}); the figure requirement cannot be established. "
                "Restore the contract or pass --min-figures / --allow-no-figures."
            )

        # Each real paper figure is captioned exactly once ("Report Figure N")
        # by the builder, so the caption count is the reliable count of embedded
        # paper figures. (We do NOT use unique-image XObjects for this, because a
        # PDF library may dedup byte-identical images into one shared XObject;
        # real paper crops are distinct, but the caption count is robust either
        # way.) But a caption is TEXT: on its own it proves nothing, and the old
        # "total_images == 0" backstop never fired because the visual abstract is
        # itself an image. So the label count is paired with an image FLOOR —
        # every required paper figure plus every other required visual asset must
        # correspond to an embedded image.
        observed = report_figure_captions
        min_images = required + other_required_images
        notes.append(
            f"paper figures: observed={observed} "
            f"(report-figure captions={report_figure_captions}, "
            f"total embedded images={total_images}); required>={required} [{req_src}]"
            + (f"; images required>={min_images}" if required > 0 else "")
            + (f"; run exported {expected} [{exp_src}]" if expected is not None else "")
        )
        if required > 0 and observed < required:
            failures.append(
                f"too few real paper figures embedded: observed={observed} "
                f"< required {required} ({req_src})."
                + _shortfall_detail(root, detail) +
                " A figure-level review must embed its real paper-figure crops; "
                "Figure selection no longer requires a caption anchor (see scripts/figure_selection): a figure is chosen when its caption scores against the claim text. A shortfall now means the crops were not produced, the captions were not specific enough to any claim, or the selection caps are too tight — read selection_rejected in figures_manifest.json, which records the cause for every figure passed over. "
                "regenerate the PDF so every exported figure is included (or pass "
                "--allow-no-figures for an explicitly text-only run)."
            )
        contributing_papers = _papers_with_embedded_figures(root) & set(
            detail.get("croppable_papers", [])
        )
        paper_required = (
            int(detail.get("paper_coverage_required", 0) or 0)
            if min_figures is None else 0
        )
        if len(contributing_papers) < paper_required:
            unused = sorted(
                set(detail.get("croppable_papers", [])) - contributing_papers
            )
            failures.append(
                f"too few cited papers contribute real figures: observed="
                f"{len(contributing_papers)} < required {paper_required}. "
                "The paper-coverage floor is independent of the total figure "
                "count; multiple crops from one paper cannot satisfy it. "
                f"Available papers with no exported crop: {', '.join(unused[:8])}"
                f"{'...' if len(unused) > 8 else ''}."
            )
        if required > 0 and observed >= required and total_images < min_images:
            failures.append(
                f"figure LABELS without IMAGES: {observed} "
                f"{figure_prefix!r} "
                f"caption(s) but only {total_images} embedded image(s); "
                f">={min_images} required ({required} paper figure(s) + "
                f"{other_required_images} other required visual asset(s)). "
                "A report can print any number of figure captions without "
                "embedding a single crop; the images are the deliverable."
            )

    # --- Exported figure crops vs their visual verdicts --------------------
    # Reconcile what the PDF ships against the entailment ledger: every exported
    # crop must have a verdict, every verdict an exported crop, and no figure
    # whose verdict does not support its claim may appear.
    failures += figure_verdict_failures(root)

    stats = {
        "total_embedded_images": total_images,
        "images_per_page": per_page,
        "infographic_marker_present": marker_present,
        "report_figure_caption_count": report_figure_captions,
        "expected_paper_figures": expected,
        "expected_source": exp_src,
        "mode": mode,
        "required_paper_figures": required,
        "required_source": req_src,
        "other_required_images": other_required_images,
        "required_embedded_images": (required + other_required_images
                                     if required > 0 else 0),
        "cited_papers": len(detail.get("cited_papers", [])),
        "croppable_cited_papers": len(detail.get("croppable_papers", [])),
        "croppable_figures": detail.get("croppable_figures", 0),
        "paper_coverage_required": detail.get("paper_coverage_required", 0),
        "contributing_papers": len(
            _papers_with_embedded_figures(root)
            & set(detail.get("croppable_papers", []))
        ),
        "supply_measurable": detail.get("supply_measurable", True),
    }
    return failures, notes, stats


def main(argv=None) -> int:
    ap = argparse.ArgumentParser(
        description="Verify the PDF embeds the required visual assets "
                    "(infographic + real paper figures)")
    ap.add_argument("--root", default=".")
    ap.add_argument("--pdf", required=True)
    ap.add_argument("--min-figures", type=int, default=None,
                    help="require >= N embedded paper figures (overrides the "
                         "contract/croppable-supply requirement)")
    ap.add_argument("--contract", default=str(DEFAULT_CONTRACT),
                    help="report contract holding the paper_figures thresholds")
    ap.add_argument("--mode", default=None,
                    help="quick|deep|broad (default: read from run_manifest.json)")
    ap.add_argument("--infographic-marker", default="visual abstract",
                    help="caption text proving the infographic is embedded")
    ap.add_argument("--no-require-infographic", dest="require_infographic",
                    action="store_false",
                    help="do not require the infographic (not recommended)")
    ap.add_argument("--allow-no-figures", action="store_true",
                    help="do not require paper figures (text-only runs)")
    ap.set_defaults(require_infographic=True)
    args = ap.parse_args(argv)

    root = pathlib.Path(args.root).resolve()
    pdf_path = pathlib.Path(args.pdf).resolve()

    failures, notes, stats = verify(
        root, pdf_path,
        require_infographic=args.require_infographic,
        infographic_marker=args.infographic_marker,
        allow_no_figures=args.allow_no_figures,
        min_figures=args.min_figures,
        contract=_load_json(pathlib.Path(args.contract)),
        mode=args.mode.strip().lower() if args.mode else None,
    )
    for note in notes:
        print(f"NOTE: {note}")
    if stats:
        waived = stats["required_source"] == "waived"
        print(
            f"PDF-ASSETS: mode={stats['mode']} images={stats['total_embedded_images']} "
            f"infographic={'yes' if stats['infographic_marker_present'] else 'no'} "
            f"paper_figures={stats['report_figure_caption_count']}/"
            f"{'waived' if waived else stats['required_paper_figures']} "
            f"croppable_cited_papers="
            f"{'n/a' if waived else stats['croppable_cited_papers']} "
            f"exported_by_run={stats['expected_paper_figures']}"
        )
    for f in failures:
        print(f"FAIL: {f}")
    print(f"VERIFY-PDF-ASSETS: failures={len(failures)} result={'pass' if not failures else 'fail'}")
    return min(255, len(failures))


if __name__ == "__main__":
    sys.exit(main())
