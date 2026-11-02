#!/usr/bin/env bash
# toolchain/write-versions-lock.sh — записать ФАКТИЧЕСКИ установленные версии
# в toolchain/versions.lock. Не выдумывает номера: только реальный вывод команд.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LDE_ROOT
. "$SCRIPT_DIR/env.env"
. "$SCRIPT_DIR/pins.env"
. "$SCRIPT_DIR/common.sh"

export PATH="$LDE_TEX_BIN:$PATH"
OUT="$LDE_ROOT/toolchain/versions.lock"
TMP="$(mktemp)"
trap 'rm -f "$TMP"' EXIT

first_line() { "$@" 2>/dev/null | head -1 || true; }

tex_ver=""
if have xelatex; then
  tex_ver="$(first_line xelatex --version)"
fi
tlmgr_rev=""
if have tlmgr; then
  tlmgr_rev="$(tlmgr --version 2>/dev/null | sed -n '1p' || true)"
fi
tl_release=""
if [ -f "$LDE_TINYTEX_DIR/release-texlive.txt" ]; then
  tl_release="$(grep -oE 'TeX Live [0-9]{4}' "$LDE_TINYTEX_DIR/release-texlive.txt" | head -1 || true)"
fi

py="$(lde_python)"
py_pkgs=""
if [ -x "$py" ]; then
  py_pkgs="$("$py" -m pip freeze 2>/dev/null \
    | grep -Ei '^(python-docx|lxml|pymupdf|pillow|beautifulsoup4|pyyaml|tabulate)==' \
    | sort || true)"
fi

lo_ver="$(first_line soffice --headless --version)"
code_ver="$(first_line code --version)"
convert_ver="$(first_line convert --version)"
pdftotext_ver="$(first_line pdftotext -v)"
git_ver="$(first_line git --version)"

{
  echo "# toolchain/versions.lock — ФАКТИЧЕСКИ установленные версии (генерируется"
  echo "# toolchain/write-versions-lock.sh; руками не править, кроме комментариев)."
  echo "# Сгенерировано: $(date -Iseconds)"
  echo "# Хост: $(uname -srm) / $(lsb_release -ds 2>/dev/null || echo 'n/a')"
  echo ""
  echo "# --- TeX Live / TinyTeX ---"
  echo "tinytex_asset=${TINYTEX_ASSET}"
  echo "tinytex_dir=${LDE_TINYTEX_DIR}"
  [ -n "$tl_release" ] && echo "texlive_release=${tl_release}"
  [ -n "$tex_ver" ] && echo "xetex_version=${tex_ver}"
  [ -n "$tlmgr_rev" ] && echo "tlmgr=${tlmgr_rev}"
  echo "latexmk_version=$(first_line latexmk -v)"
  echo "biber_version=$(first_line biber --version)"
  echo "dvisvgm_version=$(first_line dvisvgm --version)"
  echo "pdflatex_version=$(first_line pdflatex --version)"
  echo ""
  echo "# --- pandoc ---"
  echo "pandoc_pinned=${PANDOC_VERSION}"
  echo "pandoc_version=$(first_line "$LDE_PANDOC" --version)"
  echo "pandoc_path=${LDE_PANDOC}"
  echo ""
  echo "# --- python / venv ---"
  echo "python_version=$("$py" --version 2>&1 || echo MISSING)"
  echo "venv_path=${LDE_VENV}"
  [ -n "$py_pkgs" ] && echo "$py_pkgs"
  echo ""
  echo "# --- вспомогательные инструменты ---"
  echo "libreoffice=${lo_ver}"
  echo "imagemagick=${convert_ver}"
  echo "poppler_pdftotext=${pdftotext_ver}"
  echo "vscode=${code_ver}"
  echo "git=${git_ver}"
} > "$TMP"

mv "$TMP" "$OUT"
lde_ok "versions.lock обновлён: $OUT"
