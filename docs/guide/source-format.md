# Source format

A report's text lives in `text/*.md` — Markdown with a few additions for numbered objects and *inserts*.
Studio edits these files for you, but they are plain text: you can edit them in any editor, diff and
review them. LDE picks up external changes automatically.

The complete reference (in Russian, written for authors and agents) is
[`skill/lde/references/format.md`](../../skill/lde/references/format.md). This page is an English summary.

## Files and order

Section files are read by name (`01-…`, `02-…`) or in the order of `files` in `lde.toml`.
Blocks are separated by a blank line. Keep each paragraph on one line — diffs and merges work per paragraph.

## Blocks

| Block | Syntax |
|---|---|
| Section / subsection / item | `# Title {#sec:name}`, `## …`, `### …` — LDE numbers them |
| Unnumbered structural element | `# Введение {-}` (Introduction), `# Заключение {-}` (Conclusion) |
| Appendix | `# Title {.appendix #app:name}` → "ПРИЛОЖЕНИЕ А", objects numbered А.1, А.2 … |
| Paragraph | any text |
| Display equation | `$$ LaTeX $$ {#eq:name}` — numbered; without `{#eq:…}` — unnumbered |
| Figure | `![Caption](path){#fig:name width=0.8}`, path in `figures/…` or `calc/<calc>/out/…` |
| Figure with sub-figures | `::: figure {#fig:name}` … `![а) …](a.png)` … `![б) …](b.png)` … common caption … `:::` |
| Table | `: Caption {#tab:name}` followed by `| … |` rows (second row `|---|---|`) |
| Table from CSV | `: Caption {#tab:name file=calc/x/out/t.csv}` |
| List | `- item` (dash per GOST), `1) item`, `а) item`; nest with two spaces |
| Code listing | fenced block with a language |
| Comment (not printed) | `<!-- … -->` |
| Page break | `::: pagebreak` |

A paragraph that follows a display equation and starts with «где» ("where") is typeset as the
explanation of symbols: no indent, symbols aligned under each other.

## Inserts

Inserts are what makes a report consistent. Never type a symbol, a computed number or a figure number
by hand — `lde check` will flag it and offer a fix.

| Insert | Example | Result |
|---|---|---|
| Quantity | `{R_ц}` | *R*ц — italic, upright Cyrillic index, same everywhere |
| Number from a calculation | `{=afc.f_c}` | "1592 Гц" — unit and precision from `calc.toml` |
| … in other units | `{=afc.f_c кГц}` | the value in kHz (SI prefixes only) |
| … with precision | `{=afc.K .3}` (decimals), `{=x .3g}` (significant digits) | |
| … without unit | `{=afc.N -}` | |
| Reference | `@fig:x`, `@tab:x`, `@sec:x`, `@app:x` → number; `@eq:x` → "(N)" | you write the word ("рисунке") yourself |
| Citation | `[@key]`, `[@key, с. 45]`, `[@a; @b]` | "[3]", "[3, с. 45]", "[3, 4]" |
| Inline equation | `$\alpha = \lambda / d$` | |
| Bold / italic | `**…**` / `*…*` | |
| Escapes | `\{` `\}` `\$` `\@` `\*` | literal characters |

Inside equations the same inserts are commands: `\q{R_ц}` for a quantity and `\val{afc.f_c}` /
`\val{afc.f_c кГц}` for a number:

```latex
$$ f_{\text{ср}} = \frac{1}{2\pi \q{R} \q{C}} = \val{afc.f_c} $$ {#eq:fc}
```

`lde list values <calc>` prints every available number insert; `lde list labels` — all labels.

## Manual formatting of one block

A `{style …}` line directly above a block overrides its formatting (PDF and DOCX). Use sparingly —
LDE stops auto-formatting that block.

```text
{style align=center indent=0 before=6 after=-2 line=1 size=12 bold=no italic=yes keep=yes newpage=yes}
A manually formatted paragraph.
```

## Project files

| File | Contents |
|---|---|
| `lde.toml` | `[document]`: `profile` (`gost-7.32`, `guap`), `numbering` (`continuous` / `section`), `block_spacing` (`none` / `line`), `hyphenation`, which lists to print; `[title]`: title page fields |
| `quantities.toml` | `["R_ц"]` with `meaning`, `unit`, optional `latex` |
| `abbreviations.toml` | `"РСА" = "радиолокатор с синтезированной апертурой"` |
| `refs.bib` | BibTeX; optional `formatted` (ready GOST description) and `file` (attachments in `sources/<key>/`) |
| `calc/<key>/` | a calculation — see [Calculations](calculations.md) |
| `figures/`, `data/` | static figures and input data |
| `documents/<key>/` | another document of the same project (`document.toml` + `*.md`), sharing quantities, sources and calculations; commands take `-D <key>` |
| `out/` | generated PDF, DOCX and check reports — not in git |

Example `lde.toml`:

```toml
[document]
name = "report"            # out/report.pdf, out/report.docx
profile = "gost-7.32"
numbering = "continuous"   # or "section": 2.1, 2.2 …
block_spacing = "none"     # or "line": an empty line around equations, figures, tables
hyphenation = true         # false: no hyphenation in PDF and DOCX
toc = true
abbreviations = true
symbols = false
bibliography = true

[title]
org = "Организация-исполнитель"
topic = "Исследование RC-фильтра нижних частот"
author = "И. И. Иванов"
year = "2026"
```
