#!/usr/bin/env bash
set -euo pipefail
cd -- "$(dirname -- "$0")"
python3 -m venv .venv-agent
.venv-agent/bin/python -m pip install --upgrade pip
.venv-agent/bin/python -m pip install -r requirements-agent.txt
.venv-agent/bin/python -m pip freeze > installed-agent.txt
echo 'Agent environment ready. Activate it with: source .venv-agent/bin/activate'
