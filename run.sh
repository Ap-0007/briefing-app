#!/usr/bin/env bash
# Morning Briefing launcher
set -e
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
if [ ! -d ".venv" ]; then
  echo "Creating virtual environment…"
  python3 -m venv .venv
  .venv/bin/pip install -q -r requirements.txt
fi
exec .venv/bin/python3 main.py "$@"
