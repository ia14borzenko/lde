#!/usr/bin/env bash
# scripts/clean-build.sh — очистка артефактов сборки LDE.
#
#   scripts/clean-build.sh          # артефакты проектов, логи, reference.docx, out/
#   scripts/clean-build.sh --all    # + python venv (пересоздаётся `lde doctor --fix`)
#
# Никогда не удаляет: исходники, шаблон, docs, тулчейн (TinyTeX/pandoc),
# чужие каталоги. Не требует sudo. Работает только внутри репозитория LDE.
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
cd "$ROOT"

ALL=0
[ "${1:-}" = "--all" ] && ALL=1

echo "LDE clean-build: $ROOT"

# 1) артефакты внутри репозитория (demo-проекты, эксперименты, логи)
for d in demo/*/out experiments/formulas/out; do
  [ -d "$d" ] && rm -rf "$d" && echo "  удалено: $d"
done
[ -d logs ] && find logs -maxdepth 1 -type f -name '*.log' -delete && echo "  очищены: logs/*.log"

# 2) сгенерированные файлы конвейера
[ -f convert/reference.docx ] && rm -f convert/reference.docx && echo "  удалён: convert/reference.docx"
for f in template/gost-report/out; do
  [ -d "$f" ] && rm -rf "$f" && echo "  удалено: $f"
done

# 3) мусор python
find . -path ./toolchain/venv -prune -o -type d -name __pycache__ -print -exec rm -rf {} + 2>/dev/null || true
find . -path ./toolchain/venv -prune -o -type f -name '*.pyc' -delete 2>/dev/null || true

# 4) --all: пользовательские установки в репозитории
if [ "$ALL" = 1 ]; then
  if [ -d toolchain/venv ]; then
    rm -rf toolchain/venv
    echo "  удалён: toolchain/venv (пересоздать: bin/lde doctor --fix)"
  fi
fi

echo "готово."
