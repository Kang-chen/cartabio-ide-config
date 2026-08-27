# Environment & provisioning

Exact package set validated for this skill (Biomni sandbox). RDKit ships with the
Biomni image; the three generative-chemistry packages are installed on demand.

## Packages
| Package | Version | Role | Network at run time? |
|---|---|---|---|
| rdkit | 2023.09.6 | mols, QED, FilterCatalog (PAINS/BRENK), BRICS, drawing | no |
| sascorer (RDKit contrib) | bundled | Tier-1 synthesizability (SA_Score) | no |
| PyTDC (`tdc`) | latest | pretrained activity oracles (DRD2, GSK3B, ...) | small one-time oracle download (~35 MB) |
| crem | 0.2.17 | (optional) CReM mutation operator alternative | fragment DB download (may 403) |
| aizynthfinder | 4.4.1 | Tier-2 CASP retrosynthesis | models provisioned at setup (~750 MB) |
| reportlab, pypdf | bundled | PDF report + validation | no |
| scikit-learn | bundled | QSAR backend (RandomForest) | no |
| autodock vina + autosite (CLI) | bundled | optional docking backend | no |

## Install (once per environment)
```bash
uv pip install PyTDC aizynthfinder==4.4.1 crem
```
RDKit 2023.09.6 is already present. Installing the above is known to pin
`rdkit==2023.09.6` and `crem==0.2.17`; all required RDKit components
(QED, FilterCatalog with 585 PAINS+BRENK entries, BRICS, sascorer) are verified
to work at that version (haloperidol QED ≈ 0.759, SA ≈ 2.12).

## Provision retrosynthesis models AT SETUP (not lazily)
The USPTO expansion/filter/ringbreaker models + ZINC stock (~750 MB) must be
downloaded **before** the first interactive run, into a **persistent** cache so it
survives machine hibernation/restart:
```python
from run_retro import provision_models
provision_models(cache_dir="/mnt/shared-workspace/aizynth_models")
```
or equivalently the bundled CLI:
```bash
download_public_data /mnt/shared-workspace/aizynth_models
```
`provision_models` also normalizes the config paths so the cache is portable if
later copied/moved.

### Why setup-time, not lazy
A lazy 750 MB fetch inside a user's first request is the classic failure mode in
locked-down sandboxes: if egress is blocked (e.g. a GitHub 403), the download
hangs or errors mid-request. The skill therefore **never downloads inside a run**.
If the models are absent or egress is blocked, `run_retrosynthesis()` returns a
clean "skipped" result, the pipeline continues on the Tier-1 SA_Score proxy, and
the report states that retrosynthesis was unavailable — it never crashes.

## TDC oracle coverage
`tdc.Oracle(name=...)` provides pretrained oracles for a limited set of targets
(DRD2, GSK3B, JNK3, and property oracles like QED, LogP, SA, etc.). For any target
without a TDC oracle, use the **QSAR backend** (train on your own actives/inactives)
or the **docking backend** — the rest of the pipeline is identical.
