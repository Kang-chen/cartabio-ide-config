"""Resolve the disease signature input contract (disease-agnostic).

Three ways to obtain the disease up/down gene sets:

  A. Built-in LINCS disease signature -- match a disease NAME to one of the ~333
     '<disease>-up' / '<disease>-dn' entries in disease_signatures-v1.0.gmt.
     Fuzzy match; returns candidates for the agent to confirm with the user.

  B. User-supplied explicit gene lists -- pass up=[...], dn=[...] (symbols).

  C. User-supplied DE results table -- a CSV/TSV with columns for gene symbol,
     log2 fold change, and adjusted p-value; up/dn derived by thresholds.

Also resolves the perturbation library GMT (default: LINCS single-drug perturbations;
optional: single-gene perturbations for target nomination).

Public API:
  list_disease_signatures(gmt) -> list[str] of base disease names
  match_disease(name, gmt, topn=10) -> list[(base_name, score)]
  load_builtin_disease(base_name, gmt) -> (up_genes, dn_genes)
  signature_from_de_table(path, gene_col, lfc_col, padj_col, ...) -> (up, dn)
  DISEASE_GMT, DRUG_GMT, GENE_GMT constants
"""
import re
import difflib
import pandas as pd

DATALAKE = "/mnt/datalake/LINCS1000/RNAseq_transcriptomics_genesets"
DISEASE_GMT = f"{DATALAKE}/disease_signatures-v1.0.gmt"
DRUG_GMT = f"{DATALAKE}/single_drug_perturbations-v1.0.gmt"
GENE_GMT = f"{DATALAKE}/single_gene_perturbations-v1.0.gmt"


def _iter_gmt_names(gmt_path):
    with open(gmt_path) as fh:
        for line in fh:
            parts = line.rstrip("\n").split("\t")
            if len(parts) >= 3 and parts[0].strip():
                yield parts[0].strip(), parts[2:]


def _base_name(name):
    return re.sub(r"[-_](up|dn|down)$", "", name, flags=re.IGNORECASE)


def list_disease_signatures(gmt_path=DISEASE_GMT):
    """Return the sorted unique list of disease base-names available."""
    bases = set()
    for name, _ in _iter_gmt_names(gmt_path):
        if re.search(r"[-_](up|dn|down)$", name, flags=re.IGNORECASE):
            bases.add(_base_name(name))
    return sorted(bases)


def match_disease(query, gmt_path=DISEASE_GMT, topn=10):
    """Fuzzy-match a disease name to available base-names. Returns [(name, score)] desc."""
    bases = list_disease_signatures(gmt_path)
    ql = query.lower().strip()
    scored = []
    for b in bases:
        bl = b.lower()
        # substring boost + difflib ratio
        sub = 1.0 if ql in bl or bl in ql else 0.0
        ratio = difflib.SequenceMatcher(None, ql, bl).ratio()
        scored.append((b, max(ratio, 0.6 * sub + 0.4 * ratio)))
    scored.sort(key=lambda x: x[1], reverse=True)
    return scored[:topn]


def load_builtin_disease(base_name, gmt_path=DISEASE_GMT):
    """Return (up_genes, dn_genes) for a built-in disease base-name (exact, case-insensitive)."""
    up, dn = None, None
    target = base_name.lower()
    for name, genes in _iter_gmt_names(gmt_path):
        b = _base_name(name).lower()
        if b != target:
            continue
        if re.search(r"[-_]up$", name, flags=re.IGNORECASE):
            up = [g.strip() for g in genes if g.strip()]
        elif re.search(r"[-_](dn|down)$", name, flags=re.IGNORECASE):
            dn = [g.strip() for g in genes if g.strip()]
    if up is None and dn is None:
        raise ValueError(f"Disease '{base_name}' not found in {gmt_path}")
    return up or [], dn or []


def signature_from_de_table(path, gene_col=None, lfc_col=None, padj_col=None,
                            padj_thresh=0.05, lfc_thresh=1.0, top_n=None):
    """Derive up/dn gene sets from a DE results table.

    Auto-detects columns if not given (common names: gene/symbol/gene_symbol/names;
    log2FoldChange/log2FC/logFC/avg_log2FC; padj/pvalue_adj/adj.P.Val/FDR/qvalue).
    up = padj<thresh & lfc>= lfc_thresh ; dn = padj<thresh & lfc<= -lfc_thresh.
    If top_n is set, take the top_n by |lfc| within each direction instead.
    """
    sep = "\t" if path.endswith((".tsv", ".txt")) else ","
    df = pd.read_csv(path, sep=sep)
    cols = {c.lower(): c for c in df.columns}

    def pick(cands, given):
        if given:
            return given
        for c in cands:
            if c in cols:
                return cols[c]
        return None

    gene_col = pick(["gene", "symbol", "gene_symbol", "genes", "names", "gene_name"], gene_col)
    lfc_col = pick(["log2foldchange", "log2fc", "logfc", "avg_log2fc", "lfc", "log2_fold_change"], lfc_col)
    padj_col = pick(["padj", "pvalue_adj", "adj.p.val", "fdr", "qvalue", "q_value", "p_adj", "adj_pval"], padj_col)
    if gene_col is None or lfc_col is None:
        raise ValueError(f"Could not auto-detect gene/lfc columns. Columns present: {list(df.columns)}")
    df = df.dropna(subset=[gene_col, lfc_col])
    if padj_col is not None:
        sig = df[df[padj_col] < padj_thresh]
    else:
        sig = df  # no padj column: rely on lfc threshold / top_n only
    up_df = sig[sig[lfc_col] >= lfc_thresh]
    dn_df = sig[sig[lfc_col] <= -lfc_thresh]
    if top_n:
        up = up_df.reindex(up_df[lfc_col].abs().sort_values(ascending=False).index).head(top_n)[gene_col].tolist()
        dn = dn_df.reindex(dn_df[lfc_col].abs().sort_values(ascending=False).index).head(top_n)[gene_col].tolist()
    else:
        up = up_df[gene_col].tolist()
        dn = dn_df[gene_col].tolist()
    return [str(g) for g in up], [str(g) for g in dn]


if __name__ == "__main__":
    import sys
    if len(sys.argv) > 1:
        print(f"Top matches for '{sys.argv[1]}':")
        for name, sc in match_disease(sys.argv[1]):
            print(f"  {sc:.2f}  {name}")
    else:
        names = list_disease_signatures()
        print(f"{len(names)} disease signatures available. First 20:")
        for n in names[:20]:
            print("  ", n)
