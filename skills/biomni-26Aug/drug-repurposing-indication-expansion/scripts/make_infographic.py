"""Build the GenerateImage prompt for the report's opening infographic (disease-agnostic).

The infographic is a CONCEPTUAL / schematic figure (method overview: disease signature ->
connectivity reversal -> ranked approved drugs), so it MUST be produced with the
GenerateImage tool, NOT matplotlib (per the visualization guidelines). This module only
CONSTRUCTS the prompt string, grounded in the actual run numbers; the AGENT then calls
GenerateImage(prompt=..., ...) and saves the PNG into <outdir>/figures/infographic.png.

Design intent: a clean, editorial scientific infographic (muted gold accent, off-white
background, dark charcoal text, generous whitespace, flat vector look, no photorealism,
no 3D, minimal text baked into the image).

NEGATIVE CONSTRAINT: the prompt carries NO data-bearing content (no drug names, no gene
symbols, no ranks, no heatmaps). All factual content lives in the derived caption produced
by `infographic_caption_from_data`, which reads the approved-candidate DataFrame directly.
This prevents the image model from inventing a ranking or signature that contradicts the
report's own tables.

Public API:
  build_infographic_prompt(disease_label, stats, top_drugs) -> str
  infographic_caption_from_data(stats, approved_df, n=4, verdict=None, compound_flags=None) -> str
  INFOGRAPHIC_STYLE  (str) reusable style suffix
"""
import re

INFOGRAPHIC_STYLE = (
    "Editorial scientific infographic, flat vector illustration style, clean and minimal, "
    "muted gold as the single accent color, warm off-white background, "
    "dark charcoal elements, generous whitespace, thin connecting arrows, "
    "no photorealism, no 3D rendering, no clutter, no dense paragraphs of text, "
    "at most a few short labels, balanced horizontal left-to-right flow, professional and modern."
)

NEGATIVE_CONSTRAINT = (
    "Do NOT render any drug names, any gene symbols, any ranked or numbered list of compounds, "
    "any heatmap or colour-scale legend, and no numbers other than the three stat badges given above. "
    "Drug icons must be generic unlabelled capsules."
)


def build_infographic_prompt(disease_label, stats, top_drugs=None):
    """Construct a grounded GenerateImage prompt for the method-overview infographic.

    disease_label : str, e.g. "idiopathic pulmonary fibrosis"
    stats : dict with keys n_drugs, n_approved, n_appr_sig (ints)
    top_drugs : optional list of 3-4 top drug names. IGNORED — kept in the signature for
        call-site compatibility only. Drug names are never injected into the image prompt;
        all factual content lives in the derived caption (see infographic_caption_from_data).
    """
    n_drugs = stats.get("n_drugs", "N")
    n_appr = stats.get("n_approved", "N")
    n_sig = stats.get("n_appr_sig", "N")

    prompt = (
        f"A horizontal three-stage scientific method infographic explaining computational drug "
        f"repurposing for {disease_label}. "
        f"STAGE 1 (left): a 'disease gene-expression signature' shown as a pair of up-arrow / "
        f"down-arrow gene stacks, labelled 'disease signature'. "
        f"STAGE 2 (middle): a 'connectivity reversal' concept, shown as two opposing arrows or mirrored "
        f"waveforms flipping/cancelling, with a small balance/flip motif, labelled 'reverse the signature'. "
        f"STAGE 3 (right): a ranked shortlist of approved drugs shown as stacked capsule/pill icons with a "
        f"small ranking bar, labelled 'ranked approved drugs'. "
        f"Thin gold arrows connect the three stages left to right. "
        f"Include three small stat callouts as compact numbered badges: '{n_drugs} drugs screened', "
        f"'{n_appr} approved with signature', '{n_sig} significant reversers'. "
        f"Title area at top reads 'Connectivity-based drug repurposing'. "
        f"{NEGATIVE_CONSTRAINT} "
        + INFOGRAPHIC_STYLE
    )
    return prompt


def _norm(s):
    """Lowercase, drop parentheticals, collapse non-alphanumerics to single spaces."""
    if not isinstance(s, str):
        return ""
    s = s.lower().strip()
    s = re.sub(r"\s*\(.*?\)\s*", " ", s)
    return re.sub(r"[^a-z0-9]+", " ", s).strip()


def _flag_lookup(compound_flags):
    """{norm_name: (classification, note, flagged_bool)} from report_config['compound_flags']."""
    out = {}
    if not compound_flags:
        return out
    items = []
    if isinstance(compound_flags, dict):
        for k, v in compound_flags.items():
            if isinstance(v, dict):
                items.append((k, v.get("classification", "caution"), v.get("note", "")))
            else:
                items.append((k, str(v), ""))
    else:
        for it in compound_flags:
            if isinstance(it, dict):
                items.append((it.get("name", ""), it.get("classification", "caution"),
                              it.get("note", "")))
    for name, cls, note in items:
        nm = _norm(name)
        if not nm:
            continue
        cls = str(cls).strip().lower()
        out[nm] = (cls, note or "", cls not in ("credible", "ok", "clear", ""))
    return out


def _verdict_status(verdict):
    if verdict is None:
        return None
    if isinstance(verdict, dict):
        return verdict.get("status")
    return str(verdict)


def infographic_caption_from_data(stats, approved_df, n=4, verdict=None, compound_flags=None):
    """Build the factual infographic caption directly from the approved-candidate DataFrame.

    This is the derive-not-restate counterpart to the image prompt: the image carries no
    data, so the caption printed beneath it states real counts (and, when appropriate, the
    top-ranked compounds) read from the dataframe. This makes it structurally impossible for
    the deliverable to assert a ranking that disagrees with all_drugs_ranked.csv.

    Verdict- and flag-aware (defect-2 fix): page 1 must not contradict the report's own
    conclusion.
      * When the controls verdict is 'fail'/'weak', the caption LEADS with the failed verdict
        and frames the highest-scoring compounds as exploratory-only (never "recommendations"
        / "candidates").
      * Any compound classified 'artifact'/'caution' in `compound_flags` is annotated with its
        flag wherever it is named, so a compound flagged in the body cannot appear unflagged
        here.

    stats : dict with keys n_drugs, n_approved, n_appr_sig (ints)
    approved_df : DataFrame with columns including 'canonical_rank' and 'drug' (may be empty)
    n : number of top compounds to name in the caption (default 4)
    verdict : controls verdict — a status string or the dict from controls_verdict() (optional)
    compound_flags : report_config['compound_flags'] single source of truth (optional)

    Returns a caption string. Degrades to a stats-only sentence when the frame is empty.
    """
    n_drugs = stats.get("n_drugs", "N")
    n_approved = stats.get("n_approved", "N")
    n_appr_sig = stats.get("n_appr_sig", "N")

    base = (f"Schematic of the workflow; illustrative only, not a data figure. This run "
            f"screened {n_drugs} perturbations, {n_approved} of them approved, with "
            f"{n_appr_sig} significant approved reversers.")

    if approved_df is None or len(approved_df) == 0:
        return base

    # Read canonical_rank and drug directly off the dataframe
    rank_col = "canonical_rank" if "canonical_rank" in approved_df.columns else "rank"
    drug_col = "drug" if "drug" in approved_df.columns else "pert"
    if rank_col not in approved_df.columns or drug_col not in approved_df.columns:
        return base

    status = _verdict_status(verdict)
    flags = _flag_lookup(compound_flags)

    top = approved_df.head(n)
    parts = []
    any_flagged = False
    for _, r in top.iterrows():
        rank_val = int(r[rank_col]) if pd.notna(r.get(rank_col)) else "?"
        drug_val = str(r[drug_col])
        label = f"#{rank_val} {drug_val}"
        info = flags.get(_norm(drug_val))
        if info and info[2]:  # flagged
            any_flagged = True
            cls, note, _ = info
            note_short = (note[:40] + "...") if len(note) > 43 else note
            label += f" [{cls}: {note_short}]" if note_short else f" [{cls}]"
        parts.append(label)
    named = ", ".join(parts)

    # Failed / weak validation: lead with the verdict; frame as exploratory, NOT recommendations.
    if status in ("fail", "weak"):
        return (f"{base} Positive-control validation did not pass (verdict: {status}); the "
                f"highest-scoring approved compounds are exploratory only, not recommendations: "
                f"{named}. See Table 1 and the validation note above.")

    # Passing (or unknown) verdict: name the top compounds. If any are flagged, drop the
    # "candidates" framing and point to the flagged-compounds table so nothing reads as an
    # unqualified recommendation.
    if any_flagged:
        return (f"{base} The highest-ranked approved compounds are {named} (some flagged; see the "
                f"flagged-compounds table).")
    return f"{base} The canonical top {len(parts)} approved candidates are {named} (Table 1)."


# Late import so the module-level code (prompt builder) does not require pandas.
try:
    import pandas as pd
except ImportError:  # pragma: no cover
    pd = None


if __name__ == "__main__":
    demo = build_infographic_prompt(
        "idiopathic pulmonary fibrosis",
        {"n_drugs": 271, "n_approved": 107, "n_appr_sig": 33},
        top_drugs=["Deferasirox", "Captopril", "Fluticasone"])
    print(demo)
    print("\n--- caption (empty frame) ---")
    print(infographic_caption_from_data(
        {"n_drugs": 271, "n_approved": 107, "n_appr_sig": 33}, None))
