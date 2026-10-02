#!/bin/bash
# Install PhysMaster skill for OpenClaw
#
# This script creates a symbolic link from OpenClaw's skills directory to the
# physmaster skill in this repository. This ensures the Python scripts can
# always resolve back to the PHY_Master project root for imports.
#
# Usage:
#   bash extensions/skills/physmaster/install_openclaw.sh [skills_dir]
#
# Arguments:
#   skills_dir  Path to your OpenClaw skills directory.
#               Default: ~/.openclaw/skills
#
# Examples:
#   bash extensions/skills/physmaster/install_openclaw.sh
#   bash extensions/skills/physmaster/install_openclaw.sh ~/.openclaw/skills
#   bash extensions/skills/physmaster/install_openclaw.sh ~/my-openclaw-project/skills

set -e

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"

# Default to ~/.openclaw/skills if no argument given
TARGET_DIR="${1:-$HOME/.openclaw/skills}"

if [ ! -d "$TARGET_DIR" ]; then
    echo "[INFO] Creating skills directory: $TARGET_DIR"
    mkdir -p "$TARGET_DIR"
fi

DEST="$TARGET_DIR/physmaster"

# Remove old installation (directory or broken symlink)
if [ -L "$DEST" ]; then
    echo "[INFO] Removing existing symlink at $DEST"
    rm "$DEST"
elif [ -d "$DEST" ]; then
    echo "[INFO] Removing existing directory at $DEST"
    rm -rf "$DEST"
fi

# Create symbolic link
ln -s "$SCRIPT_DIR" "$DEST"

# Verify
if [ -L "$DEST" ] && [ -f "$DEST/SKILL.md" ]; then
    echo "[OK] PhysMaster skill installed for OpenClaw."
    echo "     Symlink: $DEST -> $SCRIPT_DIR"
else
    echo "[ERROR] Installation failed. Symlink verification failed."
    exit 1
fi

echo ""
echo "Usage in OpenClaw TUI or agent:"
echo "  \"Use the physmaster skill to solve: <your physics problem>\""
echo ""
echo "Direct script invocation:"
echo "  python $DEST/scripts/run_physmaster.py --query 'your problem'"
echo "  python $DEST/scripts/arxiv_search.py --query 'quantum error correction'"
