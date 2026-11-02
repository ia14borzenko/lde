#!/usr/bin/env bash
# toolchain/doctor.sh — диагностика окружения LDE и (с --fix) установка недостающего.
#
# Использование:
#   toolchain/doctor.sh [--fix] [--quiet]
# Коды возврата: 0 — всё критичное на месте; 1 — чего-то критичного не хватает.
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LDE_ROOT
. "$SCRIPT_DIR/env.env"
. "$SCRIPT_DIR/pins.env"
. "$SCRIPT_DIR/common.sh"

export PATH="$LDE_TEX_BIN:$HOME/.local/bin:$PATH"

FIX=0
QUIET=0
for arg in "$@"; do
  case "$arg" in
    --fix) FIX=1 ;;
    --quiet|-q) QUIET=1 ;;
    -h|--help)
      sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
    *) lde_die "doctor.sh: неизвестный аргумент: $arg" ;;
  esac
done

lde_log_start doctor
# вывод дублируем в лог, но не теряем цвет в терминале
if [ "$QUIET" = 0 ]; then
  exec > >(tee -a "$LDE_CURRENT_LOG") 2>&1
fi

FAILED=0
WARNED=0

# name|bin|version-args|critical(1/0)|hint
check_bin() {
  local name="$1" bin="$2" ver_args="$3" critical="$4" hint="$5"
  local path ver status
  if ! have "$bin"; then
    printf '%-14s %-8s %-28s %s\n' "$name" "MISSING" "-" "$hint"
    if [ "$critical" = 1 ]; then FAILED=$((FAILED+1)); else WARNED=$((WARNED+1)); fi
    return 0
  fi
  path="$(command -v "$bin")"
  # shellcheck disable=SC2086
  ver="$("$bin" $ver_args 2>&1 | head -1 | cut -c1-60)" || ver="?"
  printf '%-14s %-8s %-60s %s\n' "$name" "OK" "$ver" "$path"
}

check_tex_file() {
  local f="$1" pkg="$2"
  if kpsewhich "$f" >/dev/null 2>&1; then
    printf '%-24s %-8s %s\n' "$f" "OK" "$(kpsewhich "$f")"
  else
    printf '%-24s %-8s %s\n' "$f" "MISSING" "пакет: $pkg"
    FAILED=$((FAILED+1))
  fi
}

check_py_module() {
  local mod="$1" label="$2" py="$3"
  if [ ! -x "$py" ]; then
    printf '%-24s %-8s %s\n' "$label" "MISSING" "нет venv ($LDE_VENV)"
    FAILED=$((FAILED+1)); return 0
  fi
  if "$py" -c "import $mod" >/dev/null 2>&1; then
    printf '%-24s %-8s %s\n' "$label" "OK" "$("$py" -c "import $mod; print(getattr($mod,'__version__','ok'))" 2>/dev/null | head -1)"
  else
    printf '%-24s %-8s %s\n' "$label" "MISSING" "pip install -r toolchain/requirements.txt"
    FAILED=$((FAILED+1))
  fi
}

run_checks() {
  FAILED=0; WARNED=0
  echo "==================================================================="
  echo " LDE doctor — проверка инструментов (LDE_ROOT=$LDE_ROOT)"
  echo "==================================================================="
  echo ""
  echo "--- TeX (TinyTeX: $LDE_TINYTEX_DIR) ---"
  check_bin xelatex  "$LDE_TEX_BIN/xelatex"  "--version" 1 "TeX: bin/lde doctor --fix"
  check_bin pdflatex "$LDE_TEX_BIN/pdflatex" "--version" 0 "нужен только для pdfLaTeX-fallback"
  check_bin latexmk  "$LDE_TEX_BIN/latexmk"  "-v"        1 "TeX: bin/lde doctor --fix"
  check_bin biber    "$LDE_TEX_BIN/biber"    "--version" 1 "TeX: bin/lde doctor --fix"
  check_bin dvisvgm  "$LDE_TEX_BIN/dvisvgm"  "--version" 1 "режим формул --math=svg"
  check_bin tlmgr    "$LDE_TEX_BIN/tlmgr"    "--version" 1 "управление TeX-пакетами"
  echo ""
  echo "--- ключевые TeX-пакеты ---"
  check_tex_file fontspec.sty fontspec
  check_tex_file biblatex.sty biblatex
  check_tex_file biblatex-gost.def biblatex-gost
  check_tex_file gost-numeric.bbx biblatex-gost
  check_tex_file standalone.cls standalone
  check_tex_file extsizes.sty extsizes
  check_tex_file hyph-ru.tex hyphen-russian
  check_tex_file texgyretermes-math.otf tex-gyre-math
  check_tex_file gost2008n.bst gost
  echo ""
  echo "--- Документный конвейер ---"
  check_bin pandoc "$LDE_PANDOC" "--version" 1 "pandoc: bin/lde doctor --fix"
  check_bin soffice "soffice" "--version --headless" 0 "нужен для рендера DOCX/LibreOffice QA"
  check_bin pdftotext "pdftotext" "-v" 0 "нужен для QA текста (poppler-utils)"
  check_bin pdftoppm "pdftoppm" "-v" 0 "нужен для QA геометрии (poppler-utils)"
  check_bin convert "convert" "--version" 0 "ImageMagick: PNG-fallback для SVG"
  check_bin magick "magick" "--version" 0 "ImageMagick 7"
  echo ""
  echo "--- Python venv ($LDE_VENV) ---"
  printf '%-24s %-8s %s\n' "python" "$([ -x "$LDE_VENV/bin/python" ] && echo OK || echo MISSING)" "$("$LDE_VENV/bin/python" --version 2>/dev/null || echo '-')"
  local py; py="$(lde_python)"
  check_py_module docx "python-docx" "$py"
  check_py_module lxml "lxml" "$py"
  check_py_module pymupdf "PyMuPDF" "$py"
  check_py_module PIL "Pillow" "$py"
  check_py_module yaml "PyYAML" "$py"
  check_py_module tomlkit "tomlkit" "$py"
  check_py_module olefile "olefile" "$py"
  check_py_module websocket "websocket-client" "$py"
  echo ""
  echo "--- LDE v2: расчёты и Studio ---"
  printf '%-24s %-8s %s\n' "python3 (расчёты)" "$(python3 -c 'import numpy, matplotlib' 2>/dev/null && echo OK || echo WARN)" "numpy + matplotlib в системном python3 (для calc/*)"
  check_bin "chrome" "google-chrome" "--version" 0 "окно Studio (без него — адрес для любого браузера)"
  echo ""
  echo "--- Редактор и служебное ---"
  check_bin code "code" "--version" 0 "VS Code (кнопка «Проект в VS Code»)"
  check_bin git  "git"  "--version" 0 "git init проекта"
  echo ""
  echo "--- Сеть (не обязательна для работы) ---"
  if lde_net_ok; then
    echo "сеть: OK (CTAN mirror доступен)"
  else
    echo "сеть: недоступна (CTAN mirror не отвечает) — установка/обновление не сработают"
    WARNED=$((WARNED+1))
  fi
  echo ""
}

print_summary() {
  echo "==================================================================="
  if [ "$FAILED" = 0 ]; then
    if [ "$WARNED" = 0 ]; then
      printf '%sИТОГ: всё критичное и опциональное на месте.%s\n' "$C_GREEN" "$C_OFF"
    else
      printf '%sИТОГ: критичное на месте, предупреждений: %d.%s\n' "$C_GREEN" "$WARNED" "$C_OFF"
    fi
  else
    printf '%sИТОГ: не хватает критичных компонентов: %d.%s\n' "$C_RED" "$FAILED" "$C_OFF"
    echo "Запустите: $LDE_ROOT/bin/lde doctor --fix"
  fi
  echo "Версии: $LDE_ROOT/toolchain/versions.lock"
  echo "==================================================================="
}

run_checks
if [ "$FIX" = 1 ]; then
  echo ""
  lde_info "== doctor --fix: доустановка недостающего =="
  if "$SCRIPT_DIR/install.sh"; then
    lde_ok "install.sh завершён"
  else
    lde_warn "install.sh завершился с ошибкой — см. лог выше"
  fi
  "$SCRIPT_DIR/write-versions-lock.sh" || true
  echo ""
  echo "--- повторная проверка после --fix ---"
  run_checks
fi
print_summary
[ "$FAILED" = 0 ] || exit 1
exit 0
