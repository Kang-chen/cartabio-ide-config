# Workspace Rules Template

Copy this folder to your project root as `.agent/rules/` and customize.

## Example Project Rule

```markdown
---
description: Project-specific coding guidelines
activation: Always On
globs: ["**/*.py"]
---

# Project Guidelines

## Tech Stack
- Python 3.11+
- FastAPI
- PostgreSQL

## Business Logic
- All API responses must include timestamps
- User data must be sanitized before storage
```

## File Structure

```
<project-root>/
└── .agent/
    ├── rules/
    │   └── project.md
    ├── workflows/
    │   └── deploy.md
    └── skills/
        └── project-skill/
            └── SKILL.md
```
