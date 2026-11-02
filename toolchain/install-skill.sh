#!/usr/bin/env bash
# toolchain/install-skill.sh — идемпотентно установить DSH-скилл `lde`
# символической ссылкой ~/.dsh/skills/lde -> <repo>/skill/lde и проверить frontmatter.
#
# Использование:
#   toolchain/install-skill.sh           # установить/обновить симлинк
#   toolchain/install-skill.sh --check   # только проверить (exit 1, если не установлен)
#   toolchain/install-skill.sh --force   # перезаписать существующий чужой каталог (опасно, по умолчанию нельзя)
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LDE_ROOT
. "$SCRIPT_DIR/env.env"
. "$SCRIPT_DIR/common.sh"

SKILL_SRC="$LDE_ROOT/skill/lde"
DSH_HOME="${DSH_HOME:-$HOME/.dsh}"
SKILLS_DIR="$DSH_HOME/skills"
SKILL_DST="$SKILLS_DIR/lde"

MODE="install"
for arg in "$@"; do
  case "$arg" in
    --check) MODE="check" ;;
    --force) MODE="force" ;;
    -h|--help) sed -n '2,10p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
    *) lde_die "install-skill.sh: неизвестный аргумент: $arg" ;;
  esac
done

validate_skill() {
  local py; py="$(lde_python)"
  local md="$SKILL_SRC/SKILL.md"
  [ -f "$md" ] || { lde_err "нет $md"; return 1; }
  "$py" - "$md" <<'PY'
import sys, re
try:
    import yaml
except Exception as exc:  # PyYAML обязателен (toolchain/requirements.txt)
    print(f"FAIL: PyYAML недоступен: {exc}")
    sys.exit(1)
path = sys.argv[1]
text = open(path, encoding='utf-8').read()
m = re.match(r'---\s*\n(.*?)\n---\s*\n', text, re.S)
if not m:
    print("FAIL: нет YAML frontmatter (--- ... ---) в начале SKILL.md")
    sys.exit(1)
try:
    fm = yaml.safe_load(m.group(1)) or {}
except Exception as exc:
    print(f"FAIL: frontmatter не парсится как YAML: {exc}")
    sys.exit(1)
errors = []
if not isinstance(fm, dict):
    errors.append("frontmatter должен быть YAML-объектом")
else:
    name = fm.get('name')
    desc = fm.get('description')
    if name != 'lde':
        errors.append(f"name должен быть 'lde' (kebab-case), получено: {name!r}")
    if not desc or not str(desc).strip():
        errors.append("не заполнен обязательный description")
    if 'disable-model-invocation' in fm:
        errors.append("disable-model-invocation не должен выставляться (скилл виден модели)")
    if 'user-invocable' in fm and fm.get('user-invocable') is False:
        errors.append("user-invocable: false не должен выставляться (скилл виден человеку)")
if errors:
    for e in errors: print("FAIL:", e)
    sys.exit(1)
print(f"OK: frontmatter валиден (name={fm.get('name')}, fields={sorted(fm)})")
PY
}

print_status() {
  echo "DSH_HOME:    $DSH_HOME"
  echo "skills dir:  $SKILLS_DIR"
  echo "target:      $SKILL_DST"
  if [ -L "$SKILL_DST" ]; then
    echo "status:      симлинк -> $(readlink -f "$SKILL_DST" 2>/dev/null || readlink "$SKILL_DST")"
    if [ "$(readlink -f "$SKILL_DST" 2>/dev/null || true)" = "$(readlink -f "$SKILL_SRC")" ]; then
      echo "owned:       наш симлинк (lde-dev)"
      return 0
    fi
    echo "owned:       ЧУЖОЙ симлинк (не наш)"; return 1
  elif [ -e "$SKILL_DST" ]; then
    echo "status:      существует и это НЕ симлинк (чужой скилл)"; return 1
  else
    echo "status:      не установлен"; return 1
  fi
}

case "$MODE" in
  check)
    validate_skill || exit 1
    print_status || exit 1
    ;;
  install|force)
    validate_skill || lde_die "frontmatter скилла невалиден — установка отменена"
    mkdir -p "$SKILLS_DIR"
    if [ -e "$SKILL_DST" ] || [ -L "$SKILL_DST" ]; then
      if [ -L "$SKILL_DST" ] && \
         [ "$(readlink -f "$SKILL_DST" 2>/dev/null || true)" = "$(readlink -f "$SKILL_SRC")" ]; then
        lde_ok "симлинк уже установлен: $SKILL_DST -> $SKILL_SRC"
      elif [ "$MODE" = "force" ]; then
        lde_warn "--force: удаляю существующий $SKILL_DST"
        rm -rf "$SKILL_DST"
        ln -s "$SKILL_SRC" "$SKILL_DST"
        lde_ok "симлинк перезаписан: $SKILL_DST"
      else
        lde_err "$SKILL_DST уже существует и это не наш симлинк."
        lde_err "Не перезаписываю (защита чужого скилла). Если это правда наш —"
        lde_err "удалите каталог вручную или запустите с --force."
        exit 2
      fi
    else
      ln -s "$SKILL_SRC" "$SKILL_DST"
      lde_ok "скилл установлен: $SKILL_DST -> $SKILL_SRC"
    fi
    print_status || true
    ;;
esac
