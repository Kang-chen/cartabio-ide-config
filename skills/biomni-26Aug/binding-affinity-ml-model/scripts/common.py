"""
Shared config, RDKit helpers, and ChEMBL access utilities for the
ChEMBL QSAR / novel-scaffold discovery skill.

All scripts import from here so the drug-like definition, standardization,
scaffold computation, and fingerprint encoding are IDENTICAL across curation,
cross-validation, and library screening (this consistency is what prevents
silent train/test mismatches).

Tested with: rdkit 2025.03.x, numpy, pandas. Python 3.10+.
"""
import os, json, time, urllib.request, urllib.parse
import numpy as np
import pandas as pd

# ----------------------------------------------------------------------------
# CONFIG -- override any of these via a JSON config file or CLI (see scripts).
# ----------------------------------------------------------------------------
DEFAULTS = dict(
    # Target specification: EITHER a gene/protein symbol to auto-resolve, OR an
    # explicit list of ChEMBL target IDs (explicit IDs take precedence).
    target_symbol=None,               # e.g. "PCSK9", "EGFR"
    target_chembl_ids=None,           # e.g. ["CHEMBL2929", "CHEMBL4523996"]
    organism="Homo sapiens",          # used only when resolving a symbol

    # Bioactivity endpoints pooled into a single pAffinity label.
    affinity_types=["IC50", "Ki", "Kd", "EC50"],

    # Drug-like small-molecule filter.
    mw_max=650.0,                     # Da; excludes peptides/macrocycles
    require_carbon=True,
    exclude_metals=True,

    # Replicate handling.
    replicate_agg="median",           # aggregate multiple measurements/compound
    replicate_flag_log=1.0,           # flag compounds with >this log spread
    replicate_drop_log=2.0,           # drop compounds with >this log spread

    # Cross-validation.
    cv_repeats=3,
    cv_folds=5,
    run_gnn=True,                     # GNN + fingerprint benchmark
    min_compounds=100,                # data-reality gate (see curate_dataset.py)

    # Morgan fingerprint.
    fp_radius=2,
    fp_bits=2048,

    # External library for screening.
    library_max_phase=[4, 3, 2],      # approved + phase 3 + phase 2 small mols
    library_pages=4,                  # pages of 1000 per phase
    library_smiles_csv=None,          # OR supply your own CSV with a 'smiles' col

    # ---- Applicability domain: THREE-TIER confidence (NN Tanimoto to train) ----
    # A compound is only HIGH-confidence if it sits in the reliable similarity
    # window AND the prediction is in range AND model disagreement is low. Weakly
    # similar compounds (below the high floor) are BORDERLINE, and compounds too
    # far (extrapolation) or too near (trivial analog) are OUT-OF-DOMAIN. This
    # replaces the old single [0.25, 0.55] "confident" band, which mislabeled
    # weakly-similar (~0.26-0.43) compounds as confident.
    ad_high_tanimoto_min=0.40,        # high-confidence lower bound (reliable interpolation)
    ad_high_tanimoto_max=0.70,        # above this = trivial near-analog (not novel chemistry)
    ad_borderline_tanimoto_min=0.30,  # [borderline_min, high_min) = borderline/low-confidence
    ad_std_quantile=0.75,             # per-tree std must be <= this quantile for HIGH tier
    # Legacy keys (back-compat): if a config still sets ad_tanimoto_min/max they
    # are mapped onto the tiered scheme in load_config() below.
    ad_tanimoto_min=None,
    ad_tanimoto_max=None,

    # ---- Modeling framework selection (no silent substitution) ----
    # 'auto'        -> use native models (RF/GBM [+GNN]); no external framework.
    # 'deeppurpose' -> REQUIRE DeepPurpose as a first-class model; if it cannot be
    #                  imported/run, the caller must DISCLOSE prominently and fall
    #                  back to native models (never a silent swap).
    model_framework="auto",
    deeppurpose_drug_encoding="CNN",  # SMILES-CNN (CPU-feasible); 'Morgan' also ok
    deeppurpose_train_epoch=40,       # bounded for CPU tractability
    deeppurpose_LR=1e-3,
    deeppurpose_batch_size=128,

    # Output.
    outdir="/mnt/results/qsar_run",
)

METALS = {'Fe','Zn','Cu','Mn','Mg','Ca','Na','K','Pt','Au','Ag','Hg','Cd','Co',
          'Ni','Al','As','Se','Li','Ba','B','Si'}
UA = {'User-Agent': 'Mozilla/5.0'}
CHEMBL_API = 'https://www.ebi.ac.uk/chembl/api/data'


def load_config(path=None, **overrides):
    cfg = dict(DEFAULTS)
    if path and os.path.exists(path):
        with open(path) as fh:
            cfg.update(json.load(fh))
    cfg.update({k: v for k, v in overrides.items() if v is not None})
    # ---- Back-compat: map legacy single-band AD keys onto the tiered scheme ----
    # Old skill used ad_tanimoto_min / ad_tanimoto_max for a single "confident"
    # band. If a caller still supplies these, treat the old band as the BORDERLINE
    # floor .. high ceiling, and set the HIGH floor to the midpoint (so nothing
    # weakly-similar is silently called high-confidence). New configs should use
    # ad_high_tanimoto_min / ad_high_tanimoto_max / ad_borderline_tanimoto_min.
    if cfg.get('ad_tanimoto_min') is not None or cfg.get('ad_tanimoto_max') is not None:
        lo = cfg.get('ad_tanimoto_min', 0.30) or 0.30
        hi = cfg.get('ad_tanimoto_max', 0.70) or 0.70
        cfg['ad_borderline_tanimoto_min'] = lo
        cfg['ad_high_tanimoto_max'] = hi
        cfg['ad_high_tanimoto_min'] = round(lo + (hi - lo) / 2.0, 3)
        print(f"[common] NOTE: legacy AD keys detected; mapped to tiers "
              f"borderline>={lo}, high>={cfg['ad_high_tanimoto_min']}, "
              f"ceiling={hi}. Prefer ad_high_tanimoto_min/max + "
              f"ad_borderline_tanimoto_min.")
    os.makedirs(cfg['outdir'], exist_ok=True)
    os.makedirs(os.path.join(cfg['outdir'], 'data'), exist_ok=True)
    os.makedirs(os.path.join(cfg['outdir'], 'figures'), exist_ok=True)
    return cfg


def ad_tier(nn_tanimoto, pred_in_range, std_ok, cfg):
    """Assign a compound to an applicability-domain confidence tier.

    Returns one of: 'high', 'borderline', 'out_of_domain'.
      * high         : reliable interpolation window AND prediction in range AND
                       low model disagreement.
      * borderline   : weakly similar to training (below the high floor but at or
                       above the borderline floor) -> low-confidence, NOT confident.
      * out_of_domain: too far (extrapolation), too near (trivial analog), out of
                       prediction range, or high disagreement.
    """
    t = float(nn_tanimoto)
    if t > cfg['ad_high_tanimoto_max']:
        return 'out_of_domain'          # trivial near-analog, not novel chemistry
    if t < cfg['ad_borderline_tanimoto_min']:
        return 'out_of_domain'          # extrapolation into unseen space
    in_high_window = t >= cfg['ad_high_tanimoto_min']
    if in_high_window and pred_in_range and std_ok:
        return 'high'
    return 'borderline'


# ----------------------------------------------------------------------------
# ChEMBL HTTP
# ----------------------------------------------------------------------------
def _get_json(url, timeout=120, retries=3):
    last = None
    for _ in range(retries):
        try:
            req = urllib.request.Request(url, headers=UA)
            return json.loads(urllib.request.urlopen(req, timeout=timeout).read())
        except Exception as e:                                   # noqa: BLE001
            last = e
            time.sleep(1.0)
    raise last


def resolve_target(symbol, organism="Homo sapiens", verbose=True):
    """Resolve a gene/protein symbol to candidate ChEMBL target IDs.

    Returns a DataFrame (chembl_id, pref_name, target_type, organism,
    n_activities) sorted by activity count desc. The CALLER should confirm
    which target(s) to use -- do not blindly take the top hit, because PPI
    targets, mutants, and orthologs all show up here.
    """
    url = (f"{CHEMBL_API}/target/search?q={urllib.parse.quote(symbol)}"
           f"&format=json&limit=50")
    d = _get_json(url)
    rows = []
    for t in d.get('targets', []):
        if organism and t.get('organism') and t['organism'] != organism:
            continue
        rows.append(dict(chembl_id=t['target_chembl_id'],
                         pref_name=t.get('pref_name'),
                         target_type=t.get('target_type'),
                         organism=t.get('organism')))
    df = pd.DataFrame(rows).drop_duplicates('chembl_id')
    # annotate activity counts so the user can pick data-rich targets
    counts = []
    for cid in df['chembl_id']:
        try:
            c = _get_json(f"{CHEMBL_API}/activity?target_chembl_id={cid}"
                          f"&format=json&limit=1")['page_meta']['total_count']
        except Exception:                                        # noqa: BLE001
            c = -1
        counts.append(c)
        time.sleep(0.2)
    df['n_activities'] = counts
    df = df.sort_values('n_activities', ascending=False).reset_index(drop=True)
    if verbose:
        print(f"Candidate ChEMBL targets for '{symbol}' ({organism}):")
        for _, r in df.iterrows():
            print(f"  {r.chembl_id:16s} {str(r.pref_name)[:44]:44s} "
                  f"{str(r.target_type)[:22]:22s} n_act={r.n_activities}")
    return df


def fetch_activities(target_id, maxrec=20000, verbose=False):
    recs, offset, limit = [], 0, 1000
    while True:
        url = (f"{CHEMBL_API}/activity?target_chembl_id={target_id}"
               f"&format=json&limit={limit}&offset={offset}")
        d = _get_json(url)
        recs += d['activities']
        offset += limit
        if offset >= d['page_meta']['total_count'] or offset >= maxrec:
            break
        time.sleep(0.3)
    if verbose:
        print(f"  {target_id}: fetched {len(recs)} activity rows")
    return recs


def fetch_library_by_phase(max_phase, npages=4, limit=1000, verbose=False):
    """Pull small molecules at a given clinical max_phase from ChEMBL."""
    out, off = [], 0
    for _ in range(npages):
        url = (f"{CHEMBL_API}/molecule?max_phase={max_phase}"
               f"&molecule_type=Small molecule&format=json"
               f"&limit={limit}&offset={off}").replace(' ', '%20')
        try:
            d = _get_json(url)
        except Exception:                                        # noqa: BLE001
            break
        for m in d['molecules']:
            st = m.get('molecule_structures')
            if st and st.get('canonical_smiles'):
                out.append(dict(chembl_id=m['molecule_chembl_id'],
                                smi=st['canonical_smiles'],
                                max_phase=m.get('max_phase'),
                                pref_name=m.get('pref_name')))
        off += limit
        if off >= d['page_meta']['total_count']:
            break
        time.sleep(0.3)
    if verbose:
        print(f"  max_phase={max_phase}: {len(out)} small molecules")
    return out


# ----------------------------------------------------------------------------
# RDKit helpers (imported lazily so `resolve_target` works without a full env)
# ----------------------------------------------------------------------------
def _rd():
    from rdkit import Chem
    from rdkit.Chem import Descriptors, AllChem, DataStructs
    from rdkit.Chem.MolStandardize import rdMolStandardize
    from rdkit.Chem.Scaffolds import MurckoScaffold
    from rdkit import RDLogger
    RDLogger.DisableLog('rdApp.*')
    return Chem, Descriptors, AllChem, DataStructs, rdMolStandardize, MurckoScaffold


def standardize_mol(smi):
    Chem, _, _, _, rdMolStandardize, _ = _rd()
    m = Chem.MolFromSmiles(smi)
    if m is None:
        return None
    try:
        m = rdMolStandardize.LargestFragmentChooser().choose(m)
        m = rdMolStandardize.Uncharger().uncharge(m)
        Chem.SanitizeMol(m)
    except Exception:                                            # noqa: BLE001
        return None
    return m


def std_smiles_ik_mw(smi):
    Chem, Descriptors, *_ = _rd()
    m = standardize_mol(smi)
    if m is None:
        return (None, None, None)
    return (Chem.MolToSmiles(m), Chem.MolToInchiKey(m),
            round(Descriptors.MolWt(m), 1))


def is_druglike(smi, mw, mw_max=650.0, require_carbon=True, exclude_metals=True):
    Chem, *_ = _rd()
    if mw is None or mw > mw_max:
        return False
    m = Chem.MolFromSmiles(smi)
    if m is None:
        return False
    atoms = {a.GetSymbol() for a in m.GetAtoms()}
    if require_carbon and 'C' not in atoms:
        return False
    if exclude_metals and (atoms & METALS):
        return False
    return True


def murcko_scaffold(smi):
    Chem, _, _, _, _, MurckoScaffold = _rd()
    m = Chem.MolFromSmiles(smi)
    if m is None:
        return None
    try:
        return Chem.MolToSmiles(MurckoScaffold.GetScaffoldForMol(m))
    except Exception:                                            # noqa: BLE001
        return None


def assay_group(dominant_assay):
    return 'binding(Kd/Ki)' if dominant_assay in ('Kd', 'Ki') \
        else 'functional(IC50/EC50)'


def morgan_matrix(smiles_iter, radius=2, n_bits=2048):
    """ECFP-like bit matrix (n, n_bits) as int8, identical encoding everywhere."""
    Chem, _, AllChem, *_ = _rd()
    rows = []
    for s in smiles_iter:
        bv = AllChem.GetMorganFingerprintAsBitVect(
            Chem.MolFromSmiles(s), radius, nBits=n_bits)
        rows.append(np.frombuffer(bytes(bv.ToBitString(), 'ascii'), 'u1') - 48)
    return np.vstack(rows).astype(np.int8)


def morgan_bitvects(smiles_iter, radius=2, n_bits=2048):
    """List of RDKit ExplicitBitVects for Tanimoto similarity."""
    Chem, _, AllChem, *_ = _rd()
    return [AllChem.GetMorganFingerprintAsBitVect(
        Chem.MolFromSmiles(s), radius, nBits=n_bits) for s in smiles_iter]


def pAffinity_from_nM(value_nM):
    return -np.log10(np.asarray(value_nM, dtype=float) * 1e-9)
