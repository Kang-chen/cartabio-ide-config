# Operational commands and run gates

Read this reference when executing the skill. Replace every angle-bracket placeholder with a
resolved absolute path or de-identified value, then record the exact command in the run journal.

## Contents

1. Resolve the skill and run directories
2. Freeze configuration and initialize state
3. Phase 0 — intake
4. Phase 1 — preflight
5. Phase 2 — prioritization
6. Phase 3 — validation
7. Phase 4 — visualization and benchmark context
8. Phase 5 — reporting
9. Phase 6 — final QA and handoff
10. Built-in Pt22 demo
11. Failure lookup

## 1. Resolve the skill and run directories

Locate the installed skill root once. Prefer the user installation, then the system installation,
or use the path Biomni supplied. Every command below runs from that root.

Use a de-identified ID such as `case-7f3a`; do not use a patient name or MRN.

```text
SKILL_DIR=<absolute-skill-root>
RUN_DIR=/mnt/shared-workspace/neoantigen-prioritization/<run-id>
RESULTS_DIR=/mnt/results/neoantigen-prioritization/<run-id>
OUTPUTS_DIR=<run-dir>/outputs
FIGURES_DIR=<run-dir>/figures
REPORT_DIR=<run-dir>/report
```

Create only the run and results directories. Let each pipeline component create its own output
subdirectory.

## 2. Freeze configuration and initialize state

Write `<run-dir>/config.json` with the resolved decisions. Use this schema as the starting point:

```json
{
  "vcf_build": "GRCh38",
  "tumor_sample": "TUMOR",
  "hla": ["HLA-A*02:01", "HLA-B*07:02", "HLA-C*07:02"],
  "expression_units": "TPM",
  "include_indels": true,
  "use_vep_rest": true,
  "germline_af_max": 0.001,
  "report_requested": true,
  "benchmark_mode": "bundled-validated-fixture"
}
```

Record absent optional inputs as JSON `null`; do not invent paths or values. Initialize state with
every supplied input. Omit an optional `--input` when it is absent.

```bash
python3 scripts/run_state.py init \
  --run-dir <run-dir> \
  --results-dir <results-dir> \
  --case-id <de-identified-case-id> \
  --config <run-dir>/config.json \
  --input vcf=<somatic-vcf> \
  --input expression=<expression-table> \
  --input rna_bam=<tumor-rna-bam>
```

Keep direct HLA values in `config.json`; when they came from a file, also add
`--input hla_file=<hla-file>`. Omit absent optional file inputs. Use `--hash-mode full` if a content
digest is required even for very large inputs. Otherwise the default hashes files up to 256 MiB
and explicitly marks larger fingerprints as metadata-only.

## 3. Phase 0 — intake

For a real case, resolve these decisions before computation:

1. VCF path and whether it has `CSQ` or `ANN` consequence/HGVS annotation.
2. Genome build: GRCh37/hg19 or GRCh38/hg38.
3. Real HLA-I alleles or an upstream typing plan.
4. Optional expression table path, gene-ID column, value column, and units.
5. Optional tumor RNA BAM and matching index.
6. Whether to include indel/frameshift neoORFs; default yes.
7. Whether the desired result is a peptide shortlist. Use `neoantigen-io-response` for a
   patient-level checkpoint-response score.
8. Requested outputs and any time/resource limit.

Ask only unresolved questions. HLA-I and a usable somatic VCF are blocking. If no data was supplied
and the user asked to demonstrate the skill, use the Pt22 demo in section 10.

Begin and complete intake by recording `plan.md` and `config.json` as artifacts.

## 4. Phase 1 — preflight

Check before launching the scientific pipeline:

- every required path is readable and non-empty;
- VCF build is known and matches transcript/CDS resolution;
- VCF has usable annotation or VEP REST is enabled and reachable;
- HLA values normalize to four-digit `HLA-A/B/C*NN:NN` form;
- expression headers and units are identified;
- BAM index exists when RNA BAM is supplied;
- MHCflurry imports and its class-I presentation models are installed;
- expected Python dependencies import;
- the durable workspace has sufficient free space;
- the engine-independent smoke suite passes.

Run the bundled checks:

```bash
python3 tests/static_test.py
python3 tests/run_state_test.py
```

The static suite can pass without MHCflurry by verifying its hard-fail contract, but it still
requires Biomni's base scientific packages, including NumPy, pandas, cyvcf2, and Biopython.
Preflight additionally requires MHCflurry and downloaded presentation models for a scientific run.
Install when authorized:

```bash
pip install mhcflurry
mhcflurry-downloads fetch models_class1_presentation
```

Write `<run-dir>/preflight.json` containing tool versions, MHCflurry model availability, input
checks, build, annotation mode, HLA list, optional-input status, free space, smoke-test status, and
the exact run command. Complete the phase only when its `ok` field is true.

## 5. Phase 2 — prioritization

Run the CLI on the real case. The CLI accepts HLA values as a comma- or space-separated string.

```bash
python3 scripts/neoantigen_tesla.py \
  --vcf <somatic-vcf> \
  --hla "HLA-A*02:01,HLA-B*07:02,HLA-C*07:02" \
  --expression <expression-table> \
  --rna-bam <tumor-rna-bam> \
  --tumor-sample <tumor-sample> \
  --out <run-dir>/outputs
```

Omit optional flags whose inputs are absent. Add `--no-indels` only when the frozen configuration
explicitly excludes neoORFs. Add `--no-vep-rest` only when local VCF annotation is sufficient.

For a call that may approach the platform timeout, checkpoint it and use managed background
execution. Direct stdout/stderr to `<run-dir>/logs/prioritization.log` through the platform's log
capture, not an unmanaged detached shell.

Required gate artifacts:

- `outputs/neoantigens.csv`
- `outputs/prioritized_neoantigens.csv`
- `outputs/summary.csv`
- `outputs/analysis.json`
- the durable command log

Never continue from a partial export after `EngineUnavailable`.

## 6. Phase 3 — validation

Validate the case outputs before plotting or interpretation:

- `analysis.json.engine` names MHCflurry;
- `n_candidates` is positive and equals the exported candidate-row count;
- the sum of `tier_counts` equals `n_candidates`;
- every exported peptide has a real `mut_rank` and supported `hla_best`;
- prioritized rows are only Tier 1 or Tier 2;
- missing expression, VAF, stability, agretopicity, or foreignness remains null;
- no field or log claims that null means zero;
- the VCF build and filtering configuration match `config.json`;
- dropped variants/peptides are described, not silently replaced;
- output digests are recorded.

Rerun `python3 tests/static_test.py` after an environment or skill-code change. The full real TESLA
benchmark is an environment/model validation, not a patient-specific measurement. Rerun it when
MHCflurry models or feature code changed; otherwise use the shipped validated benchmark fixture
for report context and record that choice.

To refresh the benchmark with real MHCflurry values:

```python
import sys
sys.path.insert(0, "scripts")
import benchmark_tesla as bt

metrics = bt.benchmark_real_tesla("assets/benchmark/TESLA_neoepitopes.csv")
bt.export_benchmark(metrics, "<run-dir>/benchmark")
print(bt.summarize(metrics))
```

Do not substitute this generic export directly for the report fixture; the current report expects
the bundled fixture's `summary` wrapper. Update the report input only after creating and validating
that wrapper deliberately.

Write `<run-dir>/validation.json` with every assertion and its evidence.

## 7. Phase 4 — visualization and benchmark context

Generate run-specific data plots from the validated patient CSV and the bundled validated benchmark
summary:

```bash
python3 scripts/generate_plots.py \
  --neoantigens <run-dir>/outputs/neoantigens.csv \
  --benchmark tests/fixtures/benchmark_summary.json \
  --outdir <run-dir>/figures
```

The expected data plots are PNG and SVG forms of tier distribution, binding by tier, feature
separation, and ranking performance. Keep the shipped schematic infographics as method diagrams;
do not present their Pt22 evidence strips as measurements from a new patient.

Record `visualization_manifest.json` with source paths and digests for every figure. Inspect images
for clipped labels, missing legends, and accidental patient identifiers before completing the gate.

## 8. Phase 5 — reporting

Build the Biomni PDF from validated outputs:

```bash
python3 scripts/generate_report.py \
  --results <run-dir>/outputs \
  --benchmark tests/fixtures/benchmark_summary.json \
  --figures <run-dir>/figures \
  --out <run-dir>/report/report_neoantigen_tesla.pdf \
  --sample <de-identified-case-id>
```

The report must distinguish case-specific results from bundled benchmark and schematic context.
State which optional measurements were absent and how composite renormalization handled them.
Do not write a clinical treatment recommendation; frame Tier 1/2 candidates as experimental
validation priorities.

Render or inspect the PDF and record page count, file size, and visual-QA result in
`report_manifest.json`.

## 9. Phase 6 — final QA and handoff

Run state verification, then copy only promised user-facing artifacts to the configured results
directory. Preserve the durable run as the authoritative audit trail.

Final handoff normally includes:

- `neoantigens.csv`
- `prioritized_neoantigens.csv`
- `summary.csv`
- `analysis.json`
- requested PNG/SVG figures
- `report_neoantigen_tesla.pdf` when requested
- `run_summary.md` with provenance, counts, missingness, limitations, and reproduction commands
- `status.json`

Run:

```bash
python3 scripts/run_state.py verify --run-dir <run-dir>
```

Complete `handoff` with the final files as named artifacts. The state helper then publishes
`status: complete`. Never mark completion before the copied artifacts themselves are fingerprinted.

## 10. Built-in Pt22 demo

Use the demo only when the user requested a demonstration or supplied no case data and clearly
wants the bundled example. It is a real metastatic melanoma case with real Pt22 variants,
expression, and HLA-I:

```bash
python3 scripts/neoantigen_tesla.py \
  --vcf assets/demo_hugo_pt22_somatic.vcf \
  --hla "HLA-A*01:01,HLA-A*02:01,HLA-B*27:05,HLA-B*37:01,HLA-C*02:02,HLA-C*06:02" \
  --expression assets/demo_hugo_pt22_expression.tsv \
  --tumor-sample TUMOR \
  --out <run-dir>/outputs
```

Pt22 is SNV-only. `assets/demo_somatic.vcf` is a labelled synthetic code-path fixture for indel,
germline-filter, and synonymous-filter tests; never describe it as a sequenced patient or use it
for biological claims.

## 11. Failure lookup

- `EngineUnavailable`: install MHCflurry and models, or repair required inputs. Do not emit scores.
- Unsupported allele: correct four-digit formatting; if no supplied allele can be scored, block.
- No peptides: inspect VCF consequence/HGVSp/HGVSc fields, genome build, UniProt residue validation,
  and Ensembl/UniProt reachability.
- No indel peptides: require transcript ID plus HGVSc/CDS context; Pt22 itself has no indels.
- No Tier 1: check whether expression was omitted or candidates fail binding/abundance gates; do not
  lower thresholds ad hoc.
- Blank recognition fields: expected when wild-type context, Biopython, or reference evidence is
  unavailable; preserve null and document renormalization.
- Slight score shifts: MHCflurry backends can vary across hosts; record versions and model state,
  never replace results with remembered fixture numbers.
- Report build failure: retain validated CSV/JSON outputs, repair reporting separately, and keep the
  run active or explicitly renegotiate the PDF deliverable.
