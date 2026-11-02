# Command line

Everything Studio does is also available as `lde` commands — convenient for scripts, CI and agents.
`lde --help` prints the list (in Russian).

**Project folder:** `-C <folder>`, the `LDE_PROJECT` variable, or the nearest folder upwards with `lde.toml`.
**Document of a multi-document project:** `-D <key>` (default: the main document in `text/`).

## Projects and the app

| Command | Description |
|---|---|
| `lde init <folder>` | new report from the example (built right away) |
| `lde import-docx <file.docx> <folder>` | project from an existing Word report; notes in `import/report.md` |
| `lde` / `lde open [<folder>]` | open LDE Studio in its own window; if running — a new window of the same Studio |
| `lde studio [<folder>] [--port N] [--no-browser]` | run Studio in this terminal (debugging) |
| `lde app install \| uninstall \| status` | `lde` command in `~/.local/bin` and the menu launcher |
| `lde mcp [<folder>] [--port N] [--stdio] [--new-key]` | MCP server for AI agents |
| `lde new document <key> --title "…"` | another document of the project, sharing quantities, sources, calculations |

## Writing

| Command | Description |
|---|---|
| `lde new quantity <symbol> --meaning "…" --unit …` | declare a quantity |
| `lde new abbr <abbr> --meaning "…"` | add an abbreviation |
| `lde new source …` | add a bibliography entry |
| `lde new calc <key>` | new calculation with a script skeleton |
| `lde new figure \| table \| formula \| section` | print a ready-to-paste sample |
| `lde list quantities \| values [<calc>] \| labels \| sources \| calcs \| documents` | what the project has |
| `lde info <label \| calc>` | provenance of a figure or number: script, parameters, time, log |

## Checking and building

| Command | Description |
|---|---|
| `lde status` | what is stale and why |
| `lde check [--json] [--all]` | the checker: errors and warnings with file, line and a suggested fix |
| `lde fix [<rules>]` | apply the suggested fixes |
| `lde update [<calc>…]` | recompute stale calculations in dependency order |
| `lde build` | build the PDF (`out/<name>.pdf`) |
| `lde docx` | export DOCX, check it against the PDF and repair known defects |
| `lde submit` | all of the above → "ready to submit?" |

## History

| Command | Description |
|---|---|
| `lde save -m "…"` | record a point (a git commit) |
| `lde undo` / `lde redo` | undo the last action (repeat for earlier ones) / redo |
| `lde version "name" [-m …]` | named version (set automatically by `submit` and when sharing) |
| `lde versions`, `lde history` | versions; all points by session |
| `lde diff <version> [<version>]` | changes by element, word-level; without the second — up to now |
| `lde restore <version> [--only <files>]` | return the project (or files) to a version, as a new point |
| `lde merge [--resolve <uid> ours\|theirs\|both]` | resolve block-level merge conflicts |

## Server, review, sync

| Command | Description |
|---|---|
| `lde server login <url> <name>` | connect to an LDE server |
| `lde share new \| list \| pull \| accept \| reject \| own \| reply` | review links and the reviewer's edits |
| `lde review import <file.docx>`, `lde review` | comments from a Word file |
| `lde publish`, `lde push`, `lde pull`, `lde sync` | encrypted project on the server, sync with block-level merge |
| `lde clone <id> <folder>`, `lde remote list \| member …` | get a project; co-authors |

## Import from RDE

`lde import-rde <rde-project> <folder>` — one-time migration from RDE, LDE's predecessor
(calculations, quantities, sources, inserts).

## Agent feedback and service

| Command | Description |
|---|---|
| `lde feedback check "…"` | does LDE already have it, and which command |
| `lde feedback new --kind bug\|missing\|routine\|docs\|ux --title "…" …` | file a report |
| `lde feedback analyze \| list \| show <id> \| status <id> <status>` | routine detection; the inbox |
| `lde doctor [--fix]` | check / install the toolchain |
| `lde skill-status` | is the agent skill installed |
