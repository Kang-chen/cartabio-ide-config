# Loop Markdown Compatibility Guide

This reference documents what Markdown syntax works in Microsoft Loop and best practices for seamless copy-paste.

## Supported Syntax (Auto-converted when pasted)

| Markdown | Loop Behavior | Notes |
|----------|---------------|-------|
| `# H1` | ✅ Heading 1 | Max H4 supported |
| `## H2` | ✅ Heading 2 | |
| `### H3` | ✅ Heading 3 | |
| `**bold**` | ✅ Bold | |
| `*italic*` | ✅ Italic | |
| `- item` | ✅ Bullet list | |
| `1. item` | ✅ Numbered list | |
| `` `code` `` | ✅ Inline code | |
| ` ``` ` | ✅ Code block | Type ` ``` ` + Space |
| `---` | ✅ Divider | |
| `> quote` | ⚠️ Inconsistent | Use `/quote` instead |

## Requires Manual Loop Commands

These features require typing Loop slash commands after pasting:

| Feature | Loop Command | When to Use |
|---------|--------------|-------------|
| Table of Contents | `/Table of contents` | After H1-H3 headers are set |
| Status Label | `/Label` → Doc Status | At document end (Metadata) |
| Date | `/Date` | At document end (Last updated) |
| Mention | `@name` | For Maintainer field |
| Collapsible Section | `/Toggle` | For long content |

## Paste Workflow

### Option A: Paste as Markdown (Recommended)

1. Copy generated markdown content
2. In Loop, press `Ctrl+Shift+V` (Win) or `Cmd+Shift+V` (Mac)
3. Loop converts markdown syntax to rich text
4. Add Loop-specific components manually

### Option B: Paste Rich Text

1. Preview markdown in VS Code or Cursor
2. Copy from the **rendered preview** (not source)
3. Paste normally in Loop (`Ctrl+V`)
4. Add Loop-specific components manually

## Template Output Format

Templates are designed with markers for manual steps:

```markdown
> 💡 Abstract
>
> [Your summary here]

<!-- LOOP: Insert /Table of contents here -->

# 1. Context & Goal
...

---

Status: <!-- LOOP: /Label → Doc Status → Draft -->
Maintainer: <!-- LOOP: @mention -->
Last updated: <!-- LOOP: /Date -->
```

The `<!-- LOOP: ... -->` comments indicate where to use Loop commands.
