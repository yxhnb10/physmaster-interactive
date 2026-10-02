#!/bin/bash
# Install PhysMaster skill for Claude Code
#
# After running this script, you can use '/physmaster' as a slash command
# in Claude Code from any directory.
#
# Usage:
#   bash extensions/skills/physmaster/install_cc.sh

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
SKILL_DIR="$HOME/.claude/commands"

mkdir -p "$SKILL_DIR"
cp "$SCRIPT_DIR/SKILL.md" "$SKILL_DIR/physmaster.md"

echo "[OK] PhysMaster skill installed for Claude Code."
echo "     Location: $SKILL_DIR/physmaster.md"
echo ""
echo "Usage: type '/physmaster' in Claude Code to load the skill context."
echo "       Works from any directory."
