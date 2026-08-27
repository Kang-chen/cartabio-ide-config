#!/usr/bin/env python3
"""Render the synthesis panel: grounded claims by evidence axis and support tier.

This is a review-drawn chart, never a paper figure, and both renderers label it
as such. Its counts come from ``report_model.build_model()["panel_counts"]``,
which is derived from the same claim rows the Results section renders — so the
panel cannot disagree with the claim list.

That disagreement is not hypothetical: a shipped report's panel showed the
genetics axis as 2 single-direct + 3 indirect when the claims underneath it were
actually 1 single-direct + 4 indirect. The panel had been drawn from a
hand-maintained CSV instead of from the claims, and no gate compared them.
"""
from __future__ import annotations

import pathlib
from typing import Any

from report_model import (
    GROUNDED_STATES, SUPPORT_COLOR, SUPPORT_LABEL, SUPPORT_ORDER,
)

# Tiers shown in the panel, strongest first so the stack reads top-down.
# These are exactly ``report_model.GROUNDED_STATES`` — the panel counts GROUNDED
# claims, so C_INSUFFICIENT is excluded — in a display order chosen here. The
# guard below keeps the two from drifting apart: when they did, the caption
# (which summed every tier) advertised three grounded claims over a chart that
# drew one bar.
_PANEL_TIERS = [
    "C2_CONVERGENT",
    "C1_SINGLE_DIRECT",
    "C1_INDIRECT",
    "C_CONFLICTED",
    "C_REFUTED",
]
_LEGEND_BOTTOM = 0.02
_PLOT_BOTTOM = 0.25
if set(_PANEL_TIERS) != GROUNDED_STATES:
    raise RuntimeError(
        "synthesis_panel._PANEL_TIERS has drifted from report_model."
        f"GROUNDED_STATES: {sorted(set(_PANEL_TIERS) ^ GROUNDED_STATES)}"
    )


def _wrap(label: str, width: int = 14) -> str:
    words, lines, cur = str(label).split(), [], ""
    for w in words:
        trial = f"{cur} {w}".strip()
        if len(trial) <= width:
            cur = trial
        else:
            if cur:
                lines.append(cur)
            cur = w
    if cur:
        lines.append(cur)
    return "\n".join(lines) or str(label)


def render_panel(model: dict[str, Any], out_path: pathlib.Path,
                 dpi: int = 200) -> pathlib.Path | None:
    """Write the synthesis panel PNG. Returns the path, or None if unrenderable.

    Returns None rather than raising when matplotlib is unavailable or no claim
    carries a support tier: a missing supplementary chart must not abort a build.
    The contract gate is what decides whether its absence is acceptable.
    """
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        from matplotlib import font_manager
        from matplotlib.patches import Patch
    except Exception:
        return None

    axes = model.get("axes") or []
    counts = model.get("panel_counts") or {}
    if not axes or not any(sum(counts.get(a, {}).values()) for a in axes):
        return None

    present = [t for t in _PANEL_TIERS
               if any(counts.get(a, {}).get(t, 0) for a in axes)]
    if not present:
        return None

    # Stock, non-brand Unicode fonts shipped with Matplotlib. DejaVu covers the
    # scientific glyphs (Greek, operators) that appear in axis labels.
    body_font = font_manager.FontProperties(family="DejaVu Sans")
    display_font = font_manager.FontProperties(family="DejaVu Serif")
    legend_font = font_manager.FontProperties(family="DejaVu Sans", size=8)
    matplotlib.rcParams["font.family"] = ["DejaVu Sans"]

    # HORIZONTAL bars, and the quantity is independent primary studies rather
    # than claim counts. Two changes, both forced by the old chart:
    #
    #   * Vertical bars put one axis label under each bar, and the labels are
    #     phrases. Two of them collided into "mechanism_biologybiomarker_engage-
    #     ment" in a shipped report. Horizontally there is a full line per label.
    #   * Claim counts had to be disclaimed by their own caption ("read the
    #     tiers, not the totals"), because how many claims an axis carries is a
    #     function of how finely the reviewer split them. How many independent
    #     studies it rests on is not.
    labels = model.get("axis_labels") or {}
    studies = model.get("panel_studies") or {}
    strongest_tier = {
        axis: next((t for t in _PANEL_TIERS if counts.get(axis, {}).get(t, 0)),
                   None)
        for axis in axes
    }
    order = list(reversed(axes))  # first axis at the top
    values = [studies.get(a, 0) for a in order]
    claim_totals = [sum(counts.get(a, {}).get(t, 0) for t in _PANEL_TIERS)
                    for a in order]

    fig, ax = plt.subplots(figsize=(7.6, max(2.4, 0.62 * len(order) + 1.5)))
    ax.barh(range(len(order)), values, height=0.6,
            color=[SUPPORT_COLOR.get(strongest_tier[a] or "", "#999999")
                   for a in order],
            edgecolor="black", linewidth=0.6)
    for i, (value, claims) in enumerate(zip(values, claim_totals)):
        claim_note = f"{claims} claim" + ("" if claims == 1 else "s")
        # An axis with no retrieved primary study draws no bar at all, and a
        # blank row beside a populated chart reads as a rendering failure rather
        # than a finding — in a shipped report the review's own foundational
        # genetics axis was one of them. Say what the empty row means.
        if value == 0:
            note = f"no primary study retrieved · {claim_note}"
            colour = "#8A6D3B"
        else:
            note = (f"{value} stud{'y' if value == 1 else 'ies'} · {claim_note}")
            colour = "#333333"
        ax.text(value + max(values or [1]) * 0.02, i, note, va="center",
                fontsize=8.5, color=colour, fontproperties=body_font)

    ax.set_yticks(range(len(order)))
    ax.set_yticklabels([_wrap(labels.get(a, a), width=26) for a in order],
                       fontsize=9, fontproperties=body_font)
    ax.set_xlabel(
        "Independent primary studies supporting the axis",
        fontsize=10,
        fontproperties=body_font,
    )
    ax.set_title("Evidence breadth and strongest support tier, by axis",
                 fontsize=11, fontproperties=display_font)
    ax.set_xlim(0, max(values or [1]) * 1.42)
    ax.set_xticks(range(0, max(values or [1]) + 1))
    ax.spines[["top", "right"]].set_visible(False)
    # Legend BELOW the axes, not inside them. Horizontal bars leave no reliable
    # empty corner — with few studies per axis every bar is short and the box
    # lands on top of one.
    fig.legend(
        handles=[Patch(facecolor=SUPPORT_COLOR.get(t, "#999"), edgecolor="black",
                       label=SUPPORT_LABEL.get(t, t)) for t in present],
        framealpha=1.0, ncol=min(3, len(present)),
        loc="lower center", bbox_to_anchor=(0.5, _LEGEND_BOTTOM),
        title="Bar colour: strongest tier reached on that axis",
        prop=legend_font, title_fontproperties=legend_font,
    )
    # Reserve a fixed figure-level band for the legend. Anchoring it to the
    # axes made the box overlap the x-axis label for short (three-axis) reviews,
    # and the exact collision varied with the selected brand font.
    fig.tight_layout(rect=(0, _PLOT_BOTTOM, 1, 1))
    out_path.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(out_path, dpi=dpi, bbox_inches="tight")
    plt.close(fig)
    return out_path


def panel_total(model: dict[str, Any]) -> int:
    """Claims the panel actually DRAWS — i.e. those in a grounded tier.

    Summing every key of ``panel_counts`` counted C_INSUFFICIENT claims too,
    which the panel deliberately never draws, so the caption could claim "3
    grounded claims" above a chart showing one bar of one.
    """
    return sum(
        int(counts.get(tier, 0) or 0)
        for counts in (model.get("panel_counts") or {}).values()
        for tier in _PANEL_TIERS
    )


def panel_caption(model: dict[str, Any]) -> str:
    """The caption both renderers use. Contains the contract's marker text.

    It no longer has to argue with its own chart. The previous caption ended
    "read the tiers, not the totals, and do not treat this as a quantitative
    measure of evidence strength" — a disclaimer that was correct about claim
    counts and therefore an argument for plotting something else.
    """
    total = panel_total(model)
    n_studies = sum((model.get("panel_studies") or {}).values())
    return (
        "Synthesis panel (drawn by this review, not a paper figure). Bar length "
        f"is the number of independent primary studies supporting each axis "
        f"({n_studies} across the review); bar colour is the strongest support "
        f"tier any of that axis's claims reached. Claim counts ({total} grounded) "
        "are annotated beside each bar for reference — they depend on how finely "
        "the claims were split, which is why they are not the plotted quantity. "
        "All values are recomputed from the claim-evidence matrix at build time."
    )


def assert_panel_matches_claims(model: dict[str, Any]) -> list[str]:
    """Cross-check the panel against the claim list. Returns failure strings.

    Cheap insurance against the exact defect described in the module docstring.
    """
    failures: list[str] = []
    counts = model.get("panel_counts") or {}
    axes = list(model.get("axes") or [])

    # The axis SETS must match, not just the axes the model happens to list.
    # Iterating `axes` alone never looked at an axis present in panel_counts and
    # absent from axes, so a phantom axis carrying 99 claims passed clean.
    extra = sorted(set(counts) - set(axes))
    absent = sorted(set(axes) - set(counts))
    if extra:
        failures.append(
            f"synthesis panel has axes the claim list does not: {extra} "
            f"(claim-list axes: {axes})"
        )
    if absent:
        failures.append(
            f"claim list has axes the synthesis panel does not: {absent}"
        )

    for axis in axes:
        from_claims: dict[str, int] = {}
        for row in model.get("claims", []):
            if row["cluster"] == axis:
                from_claims[row["support_state"]] = \
                    from_claims.get(row["support_state"], 0) + 1
        panel = {k: v for k, v in counts.get(axis, {}).items() if v}
        if panel != {k: v for k, v in from_claims.items() if v}:
            failures.append(
                f"synthesis panel disagrees with the claim list on axis "
                f"{axis!r}: panel={panel} claims={from_claims}"
            )
    for axis in counts:
        for tier in counts.get(axis, {}):
            if tier not in SUPPORT_ORDER:
                failures.append(f"unknown support tier in panel: {tier!r}")
    return failures
