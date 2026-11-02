"""АЧХ RC-фильтра нижних частот: рисунок и числа для отчёта."""
import math

import lde_calc

p = lde_calc.params()
R, C, f_max = p["R"], p["C"], p["f_max"]
f_c = 1.0 / (2 * math.pi * R * C)

freqs = [f_max * 10 ** (k / 50 - 4) for k in range(201)]
K = [1.0 / math.sqrt(1.0 + (f / f_c) ** 2) for f in freqs]

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

plt.rcParams.update({"font.family": "serif", "font.size": 12})
fig, ax = plt.subplots(figsize=(7, 3.6))
ax.semilogx(freqs, K, color="black")
ax.axvline(f_c, color="gray", linestyle="--")
ax.set_xlabel("Частота, Гц")
ax.set_ylabel("|K|")
ax.grid(True, which="both", alpha=0.3)
fig.tight_layout()
fig.savefig(lde_calc.out("afc.png"), dpi=200)

lde_calc.save_values(f_c=f_c, K_at_fc=1 / math.sqrt(2))
