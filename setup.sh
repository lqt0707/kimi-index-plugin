#!/bin/bash
# Setup script for kimi-index-plugin
# Run this after installing the plugin to create the virtual environment

set -e

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$SCRIPT_DIR"

if [ -d ".venv" ]; then
    echo "Virtual environment already exists at .venv/"
    echo "To recreate, delete .venv/ and run this script again."
    exit 0
fi

echo "Creating virtual environment..."
python3 -m venv .venv

echo "Installing dependencies..."
./.venv/bin/pip install --upgrade pip
./.venv/bin/pip install httpx numpy watchdog

echo ""
echo "✅ Setup complete!"
echo ""
echo "Next steps:"
echo "  1. Ensure you have logged in to kimi-cli:  kimi login"
echo "  2. Install the plugin:  kimi plugin install $(pwd)"
echo "  3. In your project directory, build the index:"
echo "     → Ask the LLM to run CodeIndexBuild, or run it manually"
