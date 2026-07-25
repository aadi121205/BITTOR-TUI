#!/usr/bin/env bash
# Rebuilds dist/bitchat-tui, a standalone single-file Linux binary, from the
# current source in bitchat_tui/. Run this after any code change you want
# reflected in the compiled binary.
set -euo pipefail

PROJECT_ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
CONDA_ENV="${BITCHAT_TUI_CONDA_ENV:-bitchat-tui}"
CONDA_BASE="${BITCHAT_TUI_CONDA_BASE:-/opt/anaconda3}"
PYTHON="/opt/conda/envs/${CONDA_ENV}/bin/python"

if [ ! -x "$PYTHON" ]; then
    # Fall back to whatever "conda" resolves to, in case envs don't live under /opt/conda here.
    PYTHON="$("$CONDA_BASE/bin/conda" run -n "$CONDA_ENV" which python)"
fi

echo "==> Using interpreter: $PYTHON"
"$PYTHON" -c "import PyInstaller" 2>/dev/null || {
    echo "==> Installing PyInstaller + hooks into '$CONDA_ENV'..."
    "$PYTHON" -m pip install --quiet pyinstaller pyinstaller-hooks-contrib
}

cd "$PROJECT_ROOT"
echo "==> Building..."
"$PYTHON" -m PyInstaller \
    --name bitchat-tui \
    --onefile \
    --noconfirm \
    --collect-all textual \
    --collect-all coincurve \
    --collect-all aiohttp_socks \
    run_bitchat_tui.py

rm -rf build "bitchat-tui.spec.bak"
echo "==> Done: $PROJECT_ROOT/dist/bitchat-tui"
file "$PROJECT_ROOT/dist/bitchat-tui"
