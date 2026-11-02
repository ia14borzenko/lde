# Calculations

A calculation is a script with parameters whose results — figures, numbers, tables — appear in the
report and stay in sync with it. Change a parameter, and LDE knows exactly what became stale.

## Anatomy

```text
calc/afc/
├── calc.toml     # title, script, parameters, units of output values
├── script.py     # the script
└── out/          # results (generated, not in git)
```

```toml
# calc/afc/calc.toml
title = "АЧХ RC-фильтра"
script = "script.py"
timeout = 120
inputs = ["data/measurements.csv"]       # project files the result depends on (optional)
outputs = ["afc.png", "values.json"]     # what the script must produce in out/

[params]                                 # editable in Studio
R = { value = 1000.0, unit = "Ом", title = "Сопротивление" }
C = { value = 1.0e-7, unit = "Ф", title = "Ёмкость" }

[values]                                 # units and precision of numbers from values.json
f_c = { unit = "Гц", title = "Частота среза" }
K_at_fc = { digits = 3, title = "Коэффициент передачи на частоте среза" }
```

```python
# calc/afc/script.py
import math
import lde_calc

p = lde_calc.params()                         # {"R": 1000.0, "C": 1e-07}
f_c = 1 / (2 * math.pi * p["R"] * p["C"])

# … plot with matplotlib …
fig.savefig(lde_calc.out("afc.png"), dpi=200)

lde_calc.save_values(f_c=f_c, K_at_fc=1 / math.sqrt(2))   # → out/values.json
data = lde_calc.path("data/measurements.csv")              # path to a project file
```

`lde new calc <key>` creates a calculation with this skeleton.

## Using results in the text

| What | How |
|---|---|
| A figure | `![Caption](calc/afc/out/afc.png){#fig:afc}` — the dependency is known from the path |
| A number | `{=afc.f_c}`; nested JSON keys with dots: `{=csa.migration.diff_edge_m}` |
| A parameter | `{=afc.R}` — the value of the last successful run |
| In an equation | `\val{afc.f_c}` |
| A table | `: Caption {#tab:x file=calc/afc/out/table.csv}` |

`lde list values afc` lists every number with its ready-to-paste insert.

## Staleness and updates

A result is up to date when the script, its parameters and its input files are unchanged since the
last run (by content, not by timestamps). Otherwise:

- the PDF marks stale figures and numbers in red;
- `lde status` (and the Studio status pill) explains why: *"parameter R changed: 1000 → 2000"*;
- `lde update` recomputes only what is stale, in dependency order (`lde update afc` — one calculation).

Scripts run in a copy of the calculation folder with a timeout and, where the system allows unprivileged
user namespaces, without network access (`network = true` in `calc.toml` allows it). If a script fails,
the previous outputs are kept and the error with its log is shown.

`lde info afc` or `lde info fig:afc` shows provenance: script, parameters, run time, log.

## Python

By default the system `python3` runs the scripts (so your usual numpy / matplotlib / scipy are available).
Set `python = "/path/to/python"` in `calc.toml` to use another interpreter, e.g. a project venv.

Conventions for figures in GOST reports: axis labels in Russian, a serif font, no title above the plot
(the caption is in the text).
