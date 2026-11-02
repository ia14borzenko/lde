#!/usr/bin/env bash
# toolchain/install.sh — идемпотентная user-space установка инструментов LDE.
#
# Ставит:
#   1) TinyTeX (TeX Live) в $LDE_TINYTEX_DIR  (по умолчанию ~/.local/share/lde-tools/TinyTeX)
#   2) TeX-пакеты через tlmgr (минимально достаточное; см. TEX_PACKAGES)
#   3) статический pandoc $PANDOC_VERSION в ~/.local/bin/pandoc
#   4) python venv toolchain/venv + toolchain/requirements.txt
#
# Никакого sudo, никакой интерактивности. Повторный запуск безопасен.
# Сеть обязательна только если чего-то не хватает; без сети печатает инструкцию.
#
# Использование:  toolchain/install.sh [--force] [--no-network-check]
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LDE_ROOT
# shellcheck source=env.env
. "$SCRIPT_DIR/env.env"
# shellcheck source=common.sh
. "$SCRIPT_DIR/common.sh"

# --- зафиксированные версии (единый источник: toolchain/pins.env) ---
# shellcheck source=pins.env
. "$SCRIPT_DIR/pins.env"

# TeX-пакеты: имена пакетов TeX Live (не файлов). longtable/tabularx входят в tools.
TEX_PACKAGES=(
  fontspec xetex babel babel-russian polyglossia unicode-math
  mathtools amsmath amsfonts siunitx microtype xcolor listings caption
  booktabs tools multirow float titlesec setspace fancyhdr lastpage
  enumitem etoolbox xstring hyperref cleveref geometry
  biblatex biblatex-gost biber csquotes
  standalone dvisvgm tex-gyre tex-gyre-math
  needspace chngcntr tocloft extsizes gost hyphen-russian tempora cyrillic pgf
)

FORCE=0
SKIP_NET_CHECK=0
for arg in "$@"; do
  case "$arg" in
    --force) FORCE=1 ;;
    --no-network-check) SKIP_NET_CHECK=1 ;;
    *) lde_die "install.sh: неизвестный аргумент: $arg" ;;
  esac
done

mkdir -p "$LDE_TOOLS_DIR" "$LDE_TOOLS_DIR/downloads"
lde_log_start install
exec > >(tee -a "$LDE_CURRENT_LOG") 2>&1

net_available() {
  if [ "$SKIP_NET_CHECK" = 1 ]; then return 0; fi
  if lde_net_ok; then return 0; fi
  lde_warn "сеть недоступна (не открывается mirror.ctan.org / github.com)."
  lde_warn "Недостающие компоненты нужно установить вручную — см. инструкцию ниже."
  return 1
}

manual_hint() {
  cat >&2 <<EOF

${C_BOLD}Что установить вручную (сеть недоступна):${C_OFF}
  1) TinyTeX ${TINYTEX_VERSION}:
       curl -L -o ${TINYTEX_ASSET} "${TINYTEX_URL}"
       mkdir -p "${LDE_TINYTEX_DIR}"
       tar -xJf ${TINYTEX_ASSET} -C "${LDE_TINYTEX_DIR}" --strip-components=0
       # затем: "${LDE_TINYTEX_DIR}/bin/x86_64-linux/tlmgr" install ${TEX_PACKAGES[*]}
  2) pandoc ${PANDOC_VERSION}:
       curl -L "${PANDOC_URL}" | tar -xz -C /tmp
       cp /tmp/pandoc-${PANDOC_VERSION}/bin/pandoc ~/.local/bin/pandoc
  3) python venv:
       python3 -m venv "${LDE_VENV}"
       "${LDE_VENV}/bin/pip" install -r "${LDE_ROOT}/toolchain/requirements.txt"

После установки повторите: ${LDE_ROOT}/bin/lde doctor --fix
EOF
}

download_verify() {
  local url="$1" out="$2" sha="$3" name="$4"
  if [ -f "$out" ] && [ "$(sha256sum "$out" | cut -d' ' -f1)" = "$sha" ]; then
    lde_ok "$name: уже скачан ($out)"
    return 0
  fi
  lde_info "скачивание $name: $url"
  rm -f "$out"
  if ! curl -fL --retry 3 --retry-delay 5 --connect-timeout 30 \
        -o "$out" "$url"; then
    lde_err "не удалось скачать $name ($url)"
    return 1
  fi
  if [ -n "$sha" ]; then
    local got
    got="$(sha256sum "$out" | cut -d' ' -f1)"
    if [ "$got" != "$sha" ]; then
      lde_err "$name: неверная контрольная сумма: $got != $sha"
      rm -f "$out"
      return 1
    fi
    lde_ok "$name: скачан и проверен sha256"
  fi
}

# ---------------------------------------------------------------- TinyTeX ---
install_tinytex() {
  if [ -x "$LDE_TEX_BIN/xelatex" ] && [ -x "$LDE_TEX_BIN/tlmgr" ]; then
    lde_ok "TinyTeX уже установлен: $LDE_TINYTEX_DIR"
    return 0
  fi
  net_available || { manual_hint; return 1; }
  local tar="$LDE_TOOLS_DIR/downloads/$TINYTEX_ASSET"
  download_verify "$TINYTEX_URL" "$tar" "$TINYTEX_SHA256" "TinyTeX ${TINYTEX_VERSION}" || return 1
  lde_info "распаковка TinyTeX в $LDE_TINYTEX_DIR"
  mkdir -p "$LDE_TINYTEX_DIR"
  local tmpd; tmpd="$(mktemp -d "$LDE_TOOLS_DIR/downloads/tinyt.XXXXXX")"
  tar -xJf "$tar" -C "$tmpd"
  # архив распаковывается в каталог .TinyTeX
  if [ -d "$tmpd/.TinyTeX" ]; then
    cp -a "$tmpd/.TinyTeX/." "$LDE_TINYTEX_DIR/"
  elif [ -d "$tmpd/TinyTeX" ]; then
    cp -a "$tmpd/TinyTeX/." "$LDE_TINYTEX_DIR/"
  else
    lde_err "неожиданная структура архива TinyTeX"; rm -rf "$tmpd"; return 1
  fi
  rm -rf "$tmpd"
  chmod +x "$LDE_TINYTEX_DIR"/bin/*/* 2>/dev/null || true
  lde_ok "TinyTeX распакован: $LDE_TINYTEX_DIR"
}

install_tex_packages() {
  export PATH="$LDE_TEX_BIN:$PATH"
  have tlmgr || { lde_err "tlmgr не найден — TinyTeX не установлен"; return 1; }
  # tlmgr требует актуальный updater; update --self идемпотентен
  net_available || { manual_hint; return 1; }
  lde_info "tlmgr: репозиторий + самообновление"
  tlmgr option repository "$TEXLIVE_REPOSITORY" || true
  tlmgr update --self || lde_warn "tlmgr update --self не удался, продолжаем"
  lde_info "tlmgr: установка TeX-пакетов (${#TEX_PACKAGES[@]} шт.)"
  if ! tlmgr install "${TEX_PACKAGES[@]}"; then
    lde_warn "tlmgr install вернул ошибку; проверяю ключевые пакеты ниже"
  fi
  # Проверка ключевых файлов
  local missing=0 f
  for f in fontspec.sty biblatex.sty biblatex-gost.def gost-numeric.bbx \
           standalone.cls extsizes.sty hyphen-russian.tex; do
    if ! kpsewhich "$f" >/dev/null 2>&1; then
      lde_err "TeX-файл не найден: $f (пакет не установился?)"; missing=1
    fi
  done
  [ "$missing" = 0 ] && lde_ok "ключевые TeX-пакеты присутствуют"
  return "$missing"
}

# ----------------------------------------------------------------- Pandoc ---
install_pandoc() {
  if [ -x "$LDE_PANDOC" ] && "$LDE_PANDOC" --version 2>/dev/null | head -1 | grep -q "^pandoc ${PANDOC_VERSION}".; then
    lde_ok "pandoc ${PANDOC_VERSION}: $LDE_PANDOC"
    return 0
  fi
  net_available || { manual_hint; return 1; }
  local tar="$LDE_TOOLS_DIR/downloads/$PANDOC_ASSET"
  download_verify "$PANDOC_URL" "$tar" "$PANDOC_SHA256" "pandoc ${PANDOC_VERSION}" || return 1
  local tmpd; tmpd="$(mktemp -d "$LDE_TOOLS_DIR/downloads/pandoc.XXXXXX")"
  tar -xzf "$tar" -C "$tmpd"
  mkdir -p "$(dirname "$LDE_PANDOC")"
  cp "$tmpd/pandoc-${PANDOC_VERSION}/bin/pandoc" "$LDE_PANDOC"
  chmod +x "$LDE_PANDOC"
  rm -rf "$tmpd"
  lde_ok "pandoc установлен: $LDE_PANDOC ($("$LDE_PANDOC" --version | head -1))"
}

# ------------------------------------------------------------------ venv ----
install_venv() {
  local py
  if [ -x "$LDE_VENV/bin/python" ] && \
     "$LDE_VENV/bin/python" -c 'import docx, lxml, pymupdf, PIL, yaml' 2>/dev/null; then
    lde_ok "python venv готов: $LDE_VENV"
    return 0
  fi
  net_available || { manual_hint; return 1; }
  if [ ! -x "$LDE_VENV/bin/python" ]; then
    lde_info "создание venv: $LDE_VENV"
    python3 -m venv "$LDE_VENV" || { lde_err "python3 -m venv не работает (нет python3-venv?)"; return 1; }
  fi
  lde_info "pip install -r toolchain/requirements.txt"
  "$LDE_VENV/bin/pip" install --quiet --upgrade pip || true
  if ! "$LDE_VENV/bin/pip" install --quiet -r "$LDE_ROOT/toolchain/requirements.txt"; then
    lde_err "pip install не удался (нет сети или нет wheel для вашей версии Python)"
    return 1
  fi
  lde_ok "python venv готов: $LDE_VENV"
}

# ------------------------------------------------------------------ main ----
rc=0
install_tinytex        || rc=1
[ "$rc" = 0 ] && { install_tex_packages || rc=1; }
install_pandoc         || rc=1
install_venv           || rc=1

if [ "$rc" = 0 ]; then
  "$SCRIPT_DIR/write-versions-lock.sh" || lde_warn "не удалось записать versions.lock"
  lde_ok "установка завершена"
else
  lde_err "установка завершена с ошибками; запустите doctor для диагностики"
fi
exit "$rc"
