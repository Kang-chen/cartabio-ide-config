"""
RDKit-native graph Genetic Algorithm for goal-directed molecule generation.
Jensen-style (Jensen, Chem. Sci. 2019): BRICS-based fragment crossover +
atom/bond/ring mutation operators. No external fragment DB required.

Target-agnostic: the fitness function is supplied by the caller as a batch
callable `fitness_fn(list[str]) -> list[float]` (build it with scoring.py). The
GA never hard-codes any objective. Invalid/unscorable SMILES receive fitness 0.
Guardrails keep the search in drug-like space (heavy-atom count 8-50; atoms are
only removed from molecules with >= 9 atoms and low-degree atoms).
"""
import random
import numpy as np
from rdkit import Chem
from rdkit.Chem import BRICS, AllChem
from rdkit import RDLogger
RDLogger.DisableLog('rdApp.*')

# ---- Mutation building blocks (common medicinal-chemistry atoms/decorations) ----
_ATOMS = ['C', 'N', 'O', 'F', 'S', 'Cl']
_BOND_ORDER = [Chem.BondType.SINGLE, Chem.BondType.DOUBLE]


def _sanitize(mol):
    if mol is None:
        return None
    try:
        smi = Chem.MolToSmiles(mol)
        # reject disconnected/multi-component structures (salts, BRICS fragments)
        if "." in smi:
            return None
        m = Chem.MolFromSmiles(smi)
        if m is None:
            return None
        # basic drug-like guardrails to keep GA in reasonable space
        na = m.GetNumHeavyAtoms()
        if na < 8 or na > 50:
            return None
        return m
    except Exception:
        return None


def _canon(mol):
    try:
        return Chem.MolToSmiles(mol)
    except Exception:
        return None


# ---------------- Crossover (BRICS fragment recombination) ----------------
def crossover(parent_a, parent_b, max_tries=10):
    """Recombine two molecules via BRICS decomposition + rebuild."""
    for _ in range(max_tries):
        try:
            fa = list(BRICS.BRICSDecompose(parent_a, returnMols=True))
            fb = list(BRICS.BRICSDecompose(parent_b, returnMols=True))
            if not fa or not fb:
                return None
            frags = random.sample(fa, min(len(fa), random.randint(1, 2))) + \
                    random.sample(fb, min(len(fb), random.randint(1, 2)))
            builder = BRICS.BRICSBuild(frags, scrambleReagents=True, maxDepth=1)
            for prod in builder:
                m = _sanitize(prod)
                if m is not None:
                    return m
        except Exception:
            continue
    return None


# ---------------- Mutations ----------------
def _mut_add_atom(rw):
    if rw.GetNumAtoms() == 0:
        return None
    idx = random.randrange(rw.GetNumAtoms())
    a = rw.GetAtomWithIdx(idx)
    if a.GetImplicitValence() < 1:
        return None
    new_idx = rw.AddAtom(Chem.Atom(random.choice(_ATOMS)))
    rw.AddBond(idx, new_idx, Chem.BondType.SINGLE)
    return rw


def _mut_change_atom(rw):
    if rw.GetNumAtoms() == 0:
        return None
    idx = random.randrange(rw.GetNumAtoms())
    a = rw.GetAtomWithIdx(idx)
    if a.GetIsAromatic():
        return None
    a.SetAtomicNum(Chem.Atom(random.choice(_ATOMS)).GetAtomicNum())
    return rw


def _mut_add_bond(rw):
    n = rw.GetNumAtoms()
    if n < 2:
        return None
    i, j = random.sample(range(n), 2)
    if rw.GetBondBetweenAtoms(i, j) is not None:
        return None
    ai, aj = rw.GetAtomWithIdx(i), rw.GetAtomWithIdx(j)
    if ai.GetImplicitValence() < 1 or aj.GetImplicitValence() < 1:
        return None
    rw.AddBond(i, j, Chem.BondType.SINGLE)
    return rw


def _mut_remove_atom(rw):
    n = rw.GetNumAtoms()
    if n < 9:
        return None
    idx = random.randrange(n)
    if rw.GetAtomWithIdx(idx).GetDegree() > 2:
        return None
    rw.RemoveAtom(idx)
    return rw


_MUTATORS = [_mut_add_atom, _mut_change_atom, _mut_add_bond, _mut_remove_atom]


def mutate(mol, max_tries=10):
    for _ in range(max_tries):
        rw = Chem.RWMol(mol)
        op = random.choice(_MUTATORS)
        try:
            res = op(rw)
            if res is None:
                continue
            m = _sanitize(res.GetMol())
            if m is not None and _canon(m) != _canon(mol):
                return m
        except Exception:
            continue
    return None


# ---------------- GA driver ----------------
def run_ga(seed_smiles, fitness_fn, pop_size=100, n_generations=20,
           mutation_rate=0.5, elite_frac=0.1, tournament_k=3, seed=42,
           progress_cb=None):
    """
    seed_smiles : list[str] initial molecules
    fitness_fn  : callable(list[str]) -> list[float]  (batch scoring)
    Returns: (history dict, all_scored dict{smiles: fitness})
    """
    random.seed(seed)
    np.random.seed(seed)

    # init population from seeds (mutate to fill)
    pop = []
    seeds = [Chem.MolFromSmiles(s) for s in seed_smiles]
    seeds = [m for m in seeds if m is not None]
    while len(pop) < pop_size:
        base = random.choice(seeds)
        if len(pop) < len(seeds):
            pop.append(seeds[len(pop)])
        else:
            m = mutate(base) or crossover(random.choice(seeds), random.choice(seeds))
            if m is not None:
                pop.append(m)

    all_scored = {}          # canonical smiles -> fitness (master record)
    all_gen_first = {}       # canonical smiles -> generation first seen
    history = {'gen': [], 'best': [], 'mean': [], 'n_unique': []}

    def score_pop(mols, gen):
        smis = [_canon(m) for m in mols]
        need = [s for s in set(smis) if s is not None and s not in all_scored]
        if need:
            vals = fitness_fn(need)
            for s, v in zip(need, vals):
                all_scored[s] = float(v)
                all_gen_first[s] = gen
        return np.array([all_scored.get(s, 0.0) for s in smis])

    def tournament(mols, fits):
        idx = np.random.choice(len(mols), tournament_k, replace=False)
        return mols[idx[np.argmax(fits[idx])]]

    fits = score_pop(pop, 0)
    for gen in range(1, n_generations + 1):
        order = np.argsort(fits)[::-1]
        pop = [pop[i] for i in order]
        fits = fits[order]

        n_elite = max(1, int(elite_frac * pop_size))
        new_pop = pop[:n_elite]           # elitism

        pop_arr = np.array(pop, dtype=object)
        while len(new_pop) < pop_size:
            pa = tournament(pop_arr, fits)
            child = None
            if random.random() < 0.5:
                pb = tournament(pop_arr, fits)
                child = crossover(pa, pb)
            if child is None:
                child = mutate(pa)
            if child is None:
                child = pa
            if random.random() < mutation_rate:
                child = mutate(child) or child
            new_pop.append(child)

        pop = new_pop
        fits = score_pop(pop, gen)

        history['gen'].append(gen)
        history['best'].append(float(np.max(fits)))
        history['mean'].append(float(np.mean(fits)))
        history['n_unique'].append(len(all_scored))
        if progress_cb:
            progress_cb(gen, float(np.max(fits)), float(np.mean(fits)), len(all_scored))

    return history, all_scored, all_gen_first
