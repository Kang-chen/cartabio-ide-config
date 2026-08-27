#!/usr/bin/env python3
"""
Design second-generation CAR constructs (BBz + 28z) from a validated scFv.

Emits, per construct: annotated protein FASTA, human codon-optimized ORF FASTA,
CAR-ORF GenBank, full lentiviral cassette GenBank, and a domain-boundary table.

Default sequences are the FMC63 anti-CD19 constructs (scFv from PDB 7URV chain D).
Swap FMC63_VL/FMC63_VH (and, if needed, the flanking domains) to target another
antigen. All flanking domains are canonical human sequences (CD8a, 4-1BB, CD28,
CD3z) — see references/car_design.md for provenance.

Usage:
    python design_car.py --outdir /mnt/results/car_design
Requires: biopython
"""
import os, argparse
from Bio.Seq import Seq
from Bio.SeqRecord import SeqRecord
from Bio.SeqFeature import SeqFeature, FeatureLocation
from Bio import SeqIO

# ---------------------------------------------------------------- Domain catalog
FMC63_VL = ("DIQMTQTTSSLSASLGDRVTISCRASQDISKYLNWYQQKPDGTVKLLIYHTSRLHSGVPS"
            "RFSGSGSGTDYSLTISNLEQEDIATYFCQQGNTLPYTFGGGTKLEIT")            # 7URV chain D
FMC63_VH = ("EVKLQESGPGLVAPSQSLSVTCTVSGVSLPDYGVSWIRQPPRKGLEWLGVIWGSETTYYN"
            "SALKSRLTIIKDNSKSQVFLKMNSLQTDDTAIYYCAKHYYYGGSYAMDYWGQGTSVTVSS")
LINKER   = "GGGGSGGGGSGGGGS"                                              # (G4S)3
CD8A_SIGNAL = "MALPVTALLLPLALLLHAARP"                                      # human CD8A
CD8A_HINGE  = "TTTPAPRPPTPAPTIASQPLSLRPEACRPAAGGAVHTRGLDFACD"
CD8A_TM     = "IYIWAPLAGTCGVLLLSLVITLYC"
CD137_CYTO  = "KRGRKKLLYIFKQPFMRPVQTTQEEDGCSCRFPEEEEGGCEL"                 # 4-1BB / TNFRSF9
CD28_HINGE  = "IEVMYPPPYLDNEKSNGTIIHVKGKHLCPSPLFPGPSKP"                    # human CD28
CD28_TM     = "FWVLVVVGGVLACYSLLVTVAFIIFWV"
CD28_CYTO   = "RSKRSRLLHSDYMNMTPRRPGPTRKHYQPYAPPRDFAAYRS"
CD3Z_CYTO   = ("RVKFSRSADAPAYQQGQNQLYNELNLGRREEYDVLDKRRGRDPEMGGKPRRKNPQEGLYN"
               "ELQKDKMAEAYSEIGMKGERRRGKGHDGLYQGLSTATKDTYDALHMQALPPR")     # CD3z / CD247

# Cassette elements. Replace the promoter/WPRE placeholders with real sequences
# for cloning; they are annotated as GenBank features either way.
KOZAK = "GCCACC"
# Short EF-1a (EFS) core promoter and WPRE — provide real sequences if available.
EFS_PROMOTER = ("N" * 1179)   # placeholder length ~ typical EFS core; annotate as promoter
WPRE         = ("N" * 592)    # placeholder length ~ typical WPRE; annotate as regulatory

# Most-frequent human codon per amino acid (simple, robust codon optimization).
HUMAN_CODON = {
 'A':'GCC','R':'AGA','N':'AAC','D':'GAC','C':'TGC','Q':'CAG','E':'GAG','G':'GGC',
 'H':'CAC','I':'ATC','L':'CTG','K':'AAG','M':'ATG','F':'TTC','P':'CCC','S':'AGC',
 'T':'ACC','W':'TGG','Y':'TAC','V':'GTG','*':'TGA'}

def codon_optimize(protein):
    return "".join(HUMAN_CODON[aa] for aa in protein)

def build_construct(name, domains):
    """domains: list of (label, aa_seq). Returns dict with protein, orf, features."""
    protein = "".join(seq for _, seq in domains)
    # domain aa boundaries (1-based inclusive)
    bounds, pos = [], 1
    for label, seq in domains:
        bounds.append((label, pos, pos + len(seq) - 1, len(seq), seq))
        pos += len(seq)
    orf_nt = codon_optimize(protein) + HUMAN_CODON['*']   # + stop
    assert str(Seq(orf_nt).translate()).rstrip('*') == protein, "codon opt mismatch!"
    return {"name": name, "protein": protein, "orf": orf_nt, "bounds": bounds}

def orf_genbank(c):
    rec = SeqRecord(Seq(c["orf"]), id=c["name"], name=c["name"][:16],
                    description=f"{c['name']} CAR ORF (human codon-optimized)")
    rec.annotations["molecule_type"] = "DNA"
    rec.features.append(SeqFeature(FeatureLocation(0, len(c["orf"])), type="CDS",
                        qualifiers={"label": f"{c['name']} CAR",
                                    "translation": c["protein"]}))
    for label, s, e, _, _ in c["bounds"]:
        rec.features.append(SeqFeature(FeatureLocation((s-1)*3, e*3), type="misc_feature",
                            qualifiers={"label": label}))
    return rec

def cassette_genbank(c):
    seq = EFS_PROMOTER + KOZAK + c["orf"] + WPRE
    rec = SeqRecord(Seq(seq), id=c["name"] + "_cassette", name=(c["name"][:10] + "_cass"),
                    description=f"{c['name']} lentiviral transfer cassette (EFS-Kozak-CAR-WPRE)")
    rec.annotations["molecule_type"] = "DNA"
    p = 0
    rec.features.append(SeqFeature(FeatureLocation(p, p+len(EFS_PROMOTER)), type="promoter",
                        qualifiers={"label": "EFS (EF-1a short) promoter"})); p += len(EFS_PROMOTER)
    rec.features.append(SeqFeature(FeatureLocation(p, p+len(KOZAK)), type="misc_feature",
                        qualifiers={"label": "Kozak"})); p += len(KOZAK)
    orf_start = p
    rec.features.append(SeqFeature(FeatureLocation(orf_start, orf_start+len(c["orf"])), type="CDS",
                        qualifiers={"label": f"{c['name']} CAR", "translation": c["protein"]}))
    for label, s, e, _, _ in c["bounds"]:
        rec.features.append(SeqFeature(FeatureLocation(orf_start+(s-1)*3, orf_start+e*3),
                            type="misc_feature", qualifiers={"label": label}))
    p = orf_start + len(c["orf"])
    rec.features.append(SeqFeature(FeatureLocation(p, p+len(WPRE)), type="regulatory",
                        qualifiers={"label": "WPRE"}))
    return rec

def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--outdir", default="/mnt/results/car_design")
    args = ap.parse_args()
    os.makedirs(args.outdir, exist_ok=True)

    bbz = build_construct("FMC63-BBz", [
        ("CD8a signal peptide", CD8A_SIGNAL), ("FMC63 VL", FMC63_VL),
        ("(G4S)3 linker", LINKER), ("FMC63 VH", FMC63_VH),
        ("CD8a hinge", CD8A_HINGE), ("CD8a transmembrane", CD8A_TM),
        ("4-1BB costimulatory", CD137_CYTO), ("CD3z signaling", CD3Z_CYTO)])
    z28 = build_construct("FMC63-28z", [
        ("CD8a signal peptide", CD8A_SIGNAL), ("FMC63 VL", FMC63_VL),
        ("(G4S)3 linker", LINKER), ("FMC63 VH", FMC63_VH),
        ("CD28 hinge", CD28_HINGE), ("CD28 transmembrane", CD28_TM),
        ("CD28 costimulatory", CD28_CYTO), ("CD3z signaling", CD3Z_CYTO)])

    rows = ["construct,domain,aa_start,aa_end,length_aa,aa_sequence"]
    for c in (bbz, z28):
        base = os.path.join(args.outdir, c["name"])
        with open(base + "_protein.fasta", "w") as f:
            f.write(f">{c['name']}_CAR_protein len={len(c['protein'])}aa\n{c['protein']}\n")
        gc = 100*(c["orf"].count("G")+c["orf"].count("C"))/len(c["orf"])
        with open(base + "_ORF_codon_optimized.fasta", "w") as f:
            f.write(f">{c['name']}_CAR_ORF_codon_optimized len={len(c['orf'])}nt GC={gc:.1f}%\n{c['orf']}\n")
        SeqIO.write(orf_genbank(c),     base + "_CAR_ORF.gb", "genbank")
        SeqIO.write(cassette_genbank(c), base + "_lentiviral_cassette.gb", "genbank")
        for label, s, e, ln, seq in c["bounds"]:
            rows.append(f"{c['name']},{label},{s},{e},{ln},{seq}")
        print(f"{c['name']}: protein {len(c['protein'])} aa, ORF {len(c['orf'])} nt, GC {gc:.1f}%")
    with open(os.path.join(args.outdir, "car_domain_table.csv"), "w") as f:
        f.write("\n".join(rows) + "\n")
    print("Wrote deliverables to", args.outdir)

if __name__ == "__main__":
    main()
