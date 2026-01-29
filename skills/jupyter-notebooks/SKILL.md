---
name: jupyter-notebooks
description: >
  Jupyter notebook operations: editing cells, reading notebooks, executing, and format conversion.
  Trigger when working with .ipynb files for: (1) Creating/editing/deleting/reordering cells,
  (2) Reading notebook content, (3) Executing notebooks with papermill, (4) Converting to
  HTML/PDF/script formats. Supports Cursor EditNotebook tool, Jupytext workflows, and nbformat.
---

# Jupyter Notebook Operations

## Decision Tree

```
Need to edit a notebook?
├─ In Cursor IDE? → Use EditNotebook tool directly
├─ Structural changes (add/delete/reorder)? → Jupytext workflow
├─ Small edit in single cell? → nbformat micro-edit
├─ Execute and capture outputs? → papermill
└─ Convert format? → nbconvert
```

---

## EditNotebook Tool (Cursor IDE)

Preferred method in Cursor. Parameters:

| Parameter | Required | Description |
|-----------|----------|-------------|
| `target_notebook` | Yes | Path to .ipynb |
| `cell_idx` | Yes | 0-based cell index |
| `is_new_cell` | Yes | `true` = new cell, `false` = edit existing |
| `cell_language` | Yes | `python`, `markdown`, `r`, `sql`, `shell`, `raw`, `other` |
| `old_string` | Yes | Text to replace (empty for new cells) |
| `new_string` | Yes | Replacement content |

**Critical rules:**
- Set `is_new_cell` correctly
- Include 3-5 lines context in `old_string`
- Cannot delete cells (clear content with `new_string=""`)

---

## Jupytext (Structural Edits)

### Setup & Sync

```bash
# Pair notebook (one-time)
python -m jupytext --set-formats ipynb,py:percent notebook.ipynb

# Always sync before reading .py
python -m jupytext --sync notebook.ipynb
# Or fallback:
python -m jupytext --to py:percent notebook.ipynb -o notebook.py
```

### Edit `.py` with percent-format

```python
# %% [markdown]
# # Section Title

# %%
import pandas as pd

# %% tags=["parameters"]
param1 = "default"
```

### Sync back (preserve outputs)

```bash
python -m jupytext --to ipynb --update notebook.py -o notebook.ipynb
```

---

## nbformat (Micro-Edits & Batch)

### Edit single cell

```python
import nbformat

nb = nbformat.read("notebook.ipynb", as_version=4)
nb["cells"][3]["source"] = nb["cells"][3]["source"].replace("old", "new", 1)
nbformat.write(nb, "notebook.ipynb")
```

### Add new cell

```python
new_cell = nbformat.v4.new_code_cell(source="print('Hello')")
nb["cells"].insert(5, new_cell)
```

### Delete cell

```python
del nb["cells"][3]
```

---

## papermill (Execute Notebooks)

```bash
# Basic
papermill input.ipynb output.ipynb

# With parameters
papermill input.ipynb output.ipynb -p data_path "/path" -p n_samples 1000
```

```python
import papermill as pm
pm.execute_notebook("input.ipynb", "output.ipynb", parameters={"n_samples": 1000})
```

---

## nbconvert (Format Conversion)

| Format | Command |
|--------|---------|
| HTML | `jupyter nbconvert --to html notebook.ipynb` |
| PDF | `jupyter nbconvert --to pdf notebook.ipynb` |
| Script | `jupyter nbconvert --to script notebook.ipynb` |
| Markdown | `jupyter nbconvert --to markdown notebook.ipynb` |

Execute and convert: `jupyter nbconvert --execute --to html notebook.ipynb`

Clear outputs: `jupyter nbconvert --ClearOutputPreprocessor.enabled=True --inplace notebook.ipynb`

---

## Common Issues

| Issue | Solution |
|-------|----------|
| Outputs lost after sync | Use `--update` flag with Jupytext |
| Kernel not found | `jupyter kernelspec list` then specify `-k python3` |
| Large file size | Clear outputs before git commit |
| Windows venv | Use `.\.venv\Scripts\activate` |
