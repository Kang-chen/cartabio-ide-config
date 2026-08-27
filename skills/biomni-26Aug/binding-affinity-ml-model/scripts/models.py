"""
Molecular graph featurization + MPNN model + leakage-free scaffold splitting.

These are the exact featurization dims and the leakage-free GNN trainer used in
validation. The single most important correctness property here is that the
GNN's early-stopping validation set is carved from the TRAINING indices only --
the test fold is used solely for the final prediction. Getting this wrong makes
scaffold-split metrics optimistically biased and the GNN-vs-baseline comparison
unfair (a real trap: an earlier version selected epochs on the test fold and
made the GNN look better than it was).

Requires: torch, torch_geometric, rdkit, scikit-learn.
"""
import copy
from collections import defaultdict
import numpy as np

import torch
import torch.nn as nn
import torch.nn.functional as F
from torch_geometric.data import Data
from torch_geometric.nn import NNConv, global_add_pool
from torch_geometric.loader import DataLoader
from sklearn.model_selection import train_test_split as _tts

from rdkit import Chem
from rdkit import RDLogger
RDLogger.DisableLog('rdApp.*')

# ---------------- Atom / bond featurization (39 / 7 dims) ----------------
ATOM_LIST = ['C', 'N', 'O', 'S', 'F', 'Cl', 'Br', 'I', 'P', 'B', 'Si', 'Se', 'H']
HYBRID = [Chem.rdchem.HybridizationType.SP, Chem.rdchem.HybridizationType.SP2,
          Chem.rdchem.HybridizationType.SP3, Chem.rdchem.HybridizationType.SP3D,
          Chem.rdchem.HybridizationType.SP3D2]
BOND_TYPES = [Chem.rdchem.BondType.SINGLE, Chem.rdchem.BondType.DOUBLE,
              Chem.rdchem.BondType.TRIPLE, Chem.rdchem.BondType.AROMATIC]
ATOM_FDIM = 39
BOND_FDIM = 7


def _onehot(x, choices):
    v = [int(x == c) for c in choices]
    v.append(int(x not in choices))
    return v


def atom_features(a):
    f = _onehot(a.GetSymbol(), ATOM_LIST)          # 14
    f += _onehot(a.GetDegree(), [0, 1, 2, 3, 4, 5])  # 7
    f += _onehot(a.GetFormalCharge(), [-1, 0, 1])  # 4
    f += _onehot(a.GetTotalNumHs(), [0, 1, 2, 3, 4])  # 6
    f += _onehot(a.GetHybridization(), HYBRID)     # 6
    f += [int(a.GetIsAromatic()), int(a.IsInRing())]  # 2
    return f


def bond_features(b):
    f = _onehot(b.GetBondType(), BOND_TYPES)       # 5
    f += [int(b.GetIsConjugated()), int(b.IsInRing())]  # 2
    return f


def mol_to_graph(smi, y=None):
    m = Chem.MolFromSmiles(smi)
    if m is None:
        return None
    x = torch.tensor([atom_features(a) for a in m.GetAtoms()], dtype=torch.float)
    ei, ea = [], []
    for b in m.GetBonds():
        i, j = b.GetBeginAtomIdx(), b.GetEndAtomIdx()
        bf = bond_features(b)
        ei += [[i, j], [j, i]]
        ea += [bf, bf]                              # undirected -> both dirs
    if len(ei) == 0:                                # single-atom molecule
        ei = [[0, 0]]
        ea = [[0] * BOND_FDIM]
    data = Data(x=x,
                edge_index=torch.tensor(ei, dtype=torch.long).t().contiguous(),
                edge_attr=torch.tensor(ea, dtype=torch.float))
    if y is not None:
        data.y = torch.tensor([[y]], dtype=torch.float)
    return data


class MPNN(nn.Module):
    """Edge-conditioned message-passing network (NNConv)."""
    def __init__(self, node_dim=ATOM_FDIM, edge_dim=BOND_FDIM,
                 hidden=64, n_layers=3, dropout=0.2):
        super().__init__()
        self.lin0 = nn.Linear(node_dim, hidden)
        self.convs = nn.ModuleList()
        self.dropout = dropout
        for _ in range(n_layers):
            enn = nn.Sequential(nn.Linear(edge_dim, 32), nn.ReLU(),
                                nn.Linear(32, hidden * hidden))
            self.convs.append(NNConv(hidden, hidden, enn, aggr='mean'))
        self.bns = nn.ModuleList([nn.BatchNorm1d(hidden) for _ in range(n_layers)])
        self.head = nn.Sequential(nn.Linear(hidden, hidden), nn.ReLU(),
                                  nn.Dropout(dropout), nn.Linear(hidden, 1))

    def forward(self, data):
        x = F.relu(self.lin0(data.x))
        for conv, bn in zip(self.convs, self.bns):
            x = F.relu(bn(conv(x, data.edge_index, data.edge_attr)))
            x = F.dropout(x, p=self.dropout, training=self.training)
        return self.head(global_add_pool(x, data.batch))


# ---------------- Scaffold folds (disjoint scaffold groups) ----------------
def scaffold_folds(scaffolds, n_splits=5, seed=0):
    """Assign each Bemis-Murcko scaffold GROUP wholly to one fold, largest
    groups first, each to the currently-smallest fold. Guarantees zero scaffold
    overlap between folds -> honest test of generalization to new chemistry."""
    rng = np.random.RandomState(seed)
    groups = defaultdict(list)
    for i, s in enumerate(scaffolds):
        groups[s].append(i)
    gl = sorted(groups.values(), key=lambda x: (-len(x), rng.rand()))
    folds = [[] for _ in range(n_splits)]
    sizes = [0] * n_splits
    for g in gl:
        k = int(np.argmin(sizes))
        folds[k] += g
        sizes[k] += len(g)
    return folds


def random_folds(n, n_splits=5, seed=0):
    rng = np.random.RandomState(seed)
    perm = rng.permutation(n)
    return [perm[i::n_splits].tolist() for i in range(n_splits)]


# ---------------- Leakage-free GNN trainer ----------------
def train_gnn_fixed(train_idx, test_idx, X_smiles, y,
                    max_epochs=200, patience=25, lr=1e-3, seed=0,
                    n_threads=8):
    """Train MPNN with early stopping on an inner validation split of the
    TRAIN indices only. `test_idx` is used solely for the final prediction and
    NEVER for model selection or target scaling. Returns predictions on test."""
    torch.set_num_threads(n_threads)
    tr_in, va_in = _tts(train_idx, test_size=0.15, random_state=seed, shuffle=True)
    ymean, ystd = y[tr_in].mean(), y[tr_in].std()
    if ystd == 0:
        ystd = 1.0

    def mk(idx):
        return [mol_to_graph(X_smiles[i], (y[i] - ymean) / ystd) for i in idx]

    trl = DataLoader(mk(tr_in), batch_size=32, shuffle=True)
    val = mk(va_in)
    torch.manual_seed(seed)
    model = MPNN()
    opt = torch.optim.Adam(model.parameters(), lr=lr, weight_decay=1e-4)
    best, best_state, wait = np.inf, None, 0
    for _ in range(max_epochs):
        model.train()
        for b in trl:
            opt.zero_grad()
            loss = F.mse_loss(model(b), b.y)
            loss.backward()
            opt.step()
        model.eval()
        with torch.no_grad():
            vp = torch.cat([model(b) for b in DataLoader(val, batch_size=64)])
            vt = torch.cat([b.y for b in DataLoader(val, batch_size=64)])
        vl = F.mse_loss(vp, vt).item()
        if vl < best - 1e-4:
            best, best_state, wait = vl, copy.deepcopy(model.state_dict()), 0
        else:
            wait += 1
            if wait >= patience:
                break
    model.load_state_dict(best_state)
    model.eval()
    tdata = [mol_to_graph(X_smiles[i]) for i in test_idx]
    with torch.no_grad():
        preds = np.concatenate([model(b).numpy().ravel()
                                for b in DataLoader(tdata, batch_size=64)])
    return preds * ystd + ymean


def rf_predict_with_std(model, X):
    """Mean prediction and per-tree standard deviation (uncertainty proxy)."""
    allp = np.stack([t.predict(X) for t in model.estimators_])
    return allp.mean(0), allp.std(0)
