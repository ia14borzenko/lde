-- math2svg.lua — pandoc Lua-фильтр: display-формулы -> SVG (+PNG в постобработке).
--
-- Режим `lde docx --math=svg`: каждая ВЫКЛЮЧЕННАЯ (display) формула рендерится
-- настоящим XeLaTeX (standalone + dvisvgm --no-fonts) в векторный SVG, поэтому
-- типографика формул в DOCX совпадает с PDF-версией. Inline-формулы остаются
-- нативными OMML: у inline-картинок в Word нет вертикального смещения и они
-- «плывут» относительно базовой линии (см. docs/formulas.md).
--
-- Каталог для SVG задаётся переменной окружения LDE_DOCX_MATH_DIR (или
-- <project>/out/math-svg). Пути к файлам попадают в DOCX как out/.../eq-N.svg.
--
-- Требования: xelatex, dvisvgm (входят в TinyTeX), Pandoc >= 3.0.

local math_dir = os.getenv("LDE_DOCX_MATH_DIR") or "out/math-svg"
local counter = 0
local failed = 0

local snip_head = [[
\documentclass[border=2pt]{standalone}
\usepackage{fontspec}
\IfFontExistsTF{Times New Roman}{\setmainfont{Times New Roman}}{\setmainfont{texgyretermes}}
\usepackage{amsmath,mathtools,amssymb}
\usepackage{unicode-math}
\IfFontExistsTF{XITS Math}{\setmathfont{XITS Math}}{\setmathfont{texgyretermes-math.otf}}
\begin{document}
$\displaystyle ]]

local snip_tail = [[
$
\end{document}
]]

local function ensure_dir(path)
  os.execute(string.format("mkdir -p %q", path))
end

local function clean_tex(tex)
  -- pandoc иногда отдаёт display Math вместе с \begin{equation}...\end{equation}
  -- снимаем только внешние обёртки (внутренние env вроде pmatrix не трогаем)
  tex = tex:gsub("^%s*\\begin%s*{equation%*}", "")
  tex = tex:gsub("^%s*\\begin%s*{equation}", "")
  tex = tex:gsub("^%s*\\begin%s*{displaymath}", "")
  tex = tex:gsub("\\end%s*{equation%*}%s*$", "")
  tex = tex:gsub("\\end%s*{equation}%s*$", "")
  tex = tex:gsub("\\end%s*{displaymath}%s*$", "")
  tex = tex:gsub("\\%[", ""):gsub("\\%]", "")
  tex = tex:gsub("\\label%s*{[^}]*}", "")
  tex = tex:gsub("^%s*%$%$", ""):gsub("%$%$%s*$", "")
  -- пустая строка внутри math-режима в TeX запрещена: схлопываем пробелы
  tex = tex:gsub("%s+", " ")
  return tex
end

local function render_svg(tex)
  counter = counter + 1
  tex = clean_tex(tex)
  ensure_dir(math_dir)
  local base = string.format("%s/eq-%03d", math_dir, counter)
  local texf = base .. ".tex"
  local fh = io.open(texf, "w")
  if not fh then
    io.stderr:write("math2svg: не могу открыть " .. texf .. "\n")
    return nil
  end
  fh:write(snip_head, tex, "\n", snip_tail)
  fh:close()
  local log = base .. ".xelatex.log"
  -- -no-pdf -> XDV; dvisvgm читает XDV напрямую (без Ghostscript/mutool,
  -- которые нужны режиму dvisvgm --pdf)
  local ok = os.execute(string.format(
    "xelatex -no-pdf -interaction=nonstopmode -halt-on-error -output-directory=%q %q > %q 2>&1",
    math_dir, texf, log))
  if ok ~= true and ok ~= 0 then
    io.stderr:write("math2svg: xelatex упал: " .. texf .. "\n")
    failed = failed + 1
    return nil
  end
  local xdv = base .. ".xdv"
  local svg = base .. ".svg"
  local ok2 = os.execute(string.format(
    "dvisvgm --no-fonts --exact-bbox -o %q %q >> %q 2>&1",
    svg, xdv, log))
  if ok2 ~= true and ok2 ~= 0 then
    io.stderr:write("math2svg: dvisvgm упал: " .. xdv .. "\n")
    failed = failed + 1
    return nil
  end
  -- PNG для DOCX: XDV -> PDF (xdvipdfmx) -> PNG 600dpi (poppler pdftoppm).
  -- ImageMagick-растеризация SVG теряет часть глифов dvisvgm, а ImageMagick
  -- требуется ещё и для fallback; poppler даёт полный и точный растр.
  local pdf = base .. ".pdf"
  local png = base .. ".png"
  os.execute(string.format("xdvipdfmx -q -o %q %q >> %q 2>&1",
                           pdf, xdv, log))
  os.execute(string.format("pdftoppm -png -r 600 -singlefile %q %q >> %q 2>&1",
                           pdf, base, log))
  local fh = io.open(png, "rb")
  if fh then
    fh:close()
  else
    io.stderr:write("math2svg: PNG не создан: " .. png .. "\n")
    failed = failed + 1
    return nil
  end
  return png
end

function Math(el)
  if el.mathtype ~= "DisplayMath" then
    return nil  -- inline: остаётся OMML (baseline)
  end
  local svg = render_svg(el.text)
  if not svg then
    return nil  -- ошибка рендера: откат на OMML, предупреждение в stderr
  end
  return pandoc.Image({}, svg)
end

function Pandoc(doc)
  if failed > 0 then
    io.stderr:write(string.format(
      "math2svg: %d формул не удалось отрендерить (использован OMML)\n", failed))
  end
  io.stderr:write(string.format("math2svg: display-формул в SVG: %d\n", counter))
  return doc
end
