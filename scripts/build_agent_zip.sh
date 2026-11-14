#!/usr/bin/env bash
# Build the AgentCore Runtime code zip: agent.py + dependencies for Linux ARM64, Python 3.12.
# Runs on Windows (Git Bash), Mac or Linux: pip downloads ARM64 wheels, nothing is compiled.
set -euo pipefail
cd "$(dirname "$0")/.."
rm -rf build/agentpkg && mkdir -p build/agentpkg
python -m pip install --platform manylinux2014_aarch64 --implementation cp \
  --python-version 3.12 --only-binary=:all: --target build/agentpkg -r agent/requirements.txt
cp agent/agent.py agent/intent.py build/agentpkg/
find build/agentpkg -name __pycache__ -type d -prune -exec rm -rf {} +
python - <<'PY'
import os, zipfile
src, out = "build/agentpkg", "build/inv2drawio-agent.zip"
with zipfile.ZipFile(out, "w", zipfile.ZIP_DEFLATED, compresslevel=9) as z:
    for root, _, files in os.walk(src):
        for f in files:
            p = os.path.join(root, f)
            z.write(p, os.path.relpath(p, src))
print("built", out, round(os.path.getsize(out) / 1e6, 1), "MB")
PY
