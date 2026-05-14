#!/usr/bin/env bash
# This script is used to setup a local development environment for the agentyc project.
# Usage:
#   $ ./bin/setup.sh

### Bash Environment Setup
# http://redsymbol.net/articles/unofficial-bash-strict-mode/
# https://www.gnu.org/software/bash/manual/html_node/The-Set-Builtin.html
# set -o xtrace
# set -x
# shopt -s nullglob
set -o errexit
set -o errtrace
set -o nounset
set -o pipefail
IFS=$'\n'

SCRIPT_DIR="$( cd "$( dirname "${BASH_SOURCE[0]}" )" >/dev/null 2>&1 && pwd )"
cd "$SCRIPT_DIR"


if [ -f "$SCRIPT_DIR/lint.sh" ]; then
    echo "[√] already inside a cloned agentyc repo"
else
    echo "[+] Cloning agentyc repo into current directory: $SCRIPT_DIR"
    git clone https://github.com/agentyc/agentyc
    cd agentyc
fi

echo "[+] Installing uv..."
curl -LsSf https://astral.sh/uv/install.sh | sh

#git checkout main git pull
echo
echo "[+] Setting up venv"
uv venv
echo
echo "[+] Installing packages in venv"
uv sync --dev
echo
echo "[i] Tip: make sure to set TRAVERSE_LOGGING_LEVEL=debug and your LLM API keys in your .env file"
echo
uv pip show agentyc

echo "Usage:"
echo "  $ source .venv/bin/activate"
echo "  $ agentyc               run the MCP server"
echo "  or"
echo "  $ uv run python -c 'from agentyc import BrowserSession; print(BrowserSession.list_chrome_profiles())'"
echo ""
