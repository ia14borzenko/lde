# Расчёты LDE

Расчёт — каталог `calc/<ключ>/` (ключ латиницей: `csa`, `omegak`):

```toml
# calc/csa/calc.toml
title = "Численный пример ЧМ-масштабирования"
script = "script.py"
timeout = 600
inputs = ["data/raw.csv"]                  # файлы проекта, от которых зависит результат
outputs = ["fig14.png", "metrics.json"]    # что скрипт обязан создать в out/

[params]                                   # видны и правятся в Studio
PRF = { value = 1800.0, unit = "Гц", title = "Частота повторения импульсов" }
N = 5632

[values]                                   # единицы и точность чисел из JSON-выходов
"migration.diff_edge_m" = { unit = "м", digits = 1, title = "Дифференциальная миграция" }
```

```python
# calc/csa/script.py
import lde_calc
p = lde_calc.params()                 # {"PRF": 1800.0, "N": 5632}
...
fig.savefig(lde_calc.out("fig14.png"), dpi=200)
lde_calc.save_values(alpha_max=0.0427, migration={"diff_edge_m": 6.4})   # → out/values.json
data = lde_calc.path("data/raw.csv")  # путь к файлу проекта
```

- Запуск: `lde update` (всё устаревшее, в порядке зависимостей) или `lde update csa`.
  Скрипт работает в копии каталога, без сети; при ошибке прошлые выходы сохраняются.
- Актуальность — по содержимому скрипта, параметров и входов. `lde status` объясняет причину:
  «изменён параметр PRF: 1800 → 2000».
- Числа в тексте: `{=csa.PRF}` (параметр последнего удачного запуска), `{=csa.alpha_max}`,
  `{=csa.migration.diff_edge_m}` (вложенные ключи JSON через точку). Список — `lde list values csa`.
- Рисунок в тексте: `![…](calc/csa/out/fig14.png){#fig:…}` — зависимость определяется по пути.
- Провенанс: `lde info fig:stages` / `lde info csa` — скрипт, параметры, время, журнал.
- Python по умолчанию — системный `python3` (numpy, matplotlib). Другой — `python = "/путь"` в calc.toml.
- Подписи осей на рисунках — по-русски, шрифт с засечками; без заголовка над графиком (подпись — в тексте).
