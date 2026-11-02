#!/usr/bin/env bash
# convert/latex2docx.sh — конвейер LaTeX -> DOCX (ГОСТ) с постобработкой и отчётом.
#
#   latex2docx.sh --project <dir> [--math=omml|svg|auto] [--out <file>] [--no-render]
#
# Шаги:
#   1) prepare_docx_source.py: sections -> out/docx-source.tex + план (статические номера)
#   2) build_reference_docx.py: reference.docx (ГОСТ-стили; идемпотентно)
#   3) pandoc: LaTeX -> DOCX (+citeproc/CSL ГОСТ, + math2svg.lua при --math=svg)
#   4) postprocess_docx.py: титул, СОДЕРЖАНИЕ, keepNext, таблицы, номера формул, библиография
#   5) LibreOffice: рендер DOCX -> PDF, поиск номеров страниц заголовков
#   6) повторная постобработка с настоящими номерами страниц; финальный рендер для QA
#   7) out/main.docx.report.json — что сделано/предупреждения
set -euo pipefail

SCRIPT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LDE_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
export LDE_ROOT
# shellcheck source=../toolchain/env.env
. "$LDE_ROOT/toolchain/env.env"
# shellcheck source=../toolchain/common.sh
. "$LDE_ROOT/toolchain/common.sh"
export PATH="$LDE_TEX_BIN:$HOME/.local/bin:$PATH"

PROJECT=""
MATH_MODE="${LDE_MATH_MODE:-omml}"
OUT_FILE=""
DO_RENDER=1
while [ $# -gt 0 ]; do
  case "$1" in
    --project) PROJECT="${2:?--project требует каталога}"; shift 2 ;;
    --math=*) MATH_MODE="${1#--math=}"; shift ;;
    --math) MATH_MODE="${2:?--math требует значения}"; shift 2 ;;
    --out) OUT_FILE="${2:?--out требует файла}"; shift 2 ;;
    --no-render) DO_RENDER=0; shift ;;
    -h|--help) sed -n '2,12p' "${BASH_SOURCE[0]}" | sed 's/^# \{0,1\}//' >&2; exit 0 ;;
    *) lde_die "latex2docx.sh: неизвестный аргумент: $1" ;;
  esac
done
[ -n "$PROJECT" ] || lde_die "использование: latex2docx.sh --project <dir> [--math=omml|svg|auto] [--out FILE]"
PROJECT="${PROJECT/#\~/$HOME}"
PROJECT="$(cd "$PROJECT" && pwd)"
[ -f "$PROJECT/main.tex" ] || lde_die "в $PROJECT нет main.tex"

case "$MATH_MODE" in
  omml|svg) ;;
  auto) MATH_MODE="${LDE_MATH_MODE:-omml}" ;;
  *) lde_die "--math: ожидается omml|svg|auto, получено: $MATH_MODE" ;;
esac

PY="$(lde_python)"
[ -x "$PY" ] || lde_die "python venv не найден: запустите bin/lde doctor --fix"
PANDOC="$LDE_PANDOC"
[ -x "$PANDOC" ] || lde_die "pandoc не найден: запустите bin/lde doctor --fix"
have xelatex || lde_die "XeLaTeX не найден: запустите bin/lde doctor --fix"

OUT_DIR="$PROJECT/out"
TMP_DIR="$OUT_DIR/.docx-build"
REF_DOCX="$LDE_ROOT/convert/reference.docx"
CSL="$LDE_ROOT/convert/csl/gost-r-7-0-5-2008-num-appear.csl"
LOG="$(mkfile="$PROJECT/logs"; mkdir -p "$mkfile"; echo "$mkfile/docx-$(date +%Y%m%d-%H%M%S).log")"

mkdir -p "$OUT_DIR" "$TMP_DIR" "$PROJECT/logs"
exec > >(tee -a "$LOG") 2>&1
lde_info "DOCX-конвейер: проект=$PROJECT, math=$MATH_MODE, лог=$LOG"

# ---------------------------------------------------------------- steps ------
lde_info "[1/7] разбор LaTeX-диалекта и статическая нумерация"
"$PY" "$LDE_ROOT/convert/prepare_docx_source.py" \
  --project "$PROJECT" \
  --out "$OUT_DIR/docx-source.tex" \
  --plan "$OUT_DIR/docx-source.plan.json"

lde_info "[2/7] reference.docx (ГОСТ-стили)"
"$PY" "$LDE_ROOT/toolchain/build_reference_docx.py" --out "$REF_DOCX" >/dev/null
lde_ok "reference.docx: $REF_DOCX"

lde_info "[3/7] pandoc: LaTeX -> DOCX (math=$MATH_MODE)"
PANDOC_ARGS=(
  "$OUT_DIR/docx-source.tex"
  -f latex -t docx
  --reference-doc="$REF_DOCX"
  --citeproc
  --bibliography="$PROJECT/refs.bib"
  --csl="$CSL"
  --resource-path="$PROJECT:$PROJECT/figures:$LDE_ROOT"
  -o "$TMP_DIR/main.raw.docx"
)
if [ "$MATH_MODE" = "svg" ]; then
  export LDE_DOCX_MATH_DIR="$OUT_DIR/math-svg"
  PANDOC_ARGS+=(--lua-filter="$LDE_ROOT/convert/math2svg.lua")
fi
if ! "$PANDOC" "${PANDOC_ARGS[@]}"; then
  lde_err "pandoc завершился с ошибкой"
  exit 1
fi
lde_ok "pandoc: $TMP_DIR/main.raw.docx"

lde_info "[4/7] постобработка DOCX (титул, СОДЕРЖАНИЕ, keepNext, таблицы, формулы)"
"$PY" "$LDE_ROOT/convert/postprocess_docx.py" \
  --docx "$TMP_DIR/main.raw.docx" \
  --plan "$OUT_DIR/docx-source.plan.json" \
  --out "$TMP_DIR/main.pass1.docx" \
  --math "$MATH_MODE" \
  --report "$TMP_DIR/postprocess-pass1.json"

if [ "$DO_RENDER" = 1 ]; then
  have soffice || lde_die "LibreOffice (soffice) не найден: нужен для рендера и QA. bin/lde doctor --fix"

  render_docx_pdf() {  # $1 = docx, $2 = pdf; рендер во временном каталоге,
    # чтобы НЕ перезаписывать LaTeX-PDF out/main.pdf
    local d="$1" p="$2" rdir produced
    rm -f "$p"
    rdir="$(mktemp -d "$TMP_DIR/render.XXXXXX")"
    timeout 300 soffice --headless --convert-to pdf --outdir "$rdir" "$d" >/dev/null 2>&1 || true
    produced="$rdir/$(basename "${d%.*}").pdf"
    if [ ! -f "$produced" ]; then rm -rf "$rdir"; return 1; fi
    mv "$produced" "$p"
    rm -rf "$rdir"
  }

  lde_info "[5/7] рендер DOCX -> PDF (LibreOffice) для номеров страниц"
  if ! render_docx_pdf "$TMP_DIR/main.pass1.docx" "$TMP_DIR/pass1.pdf"; then
    lde_err "LibreOffice не смог отрендерить DOCX в PDF"
    exit 1
  fi
  "$PY" "$LDE_ROOT/convert/find_toc_pages.py" \
    --pdf "$TMP_DIR/pass1.pdf" \
    --plan "$OUT_DIR/docx-source.plan.json" \
    --out "$OUT_DIR/docx-toc-pages.json" || true

  lde_info "[6/7] постобработка с номерами страниц СОДЕРЖАНИЯ"
  FINAL_DOCX="$OUT_DIR/main.docx"
  [ -n "$OUT_FILE" ] && FINAL_DOCX="$OUT_FILE"
  "$PY" "$LDE_ROOT/convert/postprocess_docx.py" \
    --docx "$TMP_DIR/main.raw.docx" \
    --plan "$OUT_DIR/docx-source.plan.json" \
    --toc-pages "$OUT_DIR/docx-toc-pages.json" \
    --out "$FINAL_DOCX" \
    --math "$MATH_MODE" \
    --report "$TMP_DIR/postprocess-pass2.json"

  # финальный рендер для QA (не перезаписывает LaTeX-PDF: имя main.docx.pdf)
  lde_info "[7/7] финальный рендер DOCX -> PDF для сравнения"
  if render_docx_pdf "$FINAL_DOCX" "$OUT_DIR/main.docx.pdf"; then
    lde_ok "рендер: $OUT_DIR/main.docx.pdf"
    "$PY" "$LDE_ROOT/convert/find_toc_pages.py" \
      --pdf "$OUT_DIR/main.docx.pdf" \
      --plan "$OUT_DIR/docx-source.plan.json" \
      --out "$TMP_DIR/toc-pages-final.json" || true
  else
    lde_warn "финальный рендер не удался (QA сравнение PDF будет недоступно)"
  fi
else
  FINAL_DOCX="$OUT_DIR/main.docx"
  [ -n "$OUT_FILE" ] && FINAL_DOCX="$OUT_FILE"
  cp -f "$TMP_DIR/main.pass1.docx" "$FINAL_DOCX"
fi

# ---------------------------------------------------------------- report -----
"$PY" - "$LDE_ROOT" "$OUT_DIR" "$TMP_DIR" "$FINAL_DOCX" "$MATH_MODE" <<'PY'
import json, sys, datetime, os
from pathlib import Path
lde_root, out_dir, tmp_dir, final_docx, math_mode = sys.argv[1:6]
out_dir = Path(out_dir); tmp_dir = Path(tmp_dir)
plan = json.loads((out_dir / "docx-source.plan.json").read_text(encoding="utf-8"))
def load(p, default):
    try: return json.loads(Path(p).read_text(encoding="utf-8"))
    except Exception: return default
p1 = load(tmp_dir / "postprocess-pass1.json", {})
p2 = load(tmp_dir / "postprocess-pass2.json", {})
toc = load(out_dir / "docx-toc-pages.json", {})
toc_final = load(tmp_dir / "toc-pages-final.json", {})
render_pdf = out_dir / "main.docx.pdf"
report = {
    "artifact": str(Path(final_docx)),
    "generated_by": "convert/latex2docx.sh",
    "created": datetime.datetime.now().isoformat(timespec="seconds"),
    "math_mode": math_mode,
    "source": str(out_dir / "docx-source.tex"),
    "plan": str(out_dir / "docx-source.plan.json"),
    "counters": plan.get("counters", {}),
    "toc_pages": toc.get("map", {}),
    "toc_missing": toc.get("missing", []),
    "render_pdf": str(render_pdf) if render_pdf.exists() else None,
    "render_pages": toc_final.get("pages_total"),
    "toc_check_final": {
        "found": toc_final.get("found"),
        "missing": toc_final.get("missing", []),
        "consistent": (toc_final.get("map", {}) == toc.get("map", {}))
            if toc_final else None,
    },
    "postprocess": {
        "steps": p2.get("steps", p1.get("steps", [])),
        "counts": p2.get("counts", p1.get("counts", {})),
        "warnings": sorted(set(p1.get("warnings", []) + p2.get("warnings", []))),
    },
    "converter_warnings": plan.get("warnings", []),
    "status": "ok"
        if (not plan.get("warnings") and not p2.get("warnings")
            and not toc.get("missing"))
        else "warnings",
}
rp = out_dir / "main.docx.report.json"
rp.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
print(f"отчёт: {rp}")
cs = report["counters"]
print(f"  разделов={cs.get('sections')}, рисунков={cs.get('figures')}, "
      f"таблиц={cs.get('tables')}, формул={cs.get('equations')}, "
      f"приложений={cs.get('appendices')}")
print(f"  статус: {report['status']}")
for w in report["postprocess"]["warnings"][:10]:
    print(f"  ! {w}")
PY

lde_ok "DOCX: $FINAL_DOCX"
[ -f "$OUT_DIR/main.docx.pdf" ] && lde_info "PDF-рендер DOCX (для QA): $OUT_DIR/main.docx.pdf"
