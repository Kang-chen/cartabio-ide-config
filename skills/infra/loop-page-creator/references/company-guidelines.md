# Company Documentation Guidelines

This reference contains the full company standards for Loop documentation.

## Architecture: Module → Entity → Perspective

```
Layer 1: Modules     - "What category?" (Storage, Cloud, Pipeline, Experiments)
Layer 2: Entities    - "What specific thing?" (LaminDB, AWS RDS, Component Library)
Layer 3: Perspectives - "Who is reading?" (User, Admin, Developer)
```

### Perspective Types

| Perspective | Icon | Target Audience | Content Focus |
|-------------|------|-----------------|---------------|
| User View | 📘 | End users | QuickStart, API docs, tutorials |
| Admin View | 🔧 | Maintainers | SOPs, maintenance logs, troubleshooting |
| Dev View | 💻 | Developers | Architecture, source code, internals |

## Naming Conventions

### Document Tags

| Tag | Use Case | Example |
|-----|----------|---------|
| `[Doc]` | General knowledge, architecture | `[Doc] LaminDB Architecture` |
| `[SOP]` | Step-by-step procedures | `[SOP] Database Restore` |
| `[Log]` | Immutable event records | `[Log] 2026-01-06 Migration` |
| `[Ref]` | Reference sheets, dictionaries | `[Ref] Error Code List` |
| `[Draft]` | Work in progress | `[Draft] New Schema Proposal` |

### File Naming

- Format: `[tag]-brief-description` (for page title)
- Use title case for display: `[Doc] Structure Design Guidelines`
- Lowercase hyphenated for file storage: `doc-structure-design-guidelines.md`

## Page Structure (Required)

Every document must include these four sections in order:

### 1. Abstract (Quote Block)

- **Position**: First element
- **Purpose**: Help reader decide in 5 seconds if this is the right document
- **Format**: `> 💡` followed by 1-2 sentence summary

### 2. Table of Contents

- **Position**: After abstract
- **Loop Command**: `/Table of contents`
- **Auto-updates**: Based on H1, H2, H3 headers

### 3. Body Content

Recommended structure:

```
# 1. Context & Goal
- Objective: [Goal]
- Prerequisites: [Requirements]

# 2. Implementation / Steps
## 2.1 [Step One]
## 2.2 [Step Two]

# 3. Reference / Troubleshooting
(Optional: Links, error codes, FAQ)
```

### 4. Metadata (Required)

| Field | Loop Component | Values |
|-------|----------------|--------|
| Status | `/Label` → Doc Status | 🟢 Stable, 🟡 Draft, 🔴 Needs Update, ⚫ Deprecated, 🔵 In Review |
| Maintainer | `@mention` | Responsible person |
| Last updated | `/Date` | Auto-generated date |

## Icon Reference

### Containers (Directories)

```
💾  Storage / Database
☁️  Cloud / Infrastructure
🔀  Pipeline / Workflow
🧪  Experiments / Protocols
```

### Perspectives (Sub-pages)

```
📘  User Guide / QuickStart
🔧  Maintenance / Admin
💻  Developer / Internals
📝  Logs / Records
```

## Documentation Criteria

Document content if it matches ANY of these:

1. **Handover Rule**: Would successor need this to take over?
2. **Reusable Knowledge**: Can this be used by others or repeated?
3. **Context & Why**: Does this explain decisions, not just code?

**Pro Tip**: If you answer the same question twice in chat, create a document.

## Important Rules

- 🚫 **No Sensitive Info**: No passwords, API keys, credentials in plain text
- 🔗 **Single Source**: Link to existing docs, don't duplicate
- 🧹 **Deprecate, Don't Delete**: Change status to Deprecated or move to Archive
- 📁 **Max 3-4 Levels**: Keep directory nesting shallow
