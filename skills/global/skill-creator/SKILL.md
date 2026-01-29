---
name: skill-creator
description: Guide for creating effective skills. This skill should be used when users want to create a new skill (or update an existing skill) that extends Claude's capabilities with specialized knowledge, workflows, or tool integrations.
---

# Skill Creator

This skill provides guidance for creating effective skills.

## About Skills

Skills are modular, self-contained packages that extend Claude's capabilities by providing specialized knowledge, workflows, and tools. Think of them as "onboarding guides" for specific domains or tasks.

### What Skills Provide

1. Specialized workflows - Multi-step procedures for specific domains
2. Tool integrations - Instructions for working with specific file formats or APIs
3. Domain expertise - Company-specific knowledge, schemas, business logic
4. Bundled resources - Scripts, references, and assets for complex and repetitive tasks

## Core Principles

### Concise is Key

The context window is a public good. **Default assumption: Claude is already very smart.** Only add context Claude doesn't already have.

### Set Appropriate Degrees of Freedom

- **High freedom (text-based instructions)**: Use when multiple approaches are valid
- **Medium freedom (pseudocode or scripts with parameters)**: Use when a preferred pattern exists
- **Low freedom (specific scripts, few parameters)**: Use when operations are fragile

### Anatomy of a Skill

```
skill-name/
├── SKILL.md (required)
│   ├── YAML frontmatter metadata (required)
│   │   ├── name: (required)
│   │   └── description: (required)
│   └── Markdown instructions (required)
└── Bundled Resources (optional)
    ├── scripts/          - Executable code
    ├── references/       - Documentation
    └── assets/           - Files used in output
```

### Progressive Disclosure

1. **Metadata (name + description)** - Always in context (~100 words)
2. **SKILL.md body** - When skill triggers (<5k words)
3. **Bundled resources** - As needed (Unlimited)

## Skill Creation Process

1. Understand the skill with concrete examples
2. Plan reusable skill contents (scripts, references, assets)
3. Initialize the skill (run `scripts/init_skill.py`)
4. Edit the skill (implement resources and write SKILL.md)
5. Package the skill (run `scripts/package_skill.py`)
6. Iterate based on real usage

### Step 3: Initializing the Skill

```bash
scripts/init_skill.py <skill-name> --path <output-directory>
```

### Step 5: Packaging a Skill

```bash
scripts/package_skill.py <path/to/skill-folder> [output-directory]
```

### Frontmatter Guidelines

- `name`: The skill name (hyphen-case, max 64 chars)
- `description`: Primary triggering mechanism - include what the Skill does AND when to use it (max 1024 chars)

### What NOT to Include

- README.md, CHANGELOG.md, INSTALLATION_GUIDE.md
- The skill should only contain information needed for an AI agent to do the job

## Resources

- **Multi-step processes**: See [references/workflows.md](references/workflows.md)
- **Output formats**: See [references/output-patterns.md](references/output-patterns.md)
