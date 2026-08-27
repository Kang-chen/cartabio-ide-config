"""
immunogenicity_mhcii.py - MHC-II (HLA-DR) T-cell epitope assessment with
graceful degradation (§7).

Predictor tier order (never hard-fail the whole report):
  1. LOCAL NetMHCIIpan   - preferred. Used iff a customer install is present
     (env var NETMHCIIPAN_BIN pointing to the executable, or `netMHCIIpan` on
     PATH). NEVER auto-downloaded (academic-license, email-gated at DTU).
  2. IEDB web API        - netmhciipan_el, needs egress to
     tools-cluster-interface.iedb.org.
  3. NEITHER reachable   -> return status='unavailable' with the SPECIFIC
     reason (egress blocked vs missing install). Per project decision NO
     approximate/bundled-matrix epitope numbers are emitted; downstream
     scorecard must DISCLOSE the missing immunogenicity axis instead of
     silently ranking without it.

Allele panel is a PARAMETER (§8); default = 7-allele IEDB reference HLA-DR
panel (~99% pop coverage). DP/DQ are NOT modelled (documented limitation).

Epitope-calling (validated thresholds): strong rank<=2.0, binder rank<=10.0,
promiscuous = binds across >=2 alleles.
"""
from __future__ import annotations
import io
import os
import re
import time
import json
import sys
import shutil
import argparse
import subprocess
import tempfile

import numpy as np
import pandas as pd

from ab_core import make_chain, region_map, DR_PANEL_7, DEFAULT_SCHEME

IEDB_URL = "https://tools-cluster-interface.iedb.org/tools_api/mhcii/"
METHOD = "netmhciipan_el"
LEN = 15
STRONG, BIND, PROMISC_N = 2.0, 10.0, 2


# ---------------------------------------------------------------------------
# Predictor tier 1: local NetMHCIIpan
# ---------------------------------------------------------------------------
def _local_netmhciipan_bin():
    b = os.environ.get("NETMHCIIPAN_BIN")
    if b and os.path.isfile(b) and os.access(b, os.X_OK):
        return b
    return shutil.which("netMHCIIpan")


def _allele_for_local(allele: str) -> str:
    """NetMHCIIpan CLI allele format, e.g. 'HLA-DRB1*01:01' -> 'DRB1_0101'."""
    m = re.search(r"DRB1\*(\d+):(\d+)", allele)
    return f"DRB1_{m.group(1)}{m.group(2)}" if m else allele


def predict_local(seq, alleles, binary):
    """Run local NetMHCIIpan for one sequence across alleles. Returns df or None."""
    frames = []
    with tempfile.TemporaryDirectory() as td:
        fa = os.path.join(td, "q.fa")
        with open(fa, "w") as fh:
            fh.write(f">query\n{seq}\n")
        for al in alleles:
            cli_al = _allele_for_local(al)
            try:
                p = subprocess.run([binary, "-f", fa, "-inptype", "0",
                                    "-length", str(LEN), "-a", cli_al],
                                   capture_output=True, text=True, timeout=300)
            except Exception:
                return None
            df = _parse_netmhciipan_stdout(p.stdout, al)
            if df is not None and len(df):
                frames.append(df)
    return pd.concat(frames, ignore_index=True) if frames else None


def _parse_netmhciipan_stdout(text, allele):
    """Parse the fixed-width NetMHCIIpan stdout table into a normalized df with
    columns: allele, start, peptide, rank."""
    rows = []
    for ln in text.splitlines():
        parts = ln.split()
        # data rows begin with an integer position; rank%% is present
        if len(parts) >= 7 and parts[0].isdigit():
            try:
                start = int(parts[0])
                peptide = parts[2]
                # %Rank column varies by version; take the last float that
                # looks like a rank (0-100)
                floats = [float(x) for x in parts if _isfloat(x)]
                rank = None
                for f in reversed(floats):
                    if 0 <= f <= 100:
                        rank = f
                        break
                if rank is not None:
                    rows.append({"allele": allele, "start": start,
                                 "peptide": peptide, "rank": rank})
            except Exception:
                continue
    return pd.DataFrame(rows)


def _isfloat(x):
    try:
        float(x)
        return True
    except ValueError:
        return False


# ---------------------------------------------------------------------------
# Predictor tier 2: IEDB web API
# ---------------------------------------------------------------------------
def predict_iedb(seq, allele, retries=3):
    import requests
    for _ in range(retries):
        try:
            r = requests.post(IEDB_URL, data={"method": METHOD,
                              "sequence_text": seq, "allele": allele,
                              "length": str(LEN)}, timeout=120)
            if r.status_code == 200 and r.text.startswith("allele"):
                df = pd.read_csv(io.StringIO(r.text), sep="\t")
                df["allele"] = allele
                # normalize columns
                keep = df.rename(columns={"start": "start", "peptide": "peptide",
                                          "rank": "rank"})
                return keep[["allele", "start", "peptide", "rank"]]
            time.sleep(3)
        except Exception:
            time.sleep(4)
    return None


# ---------------------------------------------------------------------------
# Epitope summary (validated)
# ---------------------------------------------------------------------------
def epitope_summary(mhc_df, chain_name, cdr_idx):
    d = mhc_df.copy()
    d["chain"] = chain_name
    d["is_binder"] = d["rank"] <= BIND
    d["is_strong"] = d["rank"] <= STRONG
    g = (d.groupby(["chain", "start", "peptide"])
           .agg(n_allele_bind=("is_binder", "sum"),
                n_allele_strong=("is_strong", "sum"),
                min_rank=("rank", "min"),
                mean_rank=("rank", "mean")).reset_index())
    g["promiscuous"] = g["n_allele_bind"] >= PROMISC_N
    g["strong_promiscuous"] = g["n_allele_strong"] >= PROMISC_N
    g["overlaps_CDR"] = g["start"].apply(
        lambda s: len(set(range(s - 1, s + LEN - 1)) & cdr_idx) > 0)
    summ = {"chain": chain_name,
            "n_15mers": int(d["peptide"].nunique()),
            "epitope_load": int(d["is_binder"].sum()),
            "strong_load": int(d["is_strong"].sum()),
            "promiscuous": int(g["promiscuous"].sum()),
            "strong_promiscuous": int(g["strong_promiscuous"].sum()),
            "promisc_in_CDR": int((g["promiscuous"] & g["overlaps_CDR"]).sum()),
            "promisc_in_FR": int((g["promiscuous"] & ~g["overlaps_CDR"]).sum())}
    return g, summ


# ---------------------------------------------------------------------------
# Orchestration with degradation
# ---------------------------------------------------------------------------
def run_chain(seq, chain_name, alleles, scheme, predictor, local_bin=None):
    c = make_chain(seq, scheme=scheme)
    cdr_idx = set(i for i, (_, _, r) in enumerate(region_map(c)) if "CDR" in r)
    if predictor == "local":
        raw = predict_local(seq, alleles, local_bin)
    else:
        frames = []
        for al in alleles:
            df = predict_iedb(seq, al)
            if df is not None:
                frames.append(df)
        raw = pd.concat(frames, ignore_index=True) if frames else None
    if raw is None or not len(raw):
        return None, None
    return epitope_summary(raw, chain_name, cdr_idx)


def assess_immunogenicity(constructs: dict, alleles=None,
                          scheme=DEFAULT_SCHEME):
    """Returns dict with 'status' in {'ok','unavailable'}.
    If 'ok': 'per_epitope' (df), 'summary' (df), 'predictor', 'alleles'.
    If 'unavailable': 'reason' (specific: egress blocked vs missing install)."""
    alleles = alleles or DR_PANEL_7
    local_bin = _local_netmhciipan_bin()
    predictor = "local" if local_bin else "iedb"

    # Probe reachability once (cheap) so we can report a specific reason.
    if predictor == "iedb":
        try:
            import requests
            # Probe peptide must be >= LEN (15) or IEDB returns an error and we
            # would wrongly conclude 'unavailable'. Use a known 16-mer.
            probe = requests.post(IEDB_URL, data={"method": METHOD,
                                  "sequence_text": "PKYVKQNTLKLATGMR",
                                  "allele": alleles[0], "length": str(LEN)},
                                  timeout=30)
            if not (probe.status_code == 200 and probe.text.startswith("allele")):
                return {"status": "unavailable",
                        "reason": f"IEDB API reachable but returned "
                                  f"status={probe.status_code}; no local "
                                  f"NetMHCIIpan install found "
                                  f"(set NETMHCIIPAN_BIN to use one)."}
        except Exception as e:
            return {"status": "unavailable",
                    "reason": f"Network egress to IEDB blocked ({type(e).__name__}); "
                              f"no local NetMHCIIpan install found "
                              f"(set NETMHCIIPAN_BIN to use one)."}
        # Probe succeeded: the hosted IEDB API will be used, which transmits the
        # input antibody sequences off-site. Surface this in the run log so the
        # data flow is visible (print-only; does not change what is computed).
        print("[immunogenicity] Using hosted IEDB API (iedb.org); input "
              "antibody sequences are transmitted off-site for MHC-II "
              "prediction. Set NETMHCIIPAN_BIN to a local NetMHCIIpan install "
              "to keep sequences on-machine.", file=sys.stderr)

    per_epi, summ = [], []
    for name, v in constructs.items():
        for dom, seq in (("VH", v.get("VH")), ("VL", v.get("VL"))):
            if not seq:
                continue
            g, s = run_chain(seq, f"{name}_{dom}", alleles, scheme,
                             predictor, local_bin)
            if g is None:
                return {"status": "unavailable",
                        "reason": f"{predictor} predictor failed mid-run on "
                                  f"{name}_{dom}; partial results discarded to "
                                  f"avoid an inconsistent epitope table."}
            per_epi.append(g)
            summ.append(s)
    return {"status": "ok", "predictor": predictor, "alleles": alleles,
            "per_epitope": pd.concat(per_epi, ignore_index=True),
            "summary": pd.DataFrame(summ)}


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--constructs", required=True)
    ap.add_argument("--alleles", help="comma-separated; default 7-allele DR panel")
    ap.add_argument("--scheme", default=DEFAULT_SCHEME)
    ap.add_argument("--outdir", default="/mnt/results/tables")
    a = ap.parse_args()
    constructs = json.load(open(a.constructs))
    alleles = a.alleles.split(",") if a.alleles else None
    res = assess_immunogenicity(constructs, alleles, a.scheme)
    import os
    os.makedirs(a.outdir, exist_ok=True)
    if res["status"] == "ok":
        res["summary"].to_csv(f"{a.outdir}/04_immuno_summary.csv", index=False)
        res["per_epitope"].to_csv(f"{a.outdir}/04_immuno_per_epitope.csv", index=False)
        print(f"Predictor: {res['predictor']}")
        print(res["summary"].to_string(index=False))
    else:
        json.dump(res, open(f"{a.outdir}/04_immuno_UNAVAILABLE.json", "w"), indent=2)
        print("IMMUNOGENICITY UNAVAILABLE:", res["reason"])
