"""
constraint_analysis.py — orchestrate resolve -> fetch -> flag -> assemble table.

Produces a tidy pandas DataFrame with one row per input gene, the standard
LoF-intolerance flag (computed on gnomAD v2.1.1), a v2.1.1-vs-v4.1 version-shift
annotation, and a database-grounded disease/inheritance note.
"""

import pandas as pd

from constraint_fetch import (resolve_gene, fetch_constraint, fetch_disease,
                              LOEUF_CUT, PLI_CUT)
from constraint_druggability import add_druggability_columns


def _round(x, n=3):
    return round(x, n) if isinstance(x, (int, float)) else None


def analyze_genes(gene_inputs, verbose=True):
    """
    gene_inputs : iterable of strings (symbols, aliases, or ENSG ids)
    returns     : pandas.DataFrame (see column list in the skill README)
    """
    rows = []
    for raw in gene_inputs:
        r = resolve_gene(raw)
        sym = r["symbol"]
        if verbose:
            print(f"  {raw!s:12s} -> {sym or 'UNRESOLVED'}"
                  + (f"  [{r['alias_note']}]" if r["alias_note"] else ""))

        if not sym:
            rows.append({"gene": raw, "input_as": r["input_as"], "gene_id": None,
                         "alias_note": "symbol not resolved",
                         "LoF_intolerant": "N/A",
                         "flag_driver": "unresolved gene",
                         "note": "not available (symbol could not be resolved via MyGene.info)"})
            continue

        c2 = fetch_constraint(sym, "v2.1.1")
        c4 = fetch_constraint(sym, "v4.1")

        if not c2 and not c4:
            rows.append({"gene": sym, "input_as": r["input_as"],
                         "gene_id": r["ensembl"], "alias_note": r["alias_note"],
                         "LoF_intolerant": "N/A", "flag_driver": "no constraint record",
                         "note": "not available (no gnomAD constraint record in v2.1.1 or v4.1)"})
            continue

        loeuf2 = (c2 or {}).get("oe_lof_upper")
        pli2 = (c2 or {}).get("pLI")
        loeuf4 = (c4 or {}).get("oe_lof_upper")
        pli4 = (c4 or {}).get("pLI")

        # ---- standard flag on v2.1.1 (fall back to v4.1 only if v2 absent) ----
        basis = "v2.1.1"
        loeuf_f, pli_f = loeuf2, pli2
        if loeuf_f is None and pli_f is None:
            basis, loeuf_f, pli_f = "v4.1", loeuf4, pli4

        drivers = []
        if loeuf_f is not None and loeuf_f < LOEUF_CUT:
            drivers.append("LOEUF")
        if pli_f is not None and pli_f >= PLI_CUT:
            drivers.append("pLI")
        flag = "Yes" if drivers else "No"

        # ---- version-shift annotation (surface MECP2/TP53-type borderline moves) ----
        shift = ""
        if None not in (loeuf2, loeuf4):
            call2 = (loeuf2 < LOEUF_CUT) or (pli2 is not None and pli2 >= PLI_CUT)
            call4 = (loeuf4 < LOEUF_CUT) or (pli4 is not None and pli4 >= PLI_CUT)
            if call2 != call4:
                shift = f"flag changes v2->v4 ({'intol' if call2 else 'tol'}->{'intol' if call4 else 'tol'})"
            elif abs(loeuf2 - loeuf4) >= 0.15:
                shift = f"LOEUF shifts {loeuf2:.2f}->{loeuf4:.2f}"

        # ---- grounded disease note ----
        dz = fetch_disease(r["entrez"])
        disease_note = dz["disease_label"] or "no curated disease association retrieved"

        rows.append({
            "gene": sym,
            "input_as": r["input_as"],
            "gene_id": r["ensembl"] or (c2 or c4 or {}).get("_gene_id"),
            "alias_note": r["alias_note"],
            "obs_lof_v2": (c2 or {}).get("obs_lof"),
            "exp_lof_v2": _round((c2 or {}).get("exp_lof"), 1),
            "oe_lof_v2": _round((c2 or {}).get("oe_lof")),
            "LOEUF_v2": _round(loeuf2),
            "LOEUF_lower_v2": _round((c2 or {}).get("oe_lof_lower")),
            "LOEUF_pct_v2": _round((c2 or {}).get("oe_lof_percentile"), 3),
            "pLI_v2": _round(pli2, 4),
            "lof_z_v2": _round((c2 or {}).get("lof_z"), 2),
            "oe_mis_v2": _round((c2 or {}).get("oe_mis")),
            "mis_z_v2": _round((c2 or {}).get("mis_z"), 2),
            "LOEUF_v4": _round(loeuf4),
            "LOEUF_pct_v4": _round((c4 or {}).get("oe_lof_percentile"), 3),
            "pLI_v4": _round(pli4, 4),
            "flag_basis": basis,
            "LoF_intolerant": flag,
            "flag_driver": "+".join(drivers) if drivers else "-",
            "version_shift": shift,
            "gene_name": dz["gene_name"],
            "disease_label": disease_note,
            "inheritance": dz["inheritance"],
            "mondo_id": dz["mondo_id"],
            "gene_mim": dz["gene_mim"],
            "disease_source": dz["disease_source"],
        })

    df = pd.DataFrame(rows)
    # sort: resolved genes by v2 LOEUF ascending (most constrained first), unresolved last
    if "LOEUF_v2" in df.columns:
        df["_sortkey"] = df["LOEUF_v2"].fillna(9e9)
        df = df.sort_values("_sortkey").drop(columns="_sortkey").reset_index(drop=True)
    # append the drug-target interpretation layer (KO-tolerance tier, systemic-modality
    # risk, recommended strategy) derived from the constraint metrics + inheritance.
    df = add_druggability_columns(df)
    return df
