#!/usr/bin/env bash
set -e

PYTHON_VERSIONS="${PYTHON_VERSIONS-3.9 3.10 3.11}"

if ! command -v pdm &>/dev/null; then
    if ! command -v pipx &>/dev/null; then
        python3 -m pip install --user pipx
    fi
    pipx install pdm
fi
if ! pdm self list 2>/dev/null | grep -q pdm-multirun; then
    pipx inject pdm pdm-multirun
fi

pdm multirun -vi ${PYTHON_VERSIONS// /,} pdm install -G duty,docs,maintain,quality,tests,typing,security
pdm install -G duty,docs,maintain,quality,tests,typing,security
