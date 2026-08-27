#!/usr/bin/env python3
"""
run_deeppurpose.py — Deep-learning drug-target interaction prediction (SECONDARY predictor).

Uses DeepPurpose's pretrained BindingDB model (morgan_cnn_bindingdb) to predict a binding
affinity for the query against every panel target that has a UniProt sequence. This is an
ORTHOGONAL signal to ligand similarity: it uses a learned sequence+ligand model rather than
nearest-neighbor chemistry, so agreement between the two ("consensus") is more trustworthy
than either alone.

Model card: drug_encoding=Morgan, target_encoding=CNN, pretrained on BindingDB (Kd/Ki/IC50).
Output is -log10(affinity) style; we convert to nM via dp_pred_nM = 10^(9 - dp_affinity).
Runs CPU-only (CUDA_VISIBLE_DEVICES="") for portability.

NOTE: DeepPurpose is a SEQUENCE-based model and does NOT do leave-query-out (it never sees
ChEMBL actives), so it cannot leak the query. It is a weaker standalone predictor
(reference ROC-AUC ~0.55) but valuable as an independent vote in the consensus.

Usage:
  python run_deeppurpose.py --smiles "<smi>" --panel-seqs <outdir>/tmp/panel_with_seqs.csv \
        --outdir <outdir>

Output:
  <outdir>/data/offtarget_deeppurpose_predictions.csv
     columns: uniprot,label,chembl_target_id,dp_affinity,dp_pred_nM
"""
import argparse, os, sys, json
os.environ["CUDA_VISIBLE_DEVICES"] = ""  # force CPU before importing torch/DeepPurpose
import warnings
warnings.filterwarnings("ignore")
import pandas as pd


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--smiles", required=True)
    ap.add_argument("--panel-seqs", required=True)
    ap.add_argument("--outdir", required=True)
    ap.add_argument("--model", default="morgan_cnn_bindingdb")
    args = ap.parse_args()
    os.makedirs(f"{args.outdir}/data", exist_ok=True)

    try:
        from DeepPurpose import utils as dp_utils
        from DeepPurpose import DTI as dp_models
    except Exception as e:
        print(json.dumps({"status": "error",
                          "msg": f"DeepPurpose unavailable: {e}"}))
        sys.exit(2)

    panel = pd.read_csv(args.panel_seqs)
    panel = panel.dropna(subset=["sequence"]).reset_index(drop=True)
    if len(panel) == 0:
        print(json.dumps({"status": "error", "msg": "no targets with sequences"}))
        sys.exit(1)

    drugs = [args.smiles] * len(panel)
    targets = panel["sequence"].tolist()
    names = panel["label"].tolist()

    # DeepPurpose downloads pretrained weights into ./save_folder relative to CWD.
    # chdir into a temp cache dir so it never pollutes the user's output directory.
    import tempfile
    _cwd = os.getcwd()
    _cache = os.path.join(tempfile.gettempdir(), "deeppurpose_cache")
    os.makedirs(_cache, exist_ok=True)
    os.chdir(_cache)
    try:
        try:
            model = dp_models.model_pretrained(model=args.model)
        except Exception as e:
            os.chdir(_cwd)
            print(json.dumps({"status": "error",
                              "msg": f"could not load pretrained {args.model}: {e}"}))
            sys.exit(2)
    finally:
        os.chdir(_cwd)

    X = dp_utils.data_process(
        X_drug=drugs, X_target=targets, y=[0] * len(drugs),
        drug_encoding="Morgan", target_encoding="CNN",
        split_method="no_split")
    preds = model.predict(X)

    df = panel[["uniprot", "label", "chembl_target_id"]].copy()
    df["dp_affinity"] = list(preds)
    # convert model output (-log10 M-ish) to nM
    df["dp_pred_nM"] = df["dp_affinity"].apply(lambda a: 10 ** (9 - a))
    df = df.sort_values("dp_affinity", ascending=False)
    df.to_csv(f"{args.outdir}/data/offtarget_deeppurpose_predictions.csv", index=False)

    print(json.dumps({"status": "ok", "model": args.model, "n_targets": len(df),
                      "top": df.head(8)[["label", "dp_affinity", "dp_pred_nM"]]
                      .to_dict("records")}, default=str))


if __name__ == "__main__":
    main()
