"""
PLIP backend: use the Protein-Ligand Interaction Profiler (PLIP) as the primary,
peer-reviewed engine for interaction typing.

PLIP (Salentin et al., Nucleic Acids Res. 2015; Adasme et al., NAR 2021) protonates
the complex, then detects hydrogen bonds, hydrophobic contacts, salt bridges,
pi-stacking, pi-cation, halogen bonds, and water bridges using published geometric
criteria *including donor/acceptor angles* (which the skill's own distance-only
heuristics lacked). We run PLIP on the structure, pick the interaction set for the
target ligand copy, and translate every interaction into the skill's per-residue
schema with an explicit confidence tier and source label.

Design contract
---------------
`profile_with_plip(pdb_path, ligand_resname, chain=None, position=None)` returns
either:
  * None  -- PLIP unavailable or produced no site for the ligand (caller falls
             back to the hardened built-in geometry), or
  * dict  -- {
        "engine": "PLIP",
        "version": "<plip version>",
        "by_residue": { (chain, resseq): [ {type, confidence, source, detail}, ... ] },
        "records": { "hbond": [...], "halogen": [...], "salt_bridge": [...],
                     "pi_stacking": [...], "pi_cation": [...], "hydrophobic": [...],
                     "water_bridge": [...] },
    }

Confidence policy (honesty layer)
---------------------------------
PLIP already enforces angles, so most PLIP calls are "high". We still DOWNGRADE to
"tentative" the calls that are physically borderline or model-assumption dependent:
  * salt bridge with charge-center distance > 4.0 A  (long-range; and PLIP's ligand
    protonation of weakly basic amines, e.g. a morpholine N at pKa ~5-6, is an
    assumption rather than a certainty at physiological pH),
  * halogen bond whose donor (C-X...A) angle < 140 deg or acceptor angle < 90 deg,
  * pi-cation / pi-stacking with centroid distance beyond the "ideal" band.
These thresholds are documented in references/interaction_types.md.

This module never raises to the caller; all failures return None.
"""

# distance/angle thresholds used only to assign the confidence TIER (not to
# re-detect interactions -- detection is PLIP's job).
SALT_BRIDGE_HIGH_MAX_A = 4.0     # charged-group centroid distance for "high"
HALOGEN_HIGH_MIN_DON_ANGLE = 140.0
HALOGEN_HIGH_MIN_ACC_ANGLE = 90.0
PISTACK_HIGH_MAX_A = 5.0
PICATION_HIGH_MAX_A = 5.0


def _plip_version():
    try:
        from plip.basic import config
        return getattr(config, "__version__", "unknown")
    except Exception:  # noqa: BLE001
        return "unknown"


def plip_available():
    try:
        import plip  # noqa: F401
        from plip.structure.preparation import PDBComplex  # noqa: F401
        return True
    except Exception:  # noqa: BLE001
        return False


def _add(by_residue, chain, resseq, entry):
    by_residue.setdefault((chain, int(resseq)), []).append(entry)


def profile_with_plip(pdb_path, ligand_resname, chain=None, position=None):
    """Run PLIP and return typed per-residue interactions, or None on any failure."""
    if not plip_available():
        return None
    try:
        from plip.structure.preparation import PDBComplex
    except Exception:  # noqa: BLE001
        return None

    try:
        mol = PDBComplex()
        mol.load_pdb(pdb_path)
        # choose the ligand site matching resname (+ chain/position when given)
        target_key = None
        want = ligand_resname.strip().upper()
        for lig in mol.ligands:
            if lig.hetid.strip().upper() != want:
                continue
            if chain is not None and lig.chain != chain:
                continue
            if position is not None and int(lig.position) != int(position):
                continue
            target_key = ":".join(str(x) for x in (lig.hetid, lig.chain, lig.position))
            break
        if target_key is None:
            # fall back to the first copy of the requested ligand
            for lig in mol.ligands:
                if lig.hetid.strip().upper() == want:
                    target_key = ":".join(str(x) for x in (lig.hetid, lig.chain, lig.position))
                    break
        if target_key is None:
            return None

        mol.analyze()
        site = mol.interaction_sets.get(target_key)
        if site is None:
            # try any IRE-like key
            for k, v in mol.interaction_sets.items():
                if k.split(":")[0].upper() == want:
                    site = v
                    target_key = k
                    break
        if site is None:
            return None

        by_residue = {}
        records = {"hbond": [], "halogen": [], "salt_bridge": [], "pi_stacking": [],
                   "pi_cation": [], "hydrophobic": [], "water_bridge": []}

        # ---- hydrogen bonds (candidate; PLIP enforces an angle) ----
        for h in list(site.hbonds_ldon) + list(site.hbonds_pdon):
            rec = {"resnr": int(h.resnr), "restype": h.restype, "chain": h.reschain,
                   "dist_DA": round(float(h.distance_ad), 2), "angle": round(float(h.angle), 0),
                   "prot_is_donor": bool(h.protisdon)}
            records["hbond"].append(rec)
            _add(by_residue, h.reschain, h.resnr,
                 {"type": "H-bond", "confidence": "high", "source": "PLIP",
                  "detail": f"D-A {rec['dist_DA']} A, angle {int(rec['angle'])} deg"})

        # ---- halogen bonds (PLIP checks sigma-hole geometry) ----
        for x in site.halogen_bonds:
            don_ang = float(x.don_angle)
            acc_ang = float(x.acc_angle)
            conf = "high" if (don_ang >= HALOGEN_HIGH_MIN_DON_ANGLE and
                              acc_ang >= HALOGEN_HIGH_MIN_ACC_ANGLE) else "tentative"
            rec = {"resnr": int(x.resnr), "restype": x.restype, "chain": x.reschain,
                   "dist": round(float(x.distance), 2), "don_angle": round(don_ang, 0),
                   "acc_angle": round(acc_ang, 0), "halogen": x.donortype, "acceptor": x.acctype}
            records["halogen"].append(rec)
            _add(by_residue, x.reschain, x.resnr,
                 {"type": "halogen bond", "confidence": conf, "source": "PLIP",
                  "detail": f"{rec['dist']} A, C-X...A {int(rec['don_angle'])} deg"})

        # ---- salt bridges (charge-aware; PLIP protonates the ligand) ----
        for sb in list(site.saltbridge_lneg) + list(site.saltbridge_pneg):
            dist = float(sb.distance)
            conf = "high" if dist <= SALT_BRIDGE_HIGH_MAX_A else "tentative"
            rec = {"resnr": int(sb.resnr), "restype": sb.restype, "chain": sb.reschain,
                   "dist": round(dist, 2), "prot_is_pos": bool(getattr(sb, "protispos", False))}
            records["salt_bridge"].append(rec)
            _add(by_residue, sb.reschain, sb.resnr,
                 {"type": "salt bridge", "confidence": conf, "source": "PLIP",
                  "detail": f"{rec['dist']} A (charge-model dependent)" if conf == "tentative"
                            else f"{rec['dist']} A"})

        # ---- pi-stacking ----
        for ps in site.pistacking:
            dist = float(ps.centdist)
            conf = "high" if dist <= PISTACK_HIGH_MAX_A else "tentative"
            rec = {"resnr": int(ps.resnr), "restype": ps.restype, "chain": ps.reschain,
                   "centdist": round(dist, 2), "angle": round(float(ps.angle), 0),
                   "type_geom": ps.type}
            records["pi_stacking"].append(rec)
            _add(by_residue, ps.reschain, ps.resnr,
                 {"type": "pi-stacking", "confidence": conf, "source": "PLIP",
                  "detail": f"{rec['centdist']} A, {rec['type_geom']}"})

        # ---- pi-cation ----
        for pc in list(site.pication_laro) + list(site.pication_paro):
            dist = float(pc.distance)
            conf = "high" if dist <= PICATION_HIGH_MAX_A else "tentative"
            rec = {"resnr": int(pc.resnr), "restype": pc.restype, "chain": pc.reschain,
                   "dist": round(dist, 2)}
            records["pi_cation"].append(rec)
            _add(by_residue, pc.reschain, pc.resnr,
                 {"type": "pi-cation", "confidence": conf, "source": "PLIP",
                  "detail": f"{rec['dist']} A"})

        # ---- hydrophobic ----
        for hc in site.hydrophobic_contacts:
            rec = {"resnr": int(hc.resnr), "restype": hc.restype, "chain": hc.reschain,
                   "dist": round(float(hc.distance), 2)}
            records["hydrophobic"].append(rec)
            _add(by_residue, hc.reschain, hc.resnr,
                 {"type": "hydrophobic", "confidence": "high", "source": "PLIP",
                  "detail": f"{rec['dist']} A"})

        # ---- water bridges (reported, but NOT merged as a residue contact type) ----
        for wb in site.water_bridges:
            records["water_bridge"].append(
                {"resnr": int(wb.resnr), "restype": wb.restype, "chain": wb.reschain,
                 "dist_aw": round(float(wb.distance_aw), 2), "dist_dw": round(float(wb.distance_dw), 2)})

        return {"engine": "PLIP", "version": _plip_version(),
                "site_key": target_key, "by_residue": by_residue, "records": records}
    except Exception as e:  # noqa: BLE001
        print(f"[warn] PLIP profiling failed ({type(e).__name__}: {e}); falling back to geometry")
        return None


if __name__ == "__main__":
    import json
    import sys

    pdb = sys.argv[1] if len(sys.argv) > 1 else "/workspace/pocket_analysis_gefitinib/structures/4WKQ.pdb"
    lig = sys.argv[2] if len(sys.argv) > 2 else "IRE"
    out = profile_with_plip(pdb, lig)
    if out is None:
        print("PLIP returned None")
    else:
        print("engine:", out["engine"], "version:", out["version"], "site:", out["site_key"])
        print("records:", json.dumps({k: len(v) for k, v in out["records"].items()}))
        for (ch, rs), items in sorted(out["by_residue"].items(), key=lambda x: x[0][1]):
            tags = ", ".join(f"{i['type']}[{i['confidence']}]" for i in items)
            print(f"  {ch}{rs}: {tags}")
