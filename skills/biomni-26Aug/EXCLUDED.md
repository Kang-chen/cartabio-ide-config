# Excluded files

匯出自 Biomni Lab (https://biomni.phylo.bio) 的 92 個 skill，
以下檔案在放進本 repo 前被刻意排除，以控制 repo 體積。
所有排除項皆經確認 **不影響 skill 執行**。

匯出日期：2026-08-27

## 1. references/*.pdf（6 檔，約 24 MB）

原始論文 PDF，僅供人閱讀。全 skill 搜尋確認沒有任何 SKILL.md
或 script 引用這些檔名。

| Skill | 檔案 |
|---|---|
| `scrnaseq-seurat-core-analysis` | `references/2021_Integrated analysis of multimodal single-cell data.pdf` (14M) |
| `scrnaseq-seurat-core-analysis` | `references/2023_Dictionary learning for integrative, multimodal and scalable single-cell analysis.pdf` (4.3M) |
| `coexpression-network` | `references/2008_WGCNA an R package for weighted correlation network analysis.pdf` (1.7M) |
| `coexpression-network` | `references/2021_WGCNA Gene Correlation Network Analysis - Bioinformatics Workbook.pdf` (1.5M) |
| `functional-enrichment-from-degs` | `references/clusterProfiler_usage_2024.pdf` (2.5M) |
| `functional-enrichment-from-degs` | `references/clusterProfiler_GOSemSim.pdf` |

## 2. neoantigen-prioritization/assets/example_output/（5 檔，約 16 MB）

成品展示用的範例輸出。字串 `example_output` 在該 skill 內
除了資料夾本身外沒有任何引用。

| 檔案 | 大小 |
|---|---|
| `report_neoantigen_tesla.pdf` | 13M |
| `neoantigens.csv` | 1.9M |
| `analysis.json` | 1.2M |
| `prioritized_neoantigens.csv` | 80K |
| `summary.csv` | 4.0K |

**注意**：`neoantigen-prioritization/tests/fixtures/` 有保留。
`scripts/generate_report.py` 與 `scripts/generate_plots.py`
將其寫死為無參數執行時的預設輸入，刪除會導致 script 失效。

## 如何取回

資料來源為 Biomni Lab 的 know-how API，需登入後的 session cookie
（HttpOnly，須在已登入的瀏覽器分頁內以 `credentials: 'include'` 呼叫）。

```
GET https://api.phylo.bio/v2/know-how/all-skills?include_user=true
GET https://api.phylo.bio/v2/know-how/skills/{id}/entrypoint          # SKILL.md
GET https://api.phylo.bio/v2/know-how/skills/{id}/files               # 檔案清單
GET https://api.phylo.bio/v2/know-how/skills/{id}/files/{encodedPath} # 單一檔案
```

各 skill 的 `id` 見同目錄 `_index.json` 或各資料夾內 `_meta.json`。

## 已知缺漏

`chip-atlas-target-genes/scripts/__init__.py` — 伺服器回 404（空檔，無影響）。
