# Cartabio IDE Config

Team AI IDE configuration repository for managing global rules, workflows, and skills across different AI IDEs (Antigravity, Cursor, etc.).

## Repository Structure

```
cartabio-ide-config/
├── README.md
├── scripts/
│   ├── sync_global_config.sh    # Sync global rules and skills
│   └── sync_project_config.sh   # Sync project-specific configs
├── global_rules/
│   └── GEMINI.md                # Global rules (Antigravity format)
├── workflows/                   # Workflows/Commands (on-demand)
├── skills/                      # Agent Skills (categorized)
│   ├── global/                  # General-purpose skills
│   ├── languages/               # Language-specific skills
│   ├── infra/                   # Infrastructure skills
│   └── omics/                   # Bioinformatics skills
└── project/                     # Project templates
    └── <project-name>/
        └── .agent/
            ├── rules/
            └── skills/
```

## Quick Start

### 1. Clone the Repository

```bash
git clone https://github.com/Kang-chen/cartabio-ide-config.git
```

### 2. Sync Global Configs

```bash
chmod +x scripts/sync_global_config.sh
./scripts/sync_global_config.sh
```

This will sync:
- `global_rules/GEMINI.md` → `~/.gemini/GEMINI.md`
- `skills/global/*` → `~/.gemini/antigravity/skills/`

### 3. Sync Project Configs

```bash
# Show available project templates
bash scripts/sync_project_config.sh --help

# Sync to your project
bash scripts/sync_project_config.sh <template-name> --project-dir /path/to/project
```

## Configuration Tiers

| Tier    | Type   | Storage                         | Description             |
| ------- | ------ | ------------------------------- | ----------------------- |
| Global  | Rules  | `~/.gemini/GEMINI.md`           | Personal preferences    |
| Global  | Skills | `~/.gemini/antigravity/skills/` | Reusable global skills  |
| Project | Rules  | `<project>/.agent/rules/`       | Project-specific rules  |
| Project | Skills | `<project>/.agent/skills/`      | Project-specific skills |

## Contributing

1. Create a branch: `feat/skill-name` or `fix/rule-name`
2. Add/update configs in the appropriate directory
3. Open a Pull Request with description
4. Team review and merge

## License

MIT
