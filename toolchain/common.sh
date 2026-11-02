#!/usr/bin/env bash
# toolchain/common.sh — общие функции скриптов LDE (source, не исполняется).
# Требует: LDE_ROOT уже установлен и toolchain/env.env уже подключён.

set -euo pipefail

# --- цвета только в терминале ---
if [ -t 1 ] && [ -z "${NO_COLOR:-}" ]; then
  C_RED=$'\033[31m'; C_GREEN=$'\033[32m'; C_YELLOW=$'\033[33m'
  C_BLUE=$'\033[34m'; C_BOLD=$'\033[1m'; C_OFF=$'\033[0m'
else
  C_RED=""; C_GREEN=""; C_YELLOW=""; C_BLUE=""; C_BOLD=""; C_OFF=""
fi

lde_info()  { printf '%s[lde]%s %s\n'  "$C_BLUE" "$C_OFF" "$*" >&2; }
lde_ok()    { printf '%s  OK %s %s\n'  "$C_GREEN" "$C_OFF" "$*"; }
lde_warn()  { printf '%s WARN%s %s\n'  "$C_YELLOW" "$C_OFF" "$*" >&2; }
lde_err()   { printf '%s FAIL%s %s\n'  "$C_RED" "$C_OFF" "$*" >&2; }
lde_die()   { lde_err "$*"; exit 1; }

# --- лог команды: tee в $LDE_LOG_DIR/<name>-<timestamp>.log ---
lde_log_start() {
  local name="$1"; shift
  local dir="${LDE_LOG_DIR:-$LDE_ROOT/logs}"
  mkdir -p "$dir"
  LDE_CURRENT_LOG="$dir/${name}-$(date +%Y%m%d-%H%M%S).log"
  export LDE_CURRENT_LOG
  lde_info "лог: $LDE_CURRENT_LOG"
}

# --- проверка сети (короткий таймаут, без падения) ---
lde_net_ok() {
  local url="${1:-https://mirror.ctan.org/systems/texlive/tlnet/}"
  curl -fsIL --max-time 15 "$url" >/dev/null 2>&1
}

# --- путь к python из venv (или системному, если venv нет) ---
lde_python() {
  if [ -x "${LDE_PY:-}" ]; then printf '%s' "$LDE_PY";
  elif [ -x "$LDE_VENV/bin/python" ]; then printf '%s' "$LDE_VENV/bin/python";
  else printf 'python3'; fi
}

# --- проверка, что инструмент есть в PATH ---
have() { command -v "$1" >/dev/null 2>&1; }

# --- версия команды (первая строка вывода), безопасно ---
tool_version() {
  local bin="$1"; shift
  if ! have "$bin"; then printf 'MISSING'; return 0; fi
  if ! "$bin" "$@" >/dev/null 2>&1; then printf 'no-version'; return 0; fi
}
