#!/bin/bash
# Sync Global Config Script
# Syncs global rules and skills to Antigravity environment

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"
ANTIGRAVITY_DIR="$HOME/.gemini/antigravity"
SKILLS_TARGET="$ANTIGRAVITY_DIR/global_skills"

FORCE=false
if [[ "$1" == "-f" || "$1" == "--force" ]]; then
    FORCE=true
fi

echo "=== Kang IDE Config Sync ==="
echo "Repository: $REPO_ROOT"
echo "Target: $ANTIGRAVITY_DIR"
echo ""

# Create target directories if they don't exist
if [[ ! -d "$ANTIGRAVITY_DIR" ]]; then
    echo "Creating Antigravity directory..."
    mkdir -p "$ANTIGRAVITY_DIR"
fi

if [[ ! -d "$SKILLS_TARGET" ]]; then
    mkdir -p "$SKILLS_TARGET"
fi

# Sync GEMINI.md (global rules)
GEMINI_SOURCE="$REPO_ROOT/global_rules/GEMINI.md"
GEMINI_TARGET="$HOME/.gemini/GEMINI.md"

if [[ -f "$GEMINI_SOURCE" ]]; then
    if [[ -f "$GEMINI_TARGET" && "$FORCE" == false ]]; then
        echo "Skipping existing GEMINI.md (use -f to overwrite)"
    else
        echo "Syncing GEMINI.md..."
        cp "$GEMINI_SOURCE" "$GEMINI_TARGET"
    fi
else
    echo "Warning: GEMINI.md not found at $GEMINI_SOURCE"
fi

echo ""

# Sync skills (only global skills)
SOURCE_SKILLS="$REPO_ROOT/skills/global"
if [[ -d "$SOURCE_SKILLS" ]]; then
    for skill in "$SOURCE_SKILLS"/*/; do
        skill_name=$(basename "$skill")
        target_skill="$SKILLS_TARGET/$skill_name"
        
        if [[ -d "$target_skill" && "$FORCE" == false ]]; then
            echo "Skipping existing skill: $skill_name"
        else
            echo "Syncing skill: $skill_name"
            cp -r "$skill" "$SKILLS_TARGET/"
        fi
    done
fi

echo ""
echo "=== Sync Complete ==="
echo "To force overwrite existing skills, run with -f or --force flag"
