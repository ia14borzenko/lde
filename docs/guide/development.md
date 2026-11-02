# Development

## Repository layout

| Path | What |
|---|---|
| `bin/lde` | launcher: runs `python -m lde` from the toolchain venv |
| `lde/` | the core: source parser (`textfmt.py`), project model, checker (`check.py`), LaTeX emitter, calculations (`calcs.py`), git-based versions and block merge, DOCX export, import (`importers/`), formatting profiles (`profiles/`), crypto, sync, sharing, MCP server (`mcp_server.py`), the desktop app integration (`app.py`) |
| `lde/tex/preamble.tex` | GOST 7.32 typesetting (XeLaTeX), with references to the clauses of the standard |
| `lde/studio/` | Studio: HTTP server (`server.py`, standard library) and the UI (`static/`: plain JS, no build step; MathLive vendored) |
| `convert/` | DOCX pipeline: pandoc reference document, post-processing, TOC page numbers |
| `qa/` | DOCX and PDF checks: GOST rules, PDF↔DOCX comparison, automatic repair |
| `lde-server/` | the review and sync server (Python standard library + SQLite, Docker) |
| `skill/lde/` | skill and reference texts for AI agents (Russian) |
| `template/example/` | the project created by `lde init` |
| `toolchain/` | installer, doctor, pinned versions (`pins.env`), Python requirements |
| `tests/` | unit, end-to-end, server and headless-browser UI tests |

## Principles

- **The source is the truth.** PDF and DOCX are always regenerated; nothing is edited in the output.
- **PDF (XeLaTeX) is canonical**; DOCX is an export that is checked against the PDF.
- **No hand formatting.** Profiles implement the standard; manual formatting is an explicit, visible exception.
- **Every change is a point in git.** Studio, the CLI and agents all write through the same history.
- **Few dependencies.** Python standard library wherever reasonable; no web frameworks; no CDN in the UI.

## Machine-local settings

`toolchain/local.env` (ignored by git) holds settings for one machine, e.g.:

```sh
LDE_TOOLS_DIR=/data/lde-tools    # TinyTeX, downloads, cache (default: ~/.local/share/lde-tools)
```

`toolchain/versions.lock` is written by `lde doctor` with the versions actually installed (also ignored).

## Tests

Tests never write to your home directory (configuration goes to temporary folders) and never open
windows: UI tests drive headless Chrome over the DevTools protocol (`tests/ui/cdp.py`).

```sh
P=toolchain/venv/bin/python
$P -m unittest tests/unit/test_core.py     # parser, numbers, typography
$P tests/smoke.py                          # end-to-end on `lde init`: check, fix, status, submit, undo
$P tests/test_conflict.py                  # concurrent edits
$P tests/server/test_mcp.py                # MCP protocol, security, tools
$P tests/server/test_share_e2e.py          # review links through a real server
$P tests/ui/test_formula_inserts.py        # Studio UI (headless Chrome); see tests/ui/ for more
make test                                  # the quick set
```

Screenshots for the documentation are generated on a fresh demo project:

```sh
$P tests/ui/screens.py                     # → docs/screens/
```

## Conventions

- User-facing texts (UI, messages, CLI help) are in Russian; code identifiers are in English.
- Messages describe the problem in plain language with the place and a hint; rule codes are only in JSON.
- New UI behaviour comes with a headless UI test; fixes come with a test that fails without them.
