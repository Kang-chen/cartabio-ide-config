# Cartabio IDE Config

Team AI IDE configuration repository for managing global rules, workflows, and skills across different AI IDEs (Antigravity, Cursor, etc.).

## Repository Structure

```
cartabio-ide-config/
├── README.md                    # This file
├── scripts/
│   └── sync_global_config.sh    # Bash sync script
├── global_rules/                # Global Rules (always loaded)
├── workflows/                   # Workflows/Commands (on-demand)
├── skills/                      # Agent Skills (categorized)
│   ├── global/                  # General-purpose skills
│   ├── languages/               # Language-specific skills
│   ├── infra/                   # Infrastructure skills
│   └── omics/                   # Bioinformatics skills
└── templates/                   # Templates for new projects
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

This will sync global rules and skills to `~/.gemini/antigravity/`.

### 3. Project-Level Configs

Copy the templates to your project:
```bash
cp -r templates/.agent <project-root>/
```

## Configuration Tiers

| Tier | Type | Storage | Description |
|------|------|---------|-------------|
| Global | Rules | `~/.gemini/GEMINI.md` | Personal preferences |
| Global | Skills | `~/.gemini/antigravity/skills/` | Reusable skills |
| Project | Rules | `<project>/.agent/rules/` | Project-specific |
| Project | Skills | `<project>/.agent/skills/` | Project-specific skills |

## Contributing

1. Create a branch: `feat/skill-name` or `fix/rule-name`
2. Add/update configs in the appropriate directory
3. Open a Pull Request with description
4. Team review and merge

## License

MIT
