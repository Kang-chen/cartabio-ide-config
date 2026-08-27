"""
Generate the 2D data figures for the binding-pocket report.

  F1  interaction diagram  : ligand at center, contacting residues arranged
                             radially, colored by interaction type, spoke length
                             ~ min contact distance, H-bonds drawn as dashed lines.
  F2  contact-distance chart: horizontal bar chart of per-residue minimum
                             heavy-atom distance, colored by interaction type,
                             with the 4.0 / 4.5 A shells marked.
  F3  fragment heatmap      : ligand fragment x pocket residue matrix of contact
                             counts, showing which chemical piece each residue
                             engages.

Data plots only (matplotlib). Conceptual/schematic diagrams are out of scope here.
Every figure is saved as PNG + SVG. Fonts follow the Phylo guidance.
"""

import os

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt  # noqa: E402
import numpy as np  # noqa: E402

matplotlib.rcParams["font.family"] = ["Liberation Sans", "Arimo", "DejaVu Sans"]
matplotlib.rcParams["svg.fonttype"] = "none"
matplotlib.rcParams["pdf.fonttype"] = 42

# Interaction-type -> color (Phylo-ish, colorblind-aware)
TYPE_COLORS = {
    "H-bond": "#0279EE",       # blue
    "salt bridge": "#E9134C",  # red
    "hydrophobic": "#8A8A8A",  # gray
    "pi-stacking": "#75A025",  # green
    "pi-cation": "#FF9400",    # orange
    "halogen bond": "#FD9BED", # pink
    "vdW": "#B8B2A7",          # muted
}


def _tag_types(contact):
    """Return the list of interaction TYPE strings for a contact, tolerating both
    the new dict-tag schema ({type,confidence,source}) and the old string schema."""
    out = []
    for t in contact.get("interaction_tags", ["vdW"]):
        out.append(t["type"] if isinstance(t, dict) else t)
    return out


def _primary_type(contact):
    """Pick a single representative interaction tag for coloring."""
    order = ["salt bridge", "H-bond", "pi-cation", "pi-stacking", "halogen bond",
             "hydrophobic", "vdW"]
    tags = _tag_types(contact)
    for t in order:
        if t in tags:
            return t
    return "vdW"


def _is_tentative(contact):
    """True if the contact's overall interaction call is tentative."""
    return contact.get("interaction_confidence", "high") == "tentative"


def _save(fig, out_base):
    os.makedirs(os.path.dirname(out_base), exist_ok=True)
    png, svg = out_base + ".png", out_base + ".svg"
    fig.savefig(png, dpi=200, bbox_inches="tight", facecolor="white")
    fig.savefig(svg, bbox_inches="tight", facecolor="white")
    plt.close(fig)
    print(f"[OK] wrote {png} and {svg}")
    return png, svg


def figure_interaction_diagram(contacts, ligand_label, out_base, max_res=24):
    """
    F1: radial interaction diagram.

    Residues are placed on evenly spaced spokes around the ligand so nodes never
    overlap regardless of count. Distance is NOT encoded as radius (which caused
    the closest residues to collapse onto the same short-radius arc and collide);
    instead nodes sit on one of two concentric rings chosen by distance band
    (inner ring = closest third of contacts) and the exact distance is printed
    beside each node. Node radius shrinks automatically as the residue count grows.
    """
    cs = sorted(contacts, key=lambda c: c["min_dist"])[:max_res]
    n = len(cs)
    if n == 0:
        raise ValueError("no contacts to plot")
    # If the pocket spans >1 chain (e.g. a homodimer interface site), residue
    # numbers repeat across chains, so append the chain id to disambiguate.
    multichain = len({c.get("chain", "") for c in contacts}) > 1

    # Start each spoke at the top and go clockwise so reading order is stable.
    angles = np.pi / 2 - np.linspace(0, 2 * np.pi, n, endpoint=False)

    # Two-ring banding to give closely-spaced residues more breathing room:
    # closest ~third on an inner ring, the rest on an outer ring. This keeps
    # angular neighbours from also being radial neighbours.
    r_inner, r_outer = 1.65, 2.55
    thresh = cs[max(0, n // 3 - 1)]["min_dist"] if n >= 6 else cs[0]["min_dist"]
    radii = [r_inner if c["min_dist"] <= thresh else r_outer for c in cs]

    # Nodes are compact colored dots; the residue label sits OUTSIDE the node
    # (radially further out) so label length is independent of node size and can
    # never be clipped by the circle. Distance is shown on a second line.
    node_s = 300.0
    node_r = 0.16

    fig, ax = plt.subplots(figsize=(9.6, 9.6))
    ax.set_aspect("equal")
    ax.axis("off")

    # center ligand node
    ax.scatter([0], [0], s=2600, color="#D4A04A", edgecolor="#111111",
               linewidth=1.5, zorder=5)
    ax.text(0, 0, ligand_label, ha="center", va="center", fontsize=12,
            fontweight="bold", color="#111111", zorder=6)

    used_types = set()
    any_tentative = False
    for ang, r, c in zip(angles, radii, cs):
        x, y = r * np.cos(ang), r * np.sin(ang)
        typ = _primary_type(c)
        used_types.add(typ)
        col = TYPE_COLORS.get(typ, "#B8B2A7")
        is_hb = bool(c["hbonds"])
        tentative = _is_tentative(c)
        any_tentative = any_tentative or tentative
        # spoke from just outside the ligand node to the residue node
        ax.plot([0.32 * np.cos(ang), (r - node_r) * np.cos(ang)],
                [0.32 * np.sin(ang), (r - node_r) * np.sin(ang)],
                color=col, lw=2.4 if is_hb else 1.3,
                linestyle="--" if is_hb else "-", zorder=2, alpha=0.9)
        # Tentative calls are drawn as HOLLOW, hatched nodes so they are visually
        # distinct from high-confidence (filled) nodes.
        if tentative:
            ax.scatter([x], [y], s=node_s, facecolor="white", edgecolor=col,
                       linewidth=2.0, hatch="////", zorder=3, alpha=0.97)
        else:
            ax.scatter([x], [y], s=node_s, color=col, edgecolor="#111111",
                       linewidth=0.8, zorder=3, alpha=0.97)
        # label placed just outside the node, radially; alignment follows the
        # spoke direction so text flows away from the centre and never overlaps
        # the node or the neighbouring spokes.
        lx, ly = (r + node_r + 0.06) * np.cos(ang), (r + node_r + 0.06) * np.sin(ang)
        ha = "left" if np.cos(ang) > 0.20 else ("right" if np.cos(ang) < -0.20 else "center")
        va = "bottom" if np.sin(ang) > 0.20 else ("top" if np.sin(ang) < -0.20 else "center")
        rlab = f"{c['resname']}{c['resseq']}" + (f"/{c['chain']}" if multichain and c.get("chain") else "")
        label = f"{rlab}\n{c['min_dist']:.1f}\u00c5"
        ax.text(lx, ly, label, ha=ha, va=va, fontsize=8.4,
                color="#111111", fontweight="bold", zorder=4,
                linespacing=0.95)

    # legend
    handles = [plt.Line2D([0], [0], marker="o", linestyle="", markersize=10,
                          markerfacecolor=TYPE_COLORS[t], markeredgecolor="#111111",
                          label=t) for t in TYPE_COLORS if t in used_types]
    handles.append(plt.Line2D([0], [0], color="#0279EE", lw=2.4, linestyle="--",
                              label="candidate H-bond"))
    if any_tentative:
        handles.append(plt.Line2D([0], [0], marker="o", linestyle="", markersize=10,
                                  markerfacecolor="white", markeredgecolor="#555555",
                                  markeredgewidth=2.0, label="tentative call"))
    ax.legend(handles=handles, loc="upper left", bbox_to_anchor=(-0.08, 1.04),
              frameon=False, fontsize=9)
    # expand limits so the outside-the-node labels are never clipped
    lim = r_outer + node_r + 1.15
    ax.set_xlim(-lim, lim)
    ax.set_ylim(-lim, lim)
    ax.set_title(f"Binding-pocket interaction diagram \u2014 {ligand_label}",
                 fontsize=13, fontweight="bold", color="#111111", pad=8)
    return _save(fig, out_base)


def figure_contact_distance(contacts, out_base, cut_core=4.0, cut_wide=4.5, max_res=28):
    """F2: horizontal bar chart of per-residue minimum contact distance."""
    cs = sorted(contacts, key=lambda c: c["min_dist"])[:max_res]
    labels = [f"{c['resname']}{c['resseq']}" + (f"/{c['chain']}" if c.get("chain") else "")
              for c in cs]
    dists = [c["min_dist"] for c in cs]
    colors = [TYPE_COLORS.get(_primary_type(c), "#B8B2A7") for c in cs]

    fig, ax = plt.subplots(figsize=(8.2, max(4.0, 0.32 * len(cs) + 1.2)))
    y = np.arange(len(cs))[::-1]
    # tentative calls get a hatch so they read as lower-confidence
    hatches = ["////" if _is_tentative(c) else "" for c in cs]
    bars = ax.barh(y, dists, color=colors, edgecolor="#111111", linewidth=0.4, height=0.72)
    any_tentative = False
    for bar, h in zip(bars, hatches):
        if h:
            bar.set_hatch(h)
            any_tentative = True
    ax.axvline(cut_core, color="#111111", lw=1.0, linestyle="--")
    ax.axvline(cut_wide, color="#8A8378", lw=1.0, linestyle=":")
    ax.text(cut_core, len(cs) - 0.3, f" {cut_core:.1f}\u00c5 core", fontsize=8, color="#111111")
    ax.text(cut_wide, len(cs) - 1.1, f" {cut_wide:.1f}\u00c5 wide", fontsize=8, color="#8A8378")
    ax.set_yticks(y)
    ax.set_yticklabels(labels, fontsize=8.5)
    ax.set_xlabel("Minimum heavy-atom distance to ligand (\u00c5)", fontsize=10)
    ax.set_xlim(0, max(cut_wide + 0.4, max(dists) + 0.2))
    for i, (yy, c) in enumerate(zip(y, cs)):
        if c["hbonds"]:
            ax.text(c["min_dist"] + 0.03, yy, "H", va="center", fontsize=8,
                    color="#0279EE", fontweight="bold")
    # legend for types present
    used = {_primary_type(c) for c in cs}
    handles = [plt.Rectangle((0, 0), 1, 1, color=TYPE_COLORS[t]) for t in TYPE_COLORS if t in used]
    labels_leg = [t for t in TYPE_COLORS if t in used]
    if any_tentative:
        handles.append(plt.Rectangle((0, 0), 1, 1, facecolor="white", edgecolor="#111111", hatch="////"))
        labels_leg.append("tentative")
    # place legend OUTSIDE the plot area (upper-right, just outside axes) so it
    # never overlaps the distance bars.
    ax.legend(handles, labels_leg, loc="upper left", bbox_to_anchor=(1.01, 1.0),
              frameon=False, fontsize=8.5, title="interaction")
    ax.set_title("Per-residue contact distance", fontsize=13, fontweight="bold",
                 color="#111111")
    ax.spines[["top", "right"]].set_visible(False)
    return _save(fig, out_base)


def figure_fragment_heatmap(structure, ligand_residue, contacts, fragment_map,
                            out_base, contacts_by_atom_fn, max_res=28, cut=4.5):
    """
    F3: fragment x residue contact-count heatmap.

    contacts_by_atom_fn: pass compute_contacts.contacts_by_ligand_atom to avoid a
    hard import cycle; it returns {atom_name: {n_contacts, residues:set}}.
    """
    # residue set (top by proximity)
    cs = sorted(contacts, key=lambda c: c["min_dist"])[:max_res]
    # Key on (resname, resseq, chain) so residues that share a number across
    # chains in a multi-chain pocket (e.g. dimer interface) are not merged.
    multichain = len({c.get("chain", "") for c in contacts}) > 1
    res_labels = [f"{c['resname']}{c['resseq']}" + (f"/{c['chain']}" if multichain and c.get("chain") else "")
                  for c in cs]
    res_key = {(c["resname"], c["resseq"], c.get("chain", "")): i for i, c in enumerate(cs)}

    # fragment ordering
    frags = []
    for a in ligand_residue.get_atoms():
        if a.element == "H":
            continue
        f = fragment_map.get(a.get_name(), "scaffold")
        if f not in frags:
            frags.append(f)
    frag_idx = {f: i for i, f in enumerate(frags)}

    # build matrix by counting atom-level contacts per fragment/residue
    import numpy as _np
    M = _np.zeros((len(frags), len(cs)))
    per_atom = contacts_by_atom_fn(structure, ligand_residue, cut=cut)
    # need residue attribution per atom: recompute distance to each pocket residue
    lig_atoms = [a for a in ligand_residue.get_atoms() if a.element != "H"]
    lig_xyz = _np.array([a.coord for a in lig_atoms])
    lig_names = [a.get_name() for a in lig_atoms]
    model = list(structure)[0]
    from Bio.PDB.Polypeptide import is_aa
    for chain in model:
        for res in chain:
            if res.id[0] != " " or not is_aa(res, standard=False):
                continue
            key = (res.resname.strip(), res.id[1], chain.id)
            if key not in res_key:
                continue
            ridx = res_key[key]
            p_atoms = [a for a in res.get_atoms() if a.element != "H"]
            p_xyz = _np.array([a.coord for a in p_atoms])
            d = _np.linalg.norm(lig_xyz[:, None, :] - p_xyz[None, :, :], axis=2)
            for li, an in enumerate(lig_names):
                hits = int((d[li] <= cut).sum())
                if hits:
                    frag = fragment_map.get(an, "scaffold")
                    M[frag_idx[frag], ridx] += hits

    fig, ax = plt.subplots(figsize=(max(6.5, 0.34 * len(cs) + 2.2),
                                    max(3.0, 0.5 * len(frags) + 1.5)))
    im = ax.imshow(M, aspect="auto", cmap="YlOrBr")
    ax.set_xticks(np.arange(len(cs)))
    ax.set_xticklabels(res_labels, rotation=90, fontsize=8)
    ax.set_yticks(np.arange(len(frags)))
    ax.set_yticklabels(frags, fontsize=9)
    ax.set_xlabel("Pocket residue", fontsize=10)
    ax.set_ylabel("Ligand fragment", fontsize=10)
    ax.set_title(f"Fragment\u2013residue contact map (\u2264{cut:.1f}\u00c5)",
                 fontsize=13, fontweight="bold", color="#111111")
    for i in range(len(frags)):
        for j in range(len(cs)):
            if M[i, j] > 0:
                ax.text(j, i, int(M[i, j]), ha="center", va="center", fontsize=7,
                        color="#111111" if M[i, j] < M.max() * 0.6 else "white")
    cbar = fig.colorbar(im, ax=ax, fraction=0.03, pad=0.02)
    cbar.set_label("atom-atom contacts", fontsize=9)
    return _save(fig, out_base)
