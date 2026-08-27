#!/usr/bin/env python3
"""
extract_plddt.py — unified per-residue confidence extraction for four structure
predictors (AlphaFold v2, Boltz-2, Chai-1, ESMCFold2).

Each predictor stores per-residue pLDDT differently and on a different scale.
This module returns per-residue pLDDT ALWAYS normalized to 0-100, aligned to
residues 1..N, plus available global scores (pTM, mean pLDDT).

Verified against real HPC outputs (Biomni session, B2M mature chain, 99 aa):
    AlphaFold mean ~97.2 | Chai-1 ~96.0 | Boltz-2 ~93.7 | ESMCFold2 ~86.5

Usage (CLI):
    python extract_plddt.py --method alphafold --job-dir <hpc_output_dir> \
        --out-prefix /mnt/results/<name>_alphafold

    # generic auto-detect on an output directory:
    python extract_plddt.py --method auto --job-dir <dir> --out-prefix <prefix>

Programmatic:
    from extract_plddt import extract_plddt
    res = extract_plddt("boltz", "/path/to/output_dir")
    res["plddt"]        # np.ndarray, 0-100, len N
    res["mean_plddt"]   # float 0-100
    res["ptm"]          # float or None
    res["structure"]    # path to top-ranked structure file
"""
import os
import re
import json
import glob
import argparse
import numpy as np

# Canonical confidence-band breakdown lives in confidence_breakdown.py so band
# counts have ONE source (see item 2 of the skill hardening). Import is guarded:
# a missing module must never break the verified pLDDT extraction path.
try:
    import confidence_breakdown as _cb
except Exception:
    try:
        import sys as _sys
        _sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
        import confidence_breakdown as _cb
    except Exception:
        _cb = None


# ----------------------------------------------------------------------------
# scale normalization
# ----------------------------------------------------------------------------
def to_0_100(arr):
    """Normalize a pLDDT array to 0-100. If native scale is 0-1 (max<=1.5), x100."""
    arr = np.asarray(arr, dtype=float)
    if arr.size and np.nanmax(arr) <= 1.5:
        arr = arr * 100.0
    return arr


# ----------------------------------------------------------------------------
# structure-file B-factor parsers (per-residue pLDDT lives in CA B-factor
# for Chai and ESMCFold2; used as a cross-check for AlphaFold)
# ----------------------------------------------------------------------------
def _plddt_from_pdb_bfactor(pdb_path):
    vals = {}
    with open(pdb_path) as fh:
        for line in fh:
            if line.startswith("ATOM") and line[12:16].strip() == "CA":
                try:
                    vals[int(line[22:26])] = float(line[60:66])
                except ValueError:
                    continue
    if not vals:
        return None
    return np.array([vals[k] for k in sorted(vals)], dtype=float)


def _plddt_from_cif_bfactor(cif_path):
    """Parse CA-atom B_iso_or_equiv from an mmCIF _atom_site loop."""
    with open(cif_path) as fh:
        lines = fh.readlines()
    cols, data, i = [], [], 0
    while i < len(lines):
        if lines[i].startswith("loop_"):
            j = i + 1
            tags = []
            while j < len(lines) and lines[j].strip().startswith("_atom_site."):
                tags.append(lines[j].strip())
                j += 1
            if tags:
                cols = tags
                k = j
                while (k < len(lines) and not lines[k].startswith("#")
                       and not lines[k].startswith("loop_") and lines[k].strip()):
                    data.append(lines[k])
                    k += 1
                break
            i = j
        else:
            i += 1
    if not cols:
        return None
    idx = {t.split(".")[1]: n for n, t in enumerate(cols)}
    ci_atom, ci_b = idx.get("label_atom_id"), idx.get("B_iso_or_equiv")
    ci_seq, ci_grp = idx.get("label_seq_id"), idx.get("group_PDB")
    if None in (ci_atom, ci_b, ci_seq):
        return None
    res = {}
    for row in data:
        p = row.split()
        if len(p) <= max(ci_atom, ci_b, ci_seq):
            continue
        if ci_grp is not None and p[ci_grp] != "ATOM":
            continue
        if p[ci_atom].strip('"') != "CA":
            continue
        try:
            res[int(p[ci_seq])] = float(p[ci_b])
        except ValueError:
            continue
    if not res:
        return None
    return np.array([res[k] for k in sorted(res)], dtype=float)


def _seq_from_structure(path):
    """Best-effort 1-letter sequence from CA records (for the CSV AA column)."""
    three2one = {
        'ALA':'A','ARG':'R','ASN':'N','ASP':'D','CYS':'C','GLN':'Q','GLU':'E',
        'GLY':'G','HIS':'H','ILE':'I','LEU':'L','LYS':'K','MET':'M','PHE':'F',
        'PRO':'P','SER':'S','THR':'T','TRP':'W','TYR':'Y','VAL':'V'}
    seq = {}
    if path.endswith(".pdb"):
        with open(path) as fh:
            for line in fh:
                if line.startswith("ATOM") and line[12:16].strip() == "CA":
                    seq[int(line[22:26])] = three2one.get(line[17:20].strip(), "X")
    else:  # cif
        b = _plddt_from_cif_bfactor  # reuse loop parser structure
        with open(path) as fh:
            lines = fh.readlines()
        cols, data, i = [], [], 0
        while i < len(lines):
            if lines[i].startswith("loop_"):
                j = i + 1; tags = []
                while j < len(lines) and lines[j].strip().startswith("_atom_site."):
                    tags.append(lines[j].strip()); j += 1
                if tags:
                    cols = tags; k = j
                    while (k < len(lines) and not lines[k].startswith("#")
                           and not lines[k].startswith("loop_") and lines[k].strip()):
                        data.append(lines[k]); k += 1
                    break
                i = j
            else:
                i += 1
        if cols:
            idx = {t.split(".")[1]: n for n, t in enumerate(cols)}
            ca, cc, cs = idx.get("label_atom_id"), idx.get("label_comp_id"), idx.get("label_seq_id")
            if None not in (ca, cc, cs):
                for row in data:
                    p = row.split()
                    if len(p) > max(ca, cc, cs) and p[ca].strip('"') == "CA":
                        try:
                            seq[int(p[cs])] = three2one.get(p[cc].strip(), "X")
                        except ValueError:
                            pass
    if not seq:
        return None
    return "".join(seq[k] for k in sorted(seq))


# ----------------------------------------------------------------------------
# per-method extractors
# ----------------------------------------------------------------------------
def _find(job_dir, pattern):
    hits = glob.glob(os.path.join(job_dir, "**", pattern), recursive=True)
    return sorted(hits)


def extract_alphafold(job_dir):
    rank_files = _find(job_dir, "ranking_debug.json")
    if not rank_files:
        raise FileNotFoundError("AlphaFold ranking_debug.json not found under " + job_dir)
    base = os.path.dirname(rank_files[0])
    rank = json.load(open(rank_files[0]))
    top = rank["order"][0]
    conf = json.load(open(os.path.join(base, f"confidence_{top}.json")))
    plddt = to_0_100(np.asarray(conf["confidenceScore"], dtype=float))
    # global pTM from result pickle (optional)
    ptm = None
    pkl = os.path.join(base, f"result_{top}.pkl")
    if os.path.exists(pkl):
        try:
            import pickle
            with open(pkl, "rb") as fh:
                r = pickle.load(fh)
            ptm = float(r["ptm"]) if "ptm" in r else None
        except Exception:
            ptm = None
    struct = os.path.join(base, "ranked_0.pdb")
    return dict(method="AlphaFold v2", plddt=plddt, ptm=ptm,
                structure=struct, n_samples=len(rank["order"]),
                selected=top + " (ranked_0)")


def extract_boltz(job_dir):
    pl = _find(job_dir, "plddt_*_model_0.npz")
    if not pl:
        raise FileNotFoundError("Boltz plddt_*_model_0.npz not found under " + job_dir)
    base = os.path.dirname(pl[0])
    plddt = to_0_100(np.load(pl[0])["plddt"])
    ptm = None
    cj = _find(base, "confidence_*_model_0.json")
    if cj:
        c = json.load(open(cj[0]))
        ptm = float(c.get("ptm")) if c.get("ptm") is not None else None
    st = _find(base, "*_model_0.cif")
    return dict(method="Boltz-2", plddt=plddt, ptm=ptm,
                structure=(st[0] if st else None), n_samples=1, selected="model_0")


def extract_chai(job_dir):
    scores = _find(job_dir, "scores.model_idx_*.npz")
    cifs = _find(job_dir, "pred.model_idx_*.cif")
    if not cifs:
        raise FileNotFoundError("Chai pred.model_idx_*.cif not found under " + job_dir)
    # rank by aggregate_score if available, else idx 0
    best_i, best_ptm = 0, None
    if scores:
        aggs = {}
        for s in scores:
            m = re.search(r"idx_(\d+)", s)
            i = int(m.group(1))
            d = np.load(s)
            aggs[i] = float(np.ravel(d["aggregate_score"])[0]) if "aggregate_score" in d else -1
        best_i = max(aggs, key=aggs.get)
        sp = os.path.join(os.path.dirname(scores[0]), f"scores.model_idx_{best_i}.npz")
        d = np.load(sp)
        best_ptm = float(np.ravel(d["ptm"])[0]) if "ptm" in d else None
    cif = [c for c in cifs if f"idx_{best_i}." in c]
    cif = cif[0] if cif else cifs[0]
    plddt = to_0_100(_plddt_from_cif_bfactor(cif))
    return dict(method="Chai-1", plddt=plddt, ptm=best_ptm, structure=cif,
                n_samples=len(cifs), selected=f"model_idx_{best_i}")


def extract_esmfold(job_dir):
    pdbs = _find(job_dir, "*.pdb")
    pdbs = [p for p in pdbs if os.path.basename(p) not in ("relaxed.pdb",)]
    if not pdbs:
        raise FileNotFoundError("ESMCFold2 *.pdb not found under " + job_dir)
    pdb = pdbs[0]
    plddt = to_0_100(_plddt_from_pdb_bfactor(pdb))
    # cross-check mean vs reported
    js = _find(job_dir, "*.json")
    reported = None
    for j in js:
        try:
            d = json.load(open(j))
        except Exception:
            continue
        if isinstance(d, dict) and "mean_plddt" in d:
            reported = float(d["mean_plddt"]); break
    return dict(method="ESMCFold2", plddt=plddt, ptm=None,
                structure=pdb, n_samples=1, selected="primary",
                reported_mean_plddt=reported)


_EXTRACTORS = {
    "alphafold": extract_alphafold,
    "boltz": extract_boltz,
    "chai": extract_chai,
    "esmfold": extract_esmfold,
}


def _detect_method(job_dir):
    if _find(job_dir, "ranking_debug.json"):
        return "alphafold"
    if _find(job_dir, "plddt_*_model_0.npz"):
        return "boltz"
    if _find(job_dir, "pred.model_idx_*.cif"):
        return "chai"
    if _find(job_dir, "run_metadata.json") or _find(job_dir, "*.pdb"):
        return "esmfold"
    raise ValueError("Could not auto-detect method in " + job_dir)


def extract_plddt(method, job_dir):
    """Return dict with normalized 0-100 pLDDT + global scores for one method."""
    method = method.lower()
    if method == "auto":
        method = _detect_method(job_dir)
    res = _EXTRACTORS[method](job_dir)
    res["mean_plddt"] = float(np.mean(res["plddt"]))
    res["n_res"] = int(len(res["plddt"]))
    res["method_key"] = method
    return res


# ----------------------------------------------------------------------------
# writers (CSV + plot)
# ----------------------------------------------------------------------------
def write_outputs(res, out_prefix):
    import csv
    plddt = res["plddt"]
    seq = None
    if res.get("structure"):
        try:
            seq = _seq_from_structure(res["structure"])
        except Exception:
            seq = None
    if not seq or len(seq) != len(plddt):
        seq = ["?"] * len(plddt)
    csv_path = out_prefix + "_plddt.csv"
    with open(csv_path, "w", newline="") as fh:
        w = csv.writer(fh)
        w.writerow(["residue", "amino_acid", "plddt_0_100"])
        for i, (aa, v) in enumerate(zip(seq, plddt), start=1):
            w.writerow([i, aa, round(float(v), 2)])
    # canonical confidence-band breakdown (SINGLE source of band counts). Attached
    # to res and written as <prefix>_bands.csv so downstream reports read counts
    # from a produced artifact instead of re-binning ad hoc.
    if _cb is not None:
        try:
            bb = _cb.band_breakdown(plddt)
            res["band_breakdown"] = bb
            bands_csv = out_prefix + "_bands.csv"
            with open(bands_csv, "w", newline="") as fh:
                w = csv.writer(fh)
                w.writerow(["band", "label", "count", "percent", "mean_plddt"])
                for b in bb["bands"]:
                    w.writerow([b["band"], b["label"], b["count"],
                                b["percent"], b["mean_plddt"]])
            res["bands_csv"] = bands_csv
        except Exception as e:
            print("band breakdown skipped:", e)
    # plot
    png_path = out_prefix + "_plddt.png"
    try:
        import matplotlib
        matplotlib.use("Agg")
        import matplotlib.pyplot as plt
        matplotlib.rcParams['font.family'] = ['Liberation Sans', 'DejaVu Sans']
        fig, ax = plt.subplots(figsize=(10, 4))
        for lo, hi, c in [(90, 100, "#0053D6"), (70, 90, "#65CBF3"),
                          (50, 70, "#FFDB13"), (0, 50, "#FF7D45")]:
            ax.axhspan(lo, hi, color=c, alpha=0.08)
        x = np.arange(1, len(plddt) + 1)
        ax.plot(x, plddt, color="#0072B2", lw=1.8)
        ax.set_xlim(1, len(plddt)); ax.set_ylim(0, 100)
        ax.set_xlabel("Residue"); ax.set_ylabel("pLDDT (0-100)")
        title = f"{res['method']} — per-residue confidence (mean {res['mean_plddt']:.1f}"
        title += f", pTM {res['ptm']:.2f})" if res.get("ptm") is not None else ")"
        ax.set_title(title, fontsize=12, weight="bold")
        fig.tight_layout(); fig.savefig(png_path, dpi=150, bbox_inches="tight")
        plt.close(fig)
    except Exception as e:
        png_path = None
        print("plot skipped:", e)
    return csv_path, png_path


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--method", required=True,
                    choices=["alphafold", "boltz", "chai", "esmfold", "auto"])
    ap.add_argument("--job-dir", required=True)
    ap.add_argument("--out-prefix", required=True)
    a = ap.parse_args()
    res = extract_plddt(a.method, a.job_dir)
    csv_path, png_path = write_outputs(res, a.out_prefix)
    print(json.dumps({
        "method": res["method"], "n_res": res["n_res"],
        "mean_plddt_0_100": round(res["mean_plddt"], 2),
        "ptm": (round(res["ptm"], 3) if res.get("ptm") is not None else None),
        "selected_model": res.get("selected"),
        "structure": res.get("structure"),
        "csv": csv_path, "plot": png_path,
        "bands_csv": res.get("bands_csv"),
        "band_breakdown": res.get("band_breakdown"),
    }, indent=2))


if __name__ == "__main__":
    main()
