# Workflows

This directory contains reusable workflow templates that can be triggered via `/` commands.

## Available Workflows

| Workflow | Description |
|----------|-------------|
| `refactor.md` | Code refactoring workflow |
| `review.md` | Code review workflow |

## Creating New Workflows

Create a new `.md` file with YAML frontmatter:

```markdown
---
description: Short description of the workflow
---

# Workflow Name

1. Step one
2. Step two
3. ...
```

## Usage

In your AI IDE, type `/workflow-name` to trigger the workflow.
