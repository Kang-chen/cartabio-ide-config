# Project Templates

Project configuration templates containing reusable rules and skills.

## Directory Structure

Each project template should follow this structure:

```
project/<project-name>/
└── .agent/
    ├── rules/           # Project-specific rules
    │   └── agents.md    # Main rules file (lowercase required)
    └── skills/          # Project-specific skills
        └── <skill-name>/
            └── SKILL.md
```

## Creating a New Project Template

1. Create a new directory under `project/`
2. Add `.agent/rules/` and `.agent/skills/` subdirectories
3. Add `agents.md` or other rule files in `rules/` (filenames MUST be lowercase)
4. Reusable skills should be symlinks pointing to `skills/` directory

### Symlink Example

```bash
# Link to an existing global skill
ln -s ../../../../../skills/omics/skills/tcga-survival-analysis .agent/skills/tcga-survival-analysis
```

## Using Project Templates

Use `sync_project_config.sh` to copy rules and skills to your target project:

```bash
# Show available project templates
bash scripts/sync_project_config.sh --help

# Sync to target directory
bash scripts/sync_project_config.sh <project-name> --project-dir /path/to/target

# Force overwrite existing files
bash scripts/sync_project_config.sh <project-name> --project-dir /path/to/target -f
```

## Available Templates

| Template      | Description                                           |
| ------------- | ----------------------------------------------------- |
| `skills_demo` | Agent skills demo project with TCGA survival analysis |
