"""Обращения агентов о работе в LDE: баги, недостающие инструменты, ручная рутина, неясная документация.

Агент, столкнувшись с проблемой:
  1) сверяется, есть ли нужное в LDE:   lde feedback check "что нужно сделать"
  2) если нет или пришлось делать вручную — пишет обращение:  lde feedback new --kind … --title … …
LDE сама дописывает к обращению версию, проект, последние команды агента в проекте (журнал
.lde/journal.jsonl), сводку lde check и найденные признаки рутины (lde feedback analyze).
Повтор той же проблемы дописывается в тот же файл (счётчик), а не плодит новые.

Ящик — каталог agent-feedback/ рядом с LDE (или LDE_FEEDBACK_DIR): отчёты в git не попадают
(в них бывают фрагменты проекта пользователя). Владелец читает их (lde feedback list / Studio →
«Обращения агентов») и передаёт в разработку LDE.
"""
from __future__ import annotations

import json
import os
import re
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KINDS = {"bug": "ошибка LDE", "missing": "нет нужного инструмента", "routine": "много ручной рутины",
         "docs": "непонятно из скилла/документации", "ux": "неудобно"}
SEVERITY = ("low", "medium", "high", "blocker")
STATUSES = ("open", "forwarded", "fixed", "duplicate", "wontfix")

# Что LDE уже умеет — для сверки агентом («может, это уже есть?»). Ключи — основы слов.
CAPABILITIES = [
    (("величин", "обознач", "символ"), "объявить величину и вставлять её {R_ц}", "lde new quantity R_ц --meaning … --unit …; lde list quantities"),
    (("величин", "текстом", "набран", "автоисправ", "исправить все"), "найти величины/числа/ссылки, набранные текстом, и исправить разом", "lde check; lde fix [правила]"),
    (("числ", "расчёт", "значени", "параметр"), "числа из расчётов в тексте {=расчёт.ключ}, пересчёт устаревшего", "lde list values <расчёт>; lde update; lde status"),
    (("расчёт", "скрипт", "рисунок", "график"), "расчёт со скриптом, рисунки и числа из него", "lde new calc <ключ>; lde update <ключ>; lde info <расчёт>"),
    (("таблиц", "csv"), "таблица из CSV расчёта, обновляется с ним", ": Заголовок {#tab:имя file=calc/x/out/t.csv}"),
    (("ссылк", "рисун", "таблиц", "формул", "номер"), "ссылки на рисунки/таблицы/формулы/разделы с автономером", "@fig:… @tab:… @eq:… @sec:…; lde list labels"),
    (("источник", "литератур", "цитат", "библиограф"), "источники и цитаты [@ключ, с. 45] по ГОСТ", "lde new source …; lde list sources"),
    (("сокращен", "аббревиатур"), "перечень сокращений", "lde new abbr РСА --meaning …"),
    (("провер", "ошибк", "гост", "линтер"), "проверка текста и оформления с местом и подсказкой", "lde check [--json]"),
    (("pdf", "собрат", "сборк"), "сборка PDF", "lde build"),
    (("word", "docx"), "экспорт DOCX с проверкой и ремонтом", "lde docx"),
    (("сдат", "сдач", "готов"), "всё для сдачи: пересчёт, проверки, PDF, DOCX", "lde submit"),
    (("отмен", "откат", "верну", "undo"), "отменить действия (по одному), повторить", "lde undo; lde redo"),
    (("верси", "истори", "сравн", "diff"), "версии, история, сравнение любых версий по элементам", "lde version; lde versions; lde history; lde diff <версия> [<версия>]"),
    (("вернуть", "восстанов", "откат", "верси"), "вернуть проект или файлы к версии", "lde restore <версия> [--only …]"),
    (("слиян", "конфликт", "merge"), "слияние по блокам, конфликты", "lde merge [--resolve uid ours|theirs|both]"),
    (("рецензент", "руковод", "ссылк", "замечани", "правки"), "ссылка на документ для рецензента, приём его правок", "lde share new|list|pull|accept|reject|own|reply"),
    (("примечани", "word", "комментари"), "примечания рецензента из Word", "lde review import <файл.docx>; lde review"),
    (("сервер", "синхрон", "опубликов", "соавтор"), "проект на сервере LDE, синхронизация", "lde publish; lde sync; lde clone; lde remote"),
    (("документ", "пояснительн", "второй"), "несколько документов в проекте с общей базой", "lde new document <ключ>; lde -D <ключ> …"),
    (("оформлен", "отступ", "выравн", "шрифт", "кегль", "интервал"), "ручное оформление блока", "строка {style …} над блоком (только по просьбе пользователя)"),
    (("импорт", "word", "docx"), "проект из Word-отчёта", "lde import-docx <файл> <каталог>"),
    (("раздел", "глав", "структур"), "разделы и приложения с автонумерацией", "# Название {#sec:имя}; {.appendix #app:имя}"),
    (("перенос", "дефис"), "переносы слов в тексте: выключить для всего документа (PDF и DOCX)", "lde.toml [document] hyphenation = false; Studio ⚙ → «Переносы слов»"),
    (("формул",), "выносная формула с номером, вставки величин/чисел в формуле", "$$ … $$ {#eq:имя}; \\q{R}; \\val{расчёт.ключ}"),
]

# Чего LDE заведомо не умеет (известные пробелы) — чтобы агент не искал зря и сразу писал обращение.
KNOWN_GAPS = [
    (("переимен", "rename"), "переименовать величину/метку по всему документу одной командой — нет (только правкой файлов)"),
    (("перенест", "перемест", "порядок раздел"), "переставить разделы/главы командой — нет (порядок файлов в text/ или files в lde.toml)"),
    (("орфограф", "опечат"), "проверки орфографии в LDE нет"),
]


def inbox(root: Path | None = None) -> Path:
    """Общий ящик (рядом с LDE или LDE_FEEDBACK_DIR). Если писать туда нельзя (песочница агента) —
    запасной ящик в проекте: .lde/agent-feedback/ (его показывают lde feedback list и Studio)."""
    d = Path(os.environ.get("LDE_FEEDBACK_DIR") or ROOT / "agent-feedback")
    try:
        (d / "reports").mkdir(parents=True, exist_ok=True)
        probe = d / "reports" / ".probe"
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return d
    except OSError:
        if root is None:
            raise
        fd = Path(root) / ".lde" / "agent-feedback"
        (fd / "reports").mkdir(parents=True, exist_ok=True)
        return fd


def _dirs(root: Path | None) -> list[Path]:
    out = []
    try:
        out.append(inbox() / "reports")
    except OSError:
        pass
    if root is not None and (Path(root) / ".lde" / "agent-feedback" / "reports").is_dir():
        out.append(Path(root) / ".lde" / "agent-feedback" / "reports")
    return out


def _stems(text: str) -> set[str]:
    return {w.lower() for w in re.findall(r"\w{3,}", text or "")}


def check(text: str) -> dict:
    """Есть ли в LDE то, что нужно агенту (по словам запроса)."""
    words = " ".join(_stems(text))
    found, gaps = [], []
    for keys, what, how in CAPABILITIES:
        hits = sum(1 for k in keys if k in words)
        if hits:
            found.append({"what": what, "how": how, "score": hits})
    for keys, what in KNOWN_GAPS:
        if any(k in words for k in keys):
            gaps.append(what)
    found.sort(key=lambda x: -x["score"])
    if found:                                   # только лучшие совпадения, без «шума»
        best = found[0]["score"]
        found = [f for f in found if f["score"] >= max(1, best - (0 if best > 1 else 0))]
    return {"found": found[:4], "gaps": gaps}


# -------------------------------------------------------------- журнал ----
def journal_path(root: Path) -> Path:
    return Path(root) / ".lde" / "journal.jsonl"


def log_command(root: Path, argv: list[str], rc: int, dur: float):
    """Записать вызов lde в журнал проекта (для поиска рутины). Пароли сюда не попадают (они не в argv)."""
    try:
        p = journal_path(root)
        p.parent.mkdir(exist_ok=True)
        clean, skip = [], False
        for a in argv:                       # без -C/-D <значение>: команда — первой
            if skip:
                skip = False
                continue
            if a in ("-C", "--project", "-D", "--doc"):
                skip = True
                continue
            if a:
                clean.append(a)
        rec = {"t": round(time.time(), 1), "argv": clean[:20], "rc": rc, "dur": round(dur, 2),
               "agent": os.environ.get("LDE_AGENT") or os.environ.get("DSH_PROFILE") or ""}
        with open(p, "a", encoding="utf-8") as f:
            f.write(json.dumps(rec, ensure_ascii=False) + "\n")
        if p.stat().st_size > 2_000_000:          # хвост достаточен
            lines = p.read_text(encoding="utf-8").splitlines()[-5000:]
            p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    except OSError:
        pass


def recent(root: Path, n: int = 40, since: float = 0) -> list[dict]:
    p = journal_path(root)
    if not p.exists():
        return []
    out = []
    for ln in p.read_text(encoding="utf-8", errors="replace").splitlines()[-2000:]:
        try:
            r = json.loads(ln)
        except json.JSONDecodeError:
            continue
        if r.get("t", 0) >= since:
            out.append(r)
    return out[-n:]


def analyze(root: Path, hours: float = 6) -> list[dict]:
    """Признаки рутины за последние часы: повторы команд, циклы check/fix, ручные правки, которые
    LDE могла бы сделать сама. Каждая находка — {kind, text, hint}."""
    from lde import gitops
    root = Path(root)
    since = time.time() - hours * 3600
    rows = recent(root, 5000, since)
    out = []
    # 1. одна и та же команда много раз подряд
    cmds = [" ".join(a for a in r["argv"][:2] if not a.startswith("-")) for r in rows]
    from collections import Counter
    for c, n in Counter(cmds).most_common(5):
        if c.startswith(("new", "list", "info")) and n >= 6:
            out.append({"kind": "routine", "text": f"«lde {c}» вызвана {n} раз за {hours:g} ч",
                        "hint": "нужен пакетный вариант (несколько элементов за один вызов) или импорт списком"})
    # 2. check снова и снова с ошибками — исправления вручную
    checks = [r for r in rows if r["argv"][:1] == ["check"]]
    fails = [r for r in checks if r["rc"] != 0]
    if len(fails) >= 5:
        out.append({"kind": "routine", "text": f"lde check завершилась с ошибками {len(fails)} раз из {len(checks)}",
                    "hint": "какие правила повторяются? если исправление однотипное — нужна автоправка (lde fix)"})
    # 3. правила проверки, которые lde fix не чинит, но они массовые
    try:
        from lde.check import run_check
        from lde.project import Project
        iss = run_check(Project(root))
        # то, что лечится командой (lde update / lde merge), — не пробел автоправки
        cured = {"stale", "value-norun", "file-missing", "calc-failed", "merge-conflict", "syntax", "config"}
        by = Counter(i.rule for i in iss if i.severity == "error" and not i.fix and i.rule not in cured)
        for rule, n in by.most_common(3):
            if n >= 5:
                out.append({"kind": "missing", "text": f"{n} ошибок «{rule}» без автоисправления",
                            "hint": "кандидат на автоправку в lde fix"})
    except Exception:
        pass
    # 4. массовые ручные правки файлов (агентом/редактором), особенно однотипные замены
    try:
        log = gitops.git(root, "log", f"--since={int(hours * 3600)} seconds ago", "--format=%H%x1f%s", check=False)
        ext = [ln.split("\x1f")[0] for ln in log.splitlines() if "\x1f" in ln and ln.split("\x1f")[1].startswith("Изменения вне Studio")]
        if ext:
            stat = gitops.git(root, "show", "--numstat", "--format=", *ext[:20], check=False)
            changed = sum(int(a) for a, *_ in (ln.split("\t") for ln in stat.splitlines() if ln and ln[0].isdigit()))
            if changed >= 200:
                out.append({"kind": "routine", "text": f"вручную изменено ~{changed} строк вне инструментов LDE ({len(ext)} точек)",
                            "hint": "что из этого — однотипные замены? их стоит сделать командой LDE"})
    except Exception:
        pass
    return out


# ------------------------------------------------------------ обращения ----
def _slug(s: str) -> str:
    tr = dict(zip("абвгдеёжзийклмнопрстуфхцчшщъыьэюя", ["a", "b", "v", "g", "d", "e", "e", "zh", "z", "i", "y", "k", "l",
                  "m", "n", "o", "p", "r", "s", "t", "u", "f", "h", "c", "ch", "sh", "sch", "", "y", "", "e", "yu", "ya"]))
    s = "".join(tr.get(c, c) for c in (s or "").lower())
    return re.sub(r"[^a-z0-9]+", "-", s).strip("-")[:50] or "report"


def _context(root: Path | None) -> list[str]:
    from lde import __version__, gitops
    out = [f"- **LDE:** {__version__} (`{gitops.git(ROOT, 'rev-parse', '--short', 'HEAD', check=False).strip() or '—'}`)"]
    if not root:
        out.append("- **Проект:** —")
        return out
    try:
        from lde.check import run_check
        from lde.project import Project
        p = Project(root)
        iss = run_check(p)
        from collections import Counter
        by = Counter(f"{i.rule} ({i.severity})" for i in iss if i.severity != "info")
        out.append(f"- **Проект:** «{p.title}» (`{Path(root).name}`), документов: {len(p.documents())}, "
                   f"точка `{gitops.head(root)[:8]}`")
        out.append("- **lde check:** " + (", ".join(f"{k}: {n}" for k, n in by.most_common(8)) or "замечаний нет"))
    except Exception as e:  # noqa: BLE001
        out.append(f"- **Проект:** `{root}` (не разобрать: {e})")
    return out


def new(kind: str, title: str, asked: str = "", did: str = "", manual: str = "", want: str = "", body: str = "",
        severity: str = "medium", agent: str = "", root: Path | None = None) -> dict:
    """Записать обращение (повтор той же проблемы — дописать в существующий файл)."""
    if kind not in KINDS:
        raise ValueError(f"вид обращения: {' | '.join(KINDS)}")
    if severity not in SEVERITY:
        raise ValueError(f"серьёзность: {' | '.join(SEVERITY)}")
    title = (title or "").strip()
    if len(title) < 5:
        raise ValueError("нужен заголовок: что не так, одной фразой")
    agent = (agent or os.environ.get("LDE_AGENT") or os.environ.get("DSH_MODEL") or "агент").strip()
    d = inbox(root) / "reports"
    slug = _slug(title)
    existing = [p for p in d.glob(f"*__{kind}__{slug}.md") if "Статус: open" in p.read_text(encoding="utf-8")]
    stamp = time.strftime("%Y-%m-%d %H:%M")
    cap = check(" ".join((title, asked, want, manual)))
    routine = analyze(root) if root else []
    if existing:
        p = existing[0]
        text = p.read_text(encoding="utf-8")
        n = int((re.search(r"\*\*Повторов:\*\* (\d+)", text) or [0, 0])[1]) + 1
        text = re.sub(r"\*\*Повторов:\*\* \d+", f"**Повторов:** {n}", text)
        add = [f"\n## Повтор {n} — {stamp} ({agent})", ""]
        for lbl, v in (("Просил пользователь", asked), ("Делал агент", did), ("Вручную", manual), ("Не хватило", want), ("Подробно", body)):
            if v:
                add.append(f"**{lbl}:** {v.strip()}\n")
        text = text.replace("\nСтатус: open", "\n".join(add) + "\nСтатус: open")
        p.write_text(text, encoding="utf-8")
        return {"path": str(p), "repeat": n, "check": cap}
    p = d / f"{time.strftime('%Y-%m-%d')}__{kind}__{slug}.md"
    lines = [f"# {title}", "",
             f"- **Дата:** {stamp}", f"- **Вид:** {KINDS[kind]} (`{kind}`)", f"- **Серьёзность:** {severity}",
             f"- **Агент:** {agent}", "- **Повторов:** 1", *_context(root), ""]
    for head, v in (("Что просил пользователь", asked), ("Что делал агент", did),
                    ("Что пришлось делать вручную (рутина)", manual), ("Какого инструмента не хватило", want),
                    ("Подробно", body)):
        if v:
            lines += [f"## {head}", "", v.strip(), ""]
    lines += ["## Сверка с возможностями LDE (автоматически)", ""]
    if cap["found"]:
        lines += [f"- похоже на: {c['what']} — `{c['how']}`" for c in cap["found"]]
        lines += ["", "_Если подходящее уже есть, но агент его не нашёл — это пробел скилла/документации._"]
    else:
        lines.append("- подходящих возможностей LDE по словам обращения не найдено")
    if cap["gaps"]:
        lines += [""] + [f"- известный пробел: {g}" for g in cap["gaps"]]
    lines.append("")
    if routine:
        lines += ["## Признаки рутины в проекте (lde feedback analyze)", ""]
        lines += [f"- {r['text']} → {r['hint']}" for r in routine] + [""]
    if root:
        cmds = recent(root, 25)
        if cmds:
            lines += ["## Последние команды lde в проекте", "", "```"]
            lines += [f"{time.strftime('%H:%M:%S', time.localtime(r['t']))}  rc={r['rc']}  lde {' '.join(r['argv'])}" for r in cmds]
            lines += ["```", ""]
    lines.append("Статус: open")
    p.write_text("\n".join(lines) + "\n", encoding="utf-8")
    return {"path": str(p), "repeat": 1, "check": cap}


def _find(rid: str, root: Path | None = None) -> Path:
    name = f"{Path(rid).name.removesuffix('.md')}.md"
    for d in _dirs(root):
        if (d / name).exists():
            return d / name
    raise ValueError(f"нет обращения {rid}")


def list_reports(all_: bool = False, root: Path | None = None) -> list[dict]:
    out = []
    files = [p for d in _dirs(root) for p in d.glob("*.md")]
    for p in sorted(files, key=lambda x: x.name, reverse=True):
        if p.name.startswith("_"):
            continue
        t = p.read_text(encoding="utf-8")
        st = (re.search(r"^Статус: (\w+)", t, re.M) or [0, "open"])[1]
        if not all_ and st != "open":
            continue
        m = re.match(r"(\d{4}-\d{2}-\d{2})__(\w+)__", p.name)
        out.append({"id": p.stem, "title": t.splitlines()[0].lstrip("# ").strip(), "date": m.group(1) if m else "",
                    "kind": m.group(2) if m else "", "kind_ru": KINDS.get(m.group(2) if m else "", ""), "status": st,
                    "repeats": int((re.search(r"\*\*Повторов:\*\* (\d+)", t) or [0, 1])[1]),
                    "severity": (re.search(r"\*\*Серьёзность:\*\* (\w+)", t) or [0, ""])[1]})
    return out


def show(rid: str, root: Path | None = None) -> str:
    return _find(rid, root).read_text(encoding="utf-8")


def set_status(rid: str, status: str, root: Path | None = None):
    if status not in STATUSES:
        raise ValueError(f"статус: {' | '.join(STATUSES)}")
    p = _find(rid, root)
    t = p.read_text(encoding="utf-8")
    p.write_text(re.sub(r"^Статус: \w+", f"Статус: {status}", t, flags=re.M), encoding="utf-8")
