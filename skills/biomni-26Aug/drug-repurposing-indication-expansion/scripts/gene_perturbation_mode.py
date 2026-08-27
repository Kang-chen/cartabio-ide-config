"""OPTIONAL MODE: target nomination via single-gene perturbation reversal.

Same connectivity engine, different library: instead of drug perturbations, score the
LINCS single-gene perturbation signatures (over-expression / knockdown) against the
disease signature. A gene perturbation whose transcriptional consequence REVERSES the
disease signature nominates that gene (and its direction) as a candidate therapeutic
target:

  - if GENE-KNOCKDOWN reverses disease  -> INHIBIT the gene (antagonist / degrader)
  - if GENE-OVEREXPRESSION reverses disease -> ACTIVATE the gene (agonist / mimetic)

This complements the drug screen: drugs tell you what to give, target nomination tells
you what pathway to drug (and can rationalise the MOA of the top drug hits).

The single_gene_perturbations GMT encodes direction in the set name (e.g. GENE-up for
over-expression signatures, GENE-dn for knockdown), plus the up/dn of the readout. The
harmonize step already splits '<name>-up'/'<name>-dn' into readout directions; the base
name carries the gene + perturbation type. This module runs the standard engine and then
annotates the base name into (gene, perturbation_type).

Public API:
  run_target_nomination(disease_up, disease_dn, gene_gmt, m2h, BG, workdir, ...) -> DataFrame
  parse_gene_perturbation_name(base) -> (gene, ptype)
"""
import os
import re
import pickle
import pandas as pd

import harmonize_signatures as hz
import connectivity_score as cs


def parse_gene_perturbation_name(base):
    """Best-effort split of a gene-perturbation set base-name into (gene, ptype).

    LINCS single_gene_perturbations names look like 'GENE knockdown ...' or
    'GENE overexpression ...' or contain KD/OE/shRNA/cDNA tokens. Returns
    (gene_symbol_or_base, perturbation_type in {'knockdown','overexpression','unknown'}).
    """
    b = str(base)
    low = b.lower()
    ptype = "unknown"
    if any(t in low for t in ["knockdown", "shrna", "sirna", "-kd", " kd", "loss", "depletion", "crispr", "ko "]):
        ptype = "knockdown"
    elif any(t in low for t in ["overexpression", "over-expression", "cdna", "-oe", " oe", "gain"]):
        ptype = "overexpression"
    # gene = first token that looks like a symbol
    m = re.match(r"([A-Za-z0-9\-]+)", b.strip())
    gene = m.group(1).upper() if m else b
    return gene, ptype


def run_target_nomination(disease_up, disease_dn, gene_gmt, m2h, BG,
                          workdir, nperm=10000, seed=42, min_set_genes=5):
    """Run the connectivity engine with the single-gene perturbation library.

    Returns a DataFrame with S_reversal etc. plus gene / perturbation_type / implication.
    """
    os.makedirs(os.path.join(workdir, "data"), exist_ok=True)
    # harmonize the gene-perturbation library the same way as drugs
    lib = hz.split_updn_library(gene_gmt)
    pert_sigs = {}
    for base, dirs in lib.items():
        up = hz.to_human(dirs.get("up", []), hz.classify_organism(dirs.get("up", [])), m2h)
        dn = hz.to_human(dirs.get("dn", []), hz.classify_organism(dirs.get("dn", [])), m2h)
        up = sorted(set(up) - set(dn))
        dn = sorted(set(dn) - set(up))
        if len(up) + len(dn) >= min_set_genes:
            pert_sigs[base] = {"up": up, "dn": dn,
                               "organism": hz.classify_organism(list(dirs.get("up", [])) + list(dirs.get("dn", [])))}
    bundle = {"disease_up": sorted(set(disease_up)), "disease_dn": sorted(set(disease_dn)),
              "pert_sigs": pert_sigs, "BG": BG, "meta": {"library": "single_gene_perturbations"}}
    bp = os.path.join(workdir, "data", "gene_harmonized.pkl")
    with open(bp, "wb") as fh:
        pickle.dump(bundle, fh)
    conn = cs.run(bundle_pickle=bp,
                  out_csv=os.path.join(workdir, "data", "gene_connectivity.csv"),
                  nperm=nperm, seed=seed)
    # annotate gene / ptype / implication
    parsed = conn["pert"].map(parse_gene_perturbation_name)
    conn["gene"] = [p[0] for p in parsed]
    conn["perturbation_type"] = [p[1] for p in parsed]

    def implication(row):
        if row["S_reversal"] <= 0:
            return "no reversal"
        if row["perturbation_type"] == "knockdown":
            return f"INHIBIT {row['gene']}"
        if row["perturbation_type"] == "overexpression":
            return f"ACTIVATE {row['gene']}"
        return f"modulate {row['gene']}"

    conn["target_implication"] = conn.apply(implication, axis=1)
    conn = conn.sort_values("S_reversal", ascending=False).reset_index(drop=True)
    conn.to_csv(os.path.join(workdir, "data", "target_nomination.csv"), index=False)
    return conn
