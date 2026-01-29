#!/bin/bash
# Sync Project Config Script
# Syncs project-specific rules and skills to target directory

set -e

# Configuration
SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
REPO_ROOT="$(dirname "$SCRIPT_DIR")"

# Parse arguments
PROJECT_NAME=""
TARGET_DIR=""
FORCE=false

usage() {
    echo "Usage: $0 <project-name> --project-dir <target-dir> [-f|--force]"
    echo ""
    echo "Arguments:"
    echo "  <project-name>       Name of the project template (e.g., skills_demo)"
    echo "  --project-dir <dir>  Target directory to sync project config to"
    echo "  -f, --force          Force overwrite existing files"
    echo ""
    echo "Available projects:"
    for project in "$REPO_ROOT/project"/*/; do
        if [[ -d "$project/.agent" ]]; then
            echo "  - $(basename "$project")"
        fi
    done
    exit 1
}

while [[ $# -gt 0 ]]; do
    case $1 in
        --project-dir)
            TARGET_DIR="$2"
            shift 2
            ;;
        -f|--force)
            FORCE=true
            shift
            ;;
        -h|--help)
            usage
            ;;
        *)
            if [[ -z "$PROJECT_NAME" ]]; then
                PROJECT_NAME="$1"
            fi
            shift
            ;;
    esac
done

if [[ -z "$PROJECT_NAME" || -z "$TARGET_DIR" ]]; then
    usage
fi

PROJECT_SOURCE="$REPO_ROOT/project/$PROJECT_NAME"
if [[ ! -d "$PROJECT_SOURCE/.agent" ]]; then
    echo "Error: Project '$PROJECT_NAME' not found or has no .agent directory"
    usage
fi

echo "=== Project Config Sync ==="
echo "Project: $PROJECT_NAME"
echo "Source: $PROJECT_SOURCE"
echo "Target: $TARGET_DIR"
echo ""

# Create target .agent directories
mkdir -p "$TARGET_DIR/.agent/rules"
mkdir -p "$TARGET_DIR/.agent/skills"

# Sync rules
RULES_SOURCE="$PROJECT_SOURCE/.agent/rules"
RULES_TARGET="$TARGET_DIR/.agent/rules"

if [[ -d "$RULES_SOURCE" ]]; then
    echo "Syncing rules..."
    for rule in "$RULES_SOURCE"/*; do
        if [[ -f "$rule" ]]; then
            rule_name=$(basename "$rule")
            target_rule="$RULES_TARGET/$rule_name"
            
            if [[ -f "$target_rule" && "$FORCE" == false ]]; then
                echo "  Skipping existing rule: $rule_name"
            else
                echo "  Copying: $rule_name"
                cp "$rule" "$target_rule"
            fi
        fi
    done
fi

# Sync skills
SKILLS_SOURCE="$PROJECT_SOURCE/.agent/skills"
SKILLS_TARGET="$TARGET_DIR/.agent/skills"

if [[ -d "$SKILLS_SOURCE" ]]; then
    echo "Syncing skills..."
    for skill in "$SKILLS_SOURCE"/*/; do
        if [[ -d "$skill" ]]; then
            skill_name=$(basename "$skill")
            target_skill="$SKILLS_TARGET/$skill_name"
            
            if [[ -d "$target_skill" && "$FORCE" == false ]]; then
                echo "  Skipping existing skill: $skill_name"
            else
                echo "  Copying: $skill_name"
                rm -rf "$target_skill" 2>/dev/null || true
                cp -rL "$skill" "$SKILLS_TARGET/"
            fi
        fi
    done
fi

echo ""
echo "=== Sync Complete ==="
echo "To force overwrite existing files, run with -f or --force flag"
