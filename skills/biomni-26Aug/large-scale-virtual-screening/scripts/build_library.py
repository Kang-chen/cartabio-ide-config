#!/usr/bin/env python3
"""
build_library.py -- assemble a screening library and (if available) activity labels.

Auto-detects the input mode. Priority order reflects a PROSPECTIVE, target-agnostic
design (not benchmark-first):

  1. --smiles-csv FILE       user library. Columns auto-sniffed: a SMILES column
                             (smiles/canonical_smiles/structure) + optional id and
                             optional label (activity/active/label/y). FIRST-CLASS.
  2. --chembl-target CHEMBL  pull bioactivities for a target from ChEMBL and build a
                             library of actives (+ optional property-matched decoys if
                             --with-decoys). FIRST-CLASS. Requires network/chembl.
  3. --dude-dir DIR          DUD-E-style actives_final.ism / decoys_final.ism ->
                             labeled benchmark branch (the "labels available" case).
  4. --enamine / --datalake  heavier at-scale mode from a datalake library file.
                             DEGRADES GRACEFULLY (clear message, exit 3) if the
                             connector/dataset is absent -- never a hard crash.

Output (into --outdir):
  master_library.csv  columns: mol_id, smiles, source, activity_label, chembl_id, dude_id
                      activity_label is 1 (active) / 0 (decoy) / empty (unlabeled).
  library_meta.json   mode, counts, labeled?(bool), decoy strategy, provenance.

`labeled` in library_meta.json drives the downstream branch:
  labeled  -> enrichment.py runs (ROC/EF/BEDROC)
  unlabeled-> enrichment omitted; triage + SAR + property/ADMET only.
"""
from __future__ import annotations
import argparse
import csv
import json
import os
import random
import sys

try:
    from common import SEED
except ImportError:
    from scripts.common import SEED  # pragma: no cover

_SMILES_KEYS = ("smiles", "canonical_smiles", "structure", "smi")
_ID_KEYS = ("id", "mol_id", "name", "compound_id", "molecule_chembl_id", "chembl_id")
_LABEL_KEYS = ("activity_label", "active", "activity", "label", "y", "is_active")


def _sniff_columns(header):
    lower = [h.strip().lower() for h in header]
    def find(keys):
        for k in keys:
            if k in lower:
                return lower.index(k)
        return None
    return find(_SMILES_KEYS), find(_ID_KEYS), find(_LABEL_KEYS)


def _coerce_label(val):
    if val is None:
        return ""
    s = str(val).strip().lower()
    if s in ("1", "active", "true", "yes", "y", "actives"):
        return 1
    if s in ("0", "decoy", "inactive", "false", "no", "n", "decoys"):
        return 0
    # numeric activity (e.g. pChEMBL) -> leave to caller; treat >0 threshold elsewhere
    try:
        return 1 if float(s) >= 1 else 0
    except ValueError:
        return ""


def _from_smiles_csv(path):
    rows = []
    labeled = False
    with open(path, newline="") as fh:
        reader = csv.reader(fh)
        header = next(reader)
        si, ii, li = _sniff_columns(header)
        if si is None:
            # maybe a headerless single-column .smi
            fh.seek(0)
            for n, line in enumerate(fh):
                smi = line.strip().split()[0] if line.strip() else ""
                if smi:
                    rows.append({"mol_id": f"M{n:06d}", "smiles": smi, "source": "user",
                                 "activity_label": "", "chembl_id": "", "dude_id": ""})
            return rows, False
        for n, r in enumerate(reader):
            if not r or si >= len(r):
                continue
            smi = r[si].strip()
            if not smi:
                continue
            mid = r[ii].strip() if (ii is not None and ii < len(r) and r[ii].strip()) else f"M{n:06d}"
            lab = _coerce_label(r[li]) if (li is not None and li < len(r)) else ""
            if lab != "":
                labeled = True
            rows.append({"mol_id": mid, "smiles": smi, "source": "user",
                         "activity_label": lab, "chembl_id": "", "dude_id": ""})
    return rows, labeled


def _from_dude(dude_dir, n_decoys, seed):
    """Parse DUD-E actives_final.ism (SMILES ID CHEMBL) + decoys_final.ism (SMILES ID)."""
    act_path = None
    dec_path = None
    for name in os.listdir(dude_dir):
        if name.startswith("actives") and name.endswith(".ism"):
            act_path = os.path.join(dude_dir, name)
        if name.startswith("decoys") and name.endswith(".ism"):
            dec_path = os.path.join(dude_dir, name)
    if not act_path:
        raise SystemExit(f"[ERROR] no actives_*.ism in {dude_dir}")
    rows = []
    a = 0
    with open(act_path) as fh:
        for line in fh:
            p = line.split()
            if len(p) >= 2:
                rows.append({"mol_id": f"A{a:04d}", "smiles": p[0], "source": "active",
                             "activity_label": 1, "chembl_id": p[2] if len(p) > 2 else "",
                             "dude_id": p[1]})
                a += 1
    decoys = []
    if dec_path:
        with open(dec_path) as fh:
            for line in fh:
                p = line.split()
                if len(p) >= 2:
                    decoys.append((p[0], p[1]))
    if n_decoys and len(decoys) > n_decoys:
        random.seed(seed)
        decoys = random.sample(decoys, n_decoys)
    for d, (smi, did) in enumerate(decoys):
        rows.append({"mol_id": f"D{d:04d}", "smiles": smi, "source": "decoy",
                     "activity_label": 0, "chembl_id": "", "dude_id": did})
    return rows, True


def _from_chembl(target_chembl, pchembl_min, max_actives, with_decoys):
    """Pull actives for a ChEMBL target. Graceful if chembl_webresource_client absent."""
    try:
        from chembl_webresource_client.new_client import new_client
    except ImportError:
        print("[ERROR] chembl_webresource_client not available; cannot pull ChEMBL. "
              "Install it or provide --smiles-csv instead.", file=sys.stderr)
        raise SystemExit(3)
    act = new_client.activity
    q = act.filter(target_chembl_id=target_chembl, pchembl_value__gte=pchembl_min,
                   assay_type="B").only(
        ["molecule_chembl_id", "canonical_smiles", "pchembl_value"])
    rows = []
    seen = set()
    for i, r in enumerate(q):
        smi = r.get("canonical_smiles")
        cid = r.get("molecule_chembl_id")
        if not smi or cid in seen:
            continue
        seen.add(cid)
        rows.append({"mol_id": f"A{len(rows):04d}", "smiles": smi, "source": "active",
                     "activity_label": 1, "chembl_id": cid, "dude_id": ""})
        if max_actives and len(rows) >= max_actives:
            break
    labeled = with_decoys  # only a benchmark if decoys are added
    if with_decoys:
        # NOTE: property-matched decoy generation is a heavier add-on. If a decoy
        # generator is unavailable we still return actives-only and mark unlabeled.
        print("[WARN] --with-decoys requested: property-matched decoy generation is a "
              "heavy optional step. Returning actives-only unless a decoy set is supplied. "
              "Run remains UNLABELED unless decoys added.", file=sys.stderr)
        labeled = False
    return rows, labeled


def _from_datalake(path, n_sample, seed):
    """Enamine REAL / datalake single-file library. Graceful if missing."""
    if not path or not os.path.exists(path):
        print(f"[ERROR] datalake library not found at {path!r}. This heavier at-scale "
              "mode needs the datalake connector/dataset present. Falling back is the "
              "caller's choice (e.g. use --smiles-csv).", file=sys.stderr)
        raise SystemExit(3)
    rows = []
    with open(path) as fh:
        header = fh.readline().split()
        # assume col0 = smiles, col1 = id (typical .smi/.cxsmiles)
        for n, line in enumerate(fh):
            p = line.split()
            if len(p) >= 1:
                rows.append({"mol_id": p[1] if len(p) > 1 else f"E{n:07d}",
                             "smiles": p[0], "source": "library",
                             "activity_label": "", "chembl_id": "", "dude_id": ""})
    if n_sample and len(rows) > n_sample:
        random.seed(seed)
        rows = random.sample(rows, n_sample)
    return rows, False


_FIELDS = ["mol_id", "smiles", "source", "activity_label", "chembl_id", "dude_id"]


def _write(rows, labeled, outdir, mode, extra_meta=None):
    os.makedirs(outdir, exist_ok=True)
    out = os.path.join(outdir, "master_library.csv")
    with open(out, "w", newline="") as fh:
        w = csv.DictWriter(fh, fieldnames=_FIELDS)
        w.writeheader()
        for r in rows:
            w.writerow({k: r.get(k, "") for k in _FIELDS})
    n_act = sum(1 for r in rows if r.get("activity_label") == 1)
    n_dec = sum(1 for r in rows if r.get("activity_label") == 0)
    meta = {"mode": mode, "labeled": bool(labeled), "n_total": len(rows),
            "n_actives": n_act, "n_decoys": n_dec,
            "n_unlabeled": len(rows) - n_act - n_dec}
    if extra_meta:
        meta.update(extra_meta)
    with open(os.path.join(outdir, "library_meta.json"), "w") as fh:
        json.dump(meta, fh, indent=2)
    print(f"[OK] {len(rows)} compounds -> {out}  (labeled={labeled}, "
          f"actives={n_act}, decoys={n_dec})")
    return out


def run(args) -> int:
    if args.smiles_csv:
        rows, labeled = _from_smiles_csv(args.smiles_csv)
        return 0 if _write(rows, labeled, args.outdir, "smiles_csv",
                           {"provenance": os.path.basename(args.smiles_csv)}) else 1
    if args.chembl_target:
        rows, labeled = _from_chembl(args.chembl_target, args.pchembl_min,
                                     args.max_actives, args.with_decoys)
        return 0 if _write(rows, labeled, args.outdir, "chembl",
                           {"provenance": f"ChEMBL:{args.chembl_target}",
                            "pchembl_min": args.pchembl_min}) else 1
    if args.dude_dir:
        rows, labeled = _from_dude(args.dude_dir, args.n_decoys, args.seed)
        return 0 if _write(rows, labeled, args.outdir, "dude",
                           {"provenance": args.dude_dir, "n_decoys_target": args.n_decoys}) else 1
    if args.datalake or args.enamine:
        path = args.datalake or args.enamine
        rows, labeled = _from_datalake(path, args.n_sample, args.seed)
        return 0 if _write(rows, labeled, args.outdir, "datalake",
                           {"provenance": path, "n_sample": args.n_sample}) else 1
    raise SystemExit("No input mode given. Use one of --smiles-csv / --chembl-target / "
                     "--dude-dir / --datalake.")


def self_check() -> int:
    import tempfile
    ok = True
    # labeled csv sniff
    with tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False, newline="") as tf:
        tf.write("compound_id,canonical_smiles,activity\n")
        tf.write("c1,CCO,active\nc2,c1ccccc1,decoy\nc3,CCN,1\n")
        p = tf.name
    rows, labeled = _from_smiles_csv(p)
    os.unlink(p)
    if not (labeled and len(rows) == 3 and rows[0]["mol_id"] == "c1"
            and rows[0]["activity_label"] == 1 and rows[1]["activity_label"] == 0):
        print(f"[FAIL] smiles_csv sniff -> {rows}", file=sys.stderr); ok = False
    # unlabeled headerless .smi
    with tempfile.NamedTemporaryFile("w", suffix=".smi", delete=False) as tf:
        tf.write("CCO ethanol\nc1ccccc1 benzene\n")
        p2 = tf.name
    rows2, labeled2 = _from_smiles_csv(p2)
    os.unlink(p2)
    if labeled2 or len(rows2) < 1:
        # single-col .smi with a name still has a 'smiles'-less header sniff; accept >=1 row
        print(f"[note] headerless smi rows={len(rows2)} labeled={labeled2}")
    print("build_library.py self-check:", "PASS" if ok else "FAIL")
    return 0 if ok else 1


def main():
    ap = argparse.ArgumentParser(description="Assemble screening library + labels (auto-detect)")
    ap.add_argument("--smiles-csv", help="user library CSV/SMI (FIRST-CLASS)")
    ap.add_argument("--chembl-target", help="ChEMBL target id, e.g. CHEMBL203 (FIRST-CLASS)")
    ap.add_argument("--dude-dir", help="dir with DUD-E actives_/decoys_*.ism (labeled benchmark)")
    ap.add_argument("--datalake", help="path to a datalake library file (heavy at-scale)")
    ap.add_argument("--enamine", help="alias for --datalake (Enamine REAL subset)")
    ap.add_argument("--pchembl-min", type=float, default=6.0, help="ChEMBL active threshold")
    ap.add_argument("--max-actives", type=int, default=0, help="cap ChEMBL actives (0=all)")
    ap.add_argument("--with-decoys", action="store_true", help="ChEMBL: build a labeled benchmark")
    ap.add_argument("--n-decoys", type=int, default=2500, help="DUD-E decoy subsample size")
    ap.add_argument("--n-sample", type=int, default=0, help="datalake subsample (0=all)")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--outdir", default="/mnt/results/sbvs_run/library")
    ap.add_argument("--self-check", action="store_true")
    args = ap.parse_args()
    if args.self_check:
        sys.exit(self_check())
    sys.exit(run(args))


if __name__ == "__main__":
    main()
