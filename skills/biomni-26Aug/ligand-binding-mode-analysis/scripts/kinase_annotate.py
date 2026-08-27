"""
Optional protein-kinase annotation layer.

If the target is a protein kinase, label pocket residues by their canonical
functional role (P-loop / glycine-rich loop, catalytic Lys of the beta3 strand,
alphaC-helix Glu, gatekeeper + hinge, catalytic loop / HRD, DFG motif). These
roles are what make a kinase ATP-pocket interpretable and are exactly the
annotations produced for the imatinib-ABL1 benchmark.

Detection is heuristic and conservative:
  - Sequence-motif scan for the HRD and DFG motifs and the glycine-rich
    GxGxxG P-loop on the chain that contacts the ligand.
  - If both HRD and DFG are found in a plausible arrangement, treat as a kinase
    and anchor the numbering to the DETECTED motif positions (robust to the
    structure's own residue numbering).

This means annotations do NOT rely on hard-coded ABL1 residue numbers; they are
derived from the motifs in whatever kinase is supplied.

See references/kinase_motifs.md.
"""

import re

import numpy as np
from Bio.PDB.Polypeptide import is_aa

try:
    from Bio.PDB.Polypeptide import three_to_one
except Exception:  # noqa: BLE001
    three_to_one = None

_AA3TO1 = {
    "ALA": "A", "ARG": "R", "ASN": "N", "ASP": "D", "CYS": "C", "GLN": "Q",
    "GLU": "E", "GLY": "G", "HIS": "H", "ILE": "I", "LEU": "L", "LYS": "K",
    "MET": "M", "PHE": "F", "PRO": "P", "SER": "S", "THR": "T", "TRP": "W",
    "TYR": "Y", "VAL": "V", "MSE": "M", "SEC": "U",
}


def _lys_nz(residue):
    """Return Lys side-chain Nz coordinate, or None."""
    if residue is not None and "NZ" in residue:
        return residue["NZ"].coord
    return None


def _glu_carboxylate(residue):
    """Return a representative Glu/Asp carboxylate coordinate, or None."""
    if residue is None:
        return None
    for a in ("OE1", "OE2", "CD", "OD1", "OD2", "CG"):
        if a in residue:
            return residue[a].coord
    return None


def _pick_ion_pair(chain, lys_resnums, glu_resnums, max_dist=5.0):
    """
    Among candidate beta3-Lys and alphaC-Glu residue numbers, return the
    (lys_resnum, glu_resnum) pair whose Lys-Nz <-> Glu-carboxylate distance is
    smallest, provided it is within `max_dist` (the conserved beta3-Lys/alphaC-Glu
    salt bridge, ~3-4 A in active kinases). Returns (None, None) if no pair is
    close enough. This structural signature disambiguates spurious VAIK/Glu
    sequence matches far more reliably than residue spacing alone.
    """
    resmap = {r.id[1]: r for r in chain if r.id[0] == " "}
    best = (None, None)
    best_d = max_dist
    for kn in lys_resnums:
        nz = _lys_nz(resmap.get(kn))
        if nz is None:
            continue
        for en in glu_resnums:
            cx = _glu_carboxylate(resmap.get(en))
            if cx is None:
                continue
            d = float(np.linalg.norm(nz - cx))
            if d < best_d:
                best_d = d
                best = (kn, en)
    return best


def _chain_sequence(chain):
    """Return (seq_string, resseq_list) for the ordered amino-acid residues."""
    seq = []
    nums = []
    for res in chain:
        if res.id[0] != " " or not is_aa(res, standard=False):
            continue
        rn = res.resname.strip().upper()
        one = _AA3TO1.get(rn, "X")
        seq.append(one)
        nums.append(res.id[1])
    return "".join(seq), nums


def detect_kinase(structure, contact_chain_id=None, model_index=0):
    """
    Detect a protein kinase and locate its key motifs on the given chain.

    Returns dict:
      {is_kinase: bool, chain: id, motifs: {...}, annotations: {resseq: role}}
    `annotations` maps residue numbers (as in the structure) to a role string.
    """
    model = list(structure)[model_index]
    chains = [c for c in model if (contact_chain_id is None or c.id == contact_chain_id)]
    best = {"is_kinase": False}

    for chain in chains:
        seq, nums = _chain_sequence(chain)
        if len(seq) < 150:
            continue

        # DFG motif: Asp-Phe-Gly, usually near the activation loop start
        dfg = [m.start() for m in re.finditer("DFG", seq)]
        # HRD catalytic motif (allow HRD or variants HxD in catalytic loop)
        hrd = [m.start() for m in re.finditer("H[RK]D", seq)]
        # glycine-rich P-loop GxGxxG
        ploop = [m.start() for m in re.finditer("G.G..G", seq)]
        # VAIK-like beta3 catalytic lysine motif (K after a beta strand, often AxK)
        vaik = [m.start() for m in re.finditer("[VAIL][A-Z]K", seq)]

        if not (dfg and hrd):
            continue
        # plausible order: P-loop < VAIK < HRD < DFG
        d_pos = dfg[-1]  # take the C-terminal-most DFG (activation loop)
        h_candidates = [h for h in hrd if h < d_pos]
        if not h_candidates:
            continue
        h_pos = h_candidates[-1]

        # --- plausibility gate (avoid false positives) -----------------------
        # The bare "contains DFG and HRD substrings" test is far too permissive:
        # a ~250-residue non-kinase (e.g. carbonic anhydrase) will contain a
        # chance Asp-Phe-Gly and a chance H[RK]D and pass. In real kinase domains
        # the catalytic-loop HRD sits a short, conserved distance N-terminal to
        # the activation-loop DFG (~15-35 aa; ABL1 = 20). Require that spacing,
        # and require at least one supporting N-lobe landmark (a Gly-rich P-loop,
        # or a beta3-Lys/alphaC-Glu ion pair within salt-bridge distance). Both
        # are hallmarks of a bona fide ATP pocket and are absent in look-alikes.
        HRD_DFG_MIN, HRD_DFG_MAX = 12, 45
        if not (HRD_DFG_MIN <= (d_pos - h_pos) <= HRD_DFG_MAX):
            continue
        has_ploop = bool([p for p in ploop if p < h_pos])
        # tentative beta3-Lys / alphaC-Glu ion pair as a second possible anchor.
        # The Lys of the VAIK motif is the 3rd residue of the match (offset +2).
        vaik_lys_pre = [v + 2 for v in vaik if v + 2 < h_pos]
        lys_nums_all = [nums[k] for k in vaik_lys_pre] if vaik_lys_pre else []
        glu_nums_all = [nums[i] for i in range(len(seq))
                        if seq[i] == "E" and i < h_pos]
        ion_pair = _pick_ion_pair(chain, lys_nums_all, glu_nums_all, max_dist=5.0) \
            if (lys_nums_all and glu_nums_all) else (None, None)
        has_ion_pair = ion_pair[0] is not None
        if not (has_ploop or has_ion_pair):
            continue

        annotations = {}
        motifs = {}

        # DFG: D, F, G
        motifs["DFG"] = [nums[d_pos], nums[d_pos + 1], nums[d_pos + 2]]
        for k, role in zip(range(3), ["DFG-Asp", "DFG-Phe", "DFG-Gly"]):
            annotations[nums[d_pos + k]] = f"DFG motif ({role})"

        # catalytic loop / HRD
        motifs["HRD"] = [nums[h_pos], nums[h_pos + 1], nums[h_pos + 2]]
        for k, role in zip(range(3), ["catalytic His", "catalytic Arg", "catalytic Asp"]):
            annotations[nums[h_pos + k]] = f"Catalytic loop ({role})"

        # gatekeeper + hinge: the gatekeeper is ~ the residue just N-terminal to the
        # hinge, which sits between the two lobes. Empirically it lies well before
        # the HRD. We approximate the hinge as the stretch ~ (HRD - 40 .. HRD - 44)
        # is unreliable; instead anchor from the beta3 Lys forward. Use a robust
        # relative offset from HRD is fragile, so we mark hinge via the conserved
        # "gatekeeper+3 = hinge" only if we can locate the beta3 Lys + alphaC Glu.

        # Anchor the N-lobe motifs on the P-loop. Structural order along the
        # sequence is: P-loop (GxGxxG) -> beta3 strand VAIK Lys (~10-25 aa later)
        # -> alphaC Glu (~8-30 aa after the Lys) -> gatekeeper/hinge -> HRD -> DFG.
        # The earlier "VAIK Lys closest to HRD" heuristic is wrong: spurious
        # [VAIL].K matches near the catalytic loop (e.g. an 'LEK' just before HRD)
        # would hijack the beta3 Lys and push alphaC Glu past the hinge. We instead
        # pick the P-loop that precedes the DFG and take the first plausible VAIK
        # Lys downstream of it.
        pstart = None
        if ploop:
            pl = [p for p in ploop if p < h_pos]  # P-loop is N-terminal to HRD/DFG
            pstart = pl[0] if pl else ploop[0]
            motifs["P_loop"] = [nums[pstart], nums[min(pstart + 5, len(nums) - 1)]]
            for k in range(6):
                if pstart + k < len(nums):
                    annotations.setdefault(nums[pstart + k], "P-loop (Gly-rich)")

        # beta3 catalytic Lys + alphaC Glu.
        # Primary strategy (robust): the beta3 Lys and alphaC Glu form a conserved
        # salt bridge (Lys-Nz <-> Glu-carboxylate, ~3-4 A). Enumerate VAIK-Lys
        # candidates (N-terminal to HRD) and Glu candidates in the N-lobe, then
        # pick the spatially closest ion pair. This cleanly rejects spurious
        # [VAIL].K / Glu sequence matches. Fall back to sequence spacing only if
        # no side-chain ion pair is found (e.g. mutated/again disordered Lys/Glu).
        vaik_lys = [v + 2 for v in vaik if v + 2 < h_pos]
        # Glu candidates: all Glu positions in the N-lobe (P-loop .. hinge region).
        glu_lo = (pstart if pstart is not None else 0)
        glu_positions = [i for i, aa in enumerate(seq) if aa == "E" and glu_lo <= i < h_pos]
        lys_resnums = [nums[k] for k in vaik_lys]
        glu_resnums = [nums[g] for g in glu_positions]

        k_num = e_num = None
        kp, ep = _pick_ion_pair(chain, lys_resnums, glu_resnums, max_dist=5.0)
        if kp is not None and ep is not None:
            k_num, e_num = kp, ep
        else:
            # sequence fallback: first VAIK Lys 8-40 aa after P-loop, then first
            # Glu 4-30 aa downstream (still N-terminal to the hinge/HRD).
            k_pos = None
            if vaik_lys:
                if pstart is not None:
                    downstream = [k for k in vaik_lys if pstart + 8 <= k <= pstart + 40]
                    k_pos = downstream[0] if downstream else min(vaik_lys)
                else:
                    k_pos = min(vaik_lys)
            if k_pos is not None:
                k_num = nums[k_pos]
                for off in range(4, 31):
                    j = k_pos + off
                    if j < len(seq) and j < h_pos and seq[j] == "E":
                        e_num = nums[j]
                        break

        if k_num is not None:
            motifs["catalytic_Lys"] = k_num
            annotations[k_num] = "Catalytic Lys (beta3)"
        if e_num is not None:
            motifs["alphaC_Glu"] = e_num
            annotations[e_num] = "alphaC-helix Glu"

        # Gatekeeper + hinge: gatekeeper is the residue directly before the hinge,
        # which lies between alphaC and the catalytic loop. A robust structural
        # proxy: the hinge backbone is ~3-6 residues; it is the segment whose
        # residues most consistently H-bond the adenine. We cannot know that from
        # sequence alone, so we annotate gatekeeper/hinge by their standard
        # position relative to the alphaC Glu when available (Glu + ~30..40 is the
        # gatekeeper region in many kinases). To avoid overclaiming, we ONLY tag
        # gatekeeper/hinge if contact data later confirms proximity — see
        # annotate_contacts(), which refines these using actual ligand contacts.

        best = {
            "is_kinase": True,
            "chain": chain.id,
            "motifs": motifs,
            "annotations": annotations,
            "seq_len": len(seq),
        }
        break

    return best


def annotate_contacts(contacts, kinase_info):
    """
    Add a 'kinase_region' key to each contact using detected motif positions.

    Also refines the hinge/gatekeeper assignment using the ACTUAL contacts:
    the residues immediately N-terminal to the catalytic loop that contact the
    ligand and lie between the alphaC-Glu and the catalytic loop are labelled as
    the hinge; the hydrophobic residue just before the first hinge residue is the
    gatekeeper. This keeps the annotation grounded in geometry rather than guessed
    numbering.
    """
    if not kinase_info.get("is_kinase"):
        for c in contacts:
            c["kinase_region"] = ""
        return contacts

    ann = dict(kinase_info["annotations"])
    motifs = kinase_info["motifs"]
    chain = kinase_info["chain"]

    # First pass: apply direct motif annotations
    for c in contacts:
        c["kinase_region"] = ann.get(c["resseq"], "")

    # Refine hinge/gatekeeper from contacts between alphaC-Glu and catalytic loop
    aC = motifs.get("alphaC_Glu")
    hrd0 = motifs.get("HRD", [None])[0]
    if aC and hrd0:
        lo, hi = aC + 1, hrd0 - 1
        region_contacts = [c for c in contacts
                           if c["chain"] == chain and lo <= c["resseq"] < hi and not c["kinase_region"]]
        region_contacts.sort(key=lambda c: c["resseq"])
        if region_contacts:
            # Residues that H-bond the ligand in this stretch define the hinge
            # segment (the inhibitor typically donates/accepts to the hinge
            # backbone). If none H-bond, fall back to the closest contacts.
            hb_hinge = [c for c in region_contacts if c["hbonds"]]
            hinge = hb_hinge if hb_hinge else sorted(
                region_contacts, key=lambda c: c["min_dist"])[:3]
            hinge_nums = sorted(c["resseq"] for c in hinge)

            # Gatekeeper: the residue that controls back-pocket access sits at the
            # N-terminal edge of the hinge. Prefer the residue just before the
            # first hinge residue when it also contacts the ligand (Type II / deep
            # pocket binders touch it, e.g. Thr315 in ABL1). Otherwise, if the
            # H-bonding hinge spans >=2 residues, the most N-terminal one acts as
            # the gatekeeper and the remainder are the hinge proper.
            gk_num = None
            first_hinge = min(hinge_nums)
            gk_candidate = first_hinge - 1
            gk_contact = next((c for c in contacts if c["chain"] == chain
                               and c["resseq"] == gk_candidate and not c["kinase_region"]),
                              None)
            if gk_contact is not None:
                gk_num = gk_candidate
            elif len(hinge_nums) >= 2:
                gk_num = first_hinge  # promote leading hinge residue to gatekeeper

            for c in region_contacts:
                if c["resseq"] in hinge_nums and c["resseq"] != gk_num:
                    c["kinase_region"] = "Hinge"
            if gk_num is not None:
                for c in contacts:
                    if c["chain"] == chain and c["resseq"] == gk_num:
                        c["kinase_region"] = "Gatekeeper"
    return contacts


def kinase_summary(kinase_info):
    """Human-readable one-liner about the detected kinase motifs."""
    if not kinase_info.get("is_kinase"):
        return "Target does not appear to be a protein kinase (no HRD+DFG motif found)."
    m = kinase_info["motifs"]
    parts = []
    if "DFG" in m:
        parts.append(f"DFG at {m['DFG'][0]}-{m['DFG'][2]}")
    if "HRD" in m:
        parts.append(f"catalytic loop (HRD) at {m['HRD'][0]}")
    if "catalytic_Lys" in m:
        parts.append(f"beta3 Lys{m['catalytic_Lys']}")
    if "alphaC_Glu" in m:
        parts.append(f"alphaC Glu{m['alphaC_Glu']}")
    if "P_loop" in m:
        parts.append(f"P-loop {m['P_loop'][0]}-{m['P_loop'][1]}")
    return "Protein kinase detected: " + "; ".join(parts) + "."
