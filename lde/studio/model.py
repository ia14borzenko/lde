"""Элементы документа для Studio: описание, токены с чипами, правка, «что изменилось»."""
from __future__ import annotations

import difflib
import json
import re
from pathlib import Path

from lde import bib as bibmod
from lde import gitops
from lde.fmt import FormatError, format_value
from lde.project import Project
from lde.style import ALLOWED as STYLE_ALLOWED, clean as style_clean, format_line, is_style_line
from lde.textfmt import Block, Node, parse_inline, parse_text, replace_block, insert_after

KIND_RU = {"heading": "Заголовок", "para": "Абзац", "formula": "Формула", "figure": "Рисунок", "table": "Таблица",
           "list": "Перечисление", "code": "Листинг", "comment": "Комментарий", "pagebreak": "Разрыв страницы",
           "conflict": "Конфликт слияния"}


class ConflictError(Exception):
    def __init__(self, msg, current=None):
        super().__init__(msg)
        self.current = current


def esc_text(s: str) -> str:
    """Текст, набранный человеком, → LDE-текст (экранировать только то, что иначе станет разметкой)."""
    s = s.replace("\\", "\\\\")
    for ch in "{}$":
        s = s.replace(ch, "\\" + ch)
    s = re.sub(r"\*", r"\\*", s)
    s = s.replace("[@", "\\[@")
    s = re.sub(r"(?<![\w@])@(?=(fig|tab|eq|sec|app):)", r"\\@", s)
    return s


def chip_info(p: Project, nd: Node) -> dict:
    """Как показать вставку: {kind, text, latex?, title}."""
    if nd.k == "q":
        q = p.quantities.get(nd.v)
        return {"kind": "q", "latex": q.tex if q else "", "text": nd.v, "bad": q is None,
                "title": (f"{q.meaning}{', ' + q.unit if q.unit else ''}" if q and q.meaning else f"величина {nd.v}")
                if q else f"величина {nd.v} не объявлена"}
    if nd.k == "val":
        v = p.value(nd.v)
        if v is None:
            return {"kind": "val", "text": f"[нет {nd.v}]", "bad": True, "title": f"нет числа {nd.v}"}
        try:
            t = format_value(v["value"], v.get("unit", ""), v.get("digits"), nd.opts)["text"]
        except FormatError as e:
            return {"kind": "val", "text": "[?]", "bad": True, "title": str(e)}
        ck = nd.v.split(".", 1)[0]
        c = p.calcs.get(ck)
        return {"kind": "val", "text": t, "title": f"{v.get('title') or nd.v} — расчёт «{c.name if c else ck}»"}
    if nd.k == "ref":
        t = p.labels.get(nd.v)
        if not t:
            return {"kind": "ref", "text": "??", "bad": True, "title": f"нет метки {nd.v}"}
        num = f"({t.number})" if t.kind == "eq" else t.number
        what = {"fig": "Рисунок", "tab": "Таблица", "eq": "Формула", "sec": "Раздел", "app": "Приложение"}[t.kind]
        return {"kind": "ref", "text": num, "title": f"{what} {num}: {re.sub(r'[{}$@]', '', t.title)[:80]}"}
    if nd.k == "cite":
        nums = [(p.cite_num.get(k, 0), loc) for k, loc in nd.items]
        bad = any(n == 0 for n, _ in nums)
        titles = []
        for k, _ in nd.items:
            e = p.bib.get(k)
            titles.append(bibmod.format_entry(e)[:90] if e else f"нет источника {k}")
        return {"kind": "cite", "text": "[?]" if bad else bibmod.cite_text(nums), "bad": bad, "title": "; ".join(titles)}
    if nd.k == "math":
        from lde import mathins
        ml = mathins.expand(nd.v, p, "ml") if mathins.find(nd.v) else nd.v
        return {"kind": "math", "latex": ml, "title": "формула"}
    if nd.k in ("b", "i"):
        from lde.textfmt import plain_text
        return {"kind": nd.k, "text": plain_text(nd.items), "title": "жирный" if nd.k == "b" else "курсив"}
    return {"kind": nd.k, "text": nd.v}


def tokens(p: Project, src: str, nodes: list[Node], base: int) -> list[dict]:
    """Узлы → токены редактора: text (как видит человек) и chip (src = исходник вставки)."""
    out = []
    for nd in nodes:
        raw = src[nd.s - base:nd.e - base]
        if nd.k == "text":
            out.append({"t": "text", "v": nd.v, "src": raw})
        else:
            out.append({"t": "chip", "src": raw, **chip_info(p, nd)})
    return out


def detokenize(new_tokens: list[dict], old_tokens: list[dict]) -> str:
    """Токены → исходник; неизменённые куски текста берутся из исходника как были (минимальный diff)."""
    old_vals = [("c:" + t["src"]) if t["t"] == "chip" else ("t:" + t["v"]) for t in old_tokens]
    new_vals = [("c:" + t["src"]) if t["t"] == "chip" else ("t:" + t["v"]) for t in new_tokens]
    sm = difflib.SequenceMatcher(a=old_vals, b=new_vals, autojunk=False)
    out = []
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            out += [old_tokens[i]["src"] for i in range(a1, a2)]
        else:
            for t in new_tokens[b1:b2]:
                out.append(t["src"] if t["t"] == "chip" else esc_text(t["v"]))
    return "".join(out)


def block_text_span(b: Block) -> tuple[int, int]:
    """Где в src блока лежит редактируемый текст (абзац/подпись/заголовок)."""
    if b.kind == "para":
        return b.text_off, len(b.src)
    return b.text_off, b.text_off + len(b.text)


def describe(p: Project, b: Block) -> dict:
    d = {"uid": b.uid, "kind": b.kind, "kind_ru": KIND_RU.get(b.kind, b.kind), "title": p.title_of(b),
         "file": b.file, "line": b.line, "src": b.src[b.style_len:], "label": b.label, "base": b.src,
         "style": b.style, "style_on": bool(b.style_len), "style_keys": STYLE_ALLOWED.get(b.kind, [])}
    if b.kind in ("para", "heading", "figure", "table") and b.kind != "list":
        d["tokens"] = tokens(p, b.src, b.inl, 0)
    if b.kind == "heading":
        d["level"] = b.level
        d["unnumbered"] = b.unnumbered
    if b.kind == "formula":
        from lde import mathins
        d["latex"] = mathins.expand(b.math, p, "ml") if mathins.find(b.math) else b.math
        d["numbered"] = bool(b.label) and not b.unnumbered
        d["number"] = p.numbers.get(id(b), "")
    if b.kind == "figure":
        d["path"] = b.path
        d["width"] = b.attrs.get("width", "0.9")
        deps = sorted(p.block_calcs(b))
        d["calc"] = deps[0] if deps else ""
        d["number"] = p.numbers.get(id(b), "")
        d["subfigs"] = [sf[1] for sf in b.subfigs]
    if b.kind == "table":
        d["number"] = p.numbers.get(id(b), "")
        d["header"] = [c[0] for c in b.header]
        d["header_tokens"] = [tokens(p, b.src, c[2], 0) for c in b.header]
        d["rows_tokens"] = [[tokens(p, b.src, c[2], 0) for c in r] for r in b.rows]
        d["rows"] = [[c[0] for c in r] for r in b.rows]
        d["file_src"] = b.attrs.get("file", "")
    if b.kind == "list":
        d["items"] = [{"level": it[0], "mark": it[1], "tokens": tokens(p, b.src, it[4], 0)} for it in b.items]
    if b.kind == "conflict":
        from lde.merge import parse_conflict
        c = parse_conflict(b.src)
        d["conflict"] = dict(c, w_ours=word_diff(c.get("base", ""), c.get("ours", "")),
                             w_theirs=word_diff(c.get("base", ""), c.get("theirs", "")))
    return d


# ------------------------------------------------------------------ правка ----
def find_block(p: Project, uid: str, base_src: str | None) -> Block:
    b = p.block_by_uid(uid)
    if b is not None and (base_src is None or b.src == base_src):
        return b
    if base_src is not None:
        # блок мог сдвинуться (агент вставил абзац выше) — ищем по точному тексту
        same = [x for x in p.blocks if x.src == base_src]
        if len(same) == 1:
            return same[0]
    cur = b.src if b is not None else None
    raise ConflictError("Этот элемент изменился, пока вы его правили (вероятно, агентом). "
                        "Ваш вариант не потерян — сравните и выберите.", cur)


def write_file(root: Path, rel: str, text: str):
    p = root / rel
    tmp = p.with_suffix(p.suffix + ".lde-tmp")
    tmp.write_text(text, encoding="utf-8")
    tmp.replace(p)


def save_block(root: Path, uid: str, base_src: str, new_src: str, message: str | None = None, doc=None,
               keep_style: bool = True, author=gitops.STUDIO_AUTHOR, who: str = "Studio") -> dict:
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    p = Project(root, doc)
    b = find_block(p, uid, base_src)
    if keep_style and b.style_len and new_src.strip() and not is_style_line(new_src.split("\n", 1)[0]):
        new_src = b.src[:b.style_len] + new_src     # редактор прислал блок без строки {style …}
    if new_src == b.src:
        return {"changed": False}
    text = p.texts[b.file]
    title = p.title_of(b)
    if new_src.strip() == "":
        new_text = replace_block(text, b, "")
        new_text = re.sub(r"\n{3,}", "\n\n", new_text)
        msg = message or f"{who}: удалён элемент «{title}»"
    else:
        new_text = replace_block(text, b, new_src)
        msg = message or f"{who}: изменён элемент «{title}»"
    write_file(root, b.file, new_text)
    h = gitops.commit(root, msg, author=author, paths=[b.file])
    return {"changed": True, "commit": h, "message": msg}


def set_style(root: Path, uid: str, base_src: str, style: dict | None, doc=None) -> dict:
    """Включить/изменить/снять ручное оформление блока (строка {style …} над ним)."""
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    p = Project(root, doc)
    b = find_block(p, uid, base_src)
    st = style_clean(style, b.kind) if style is not None else {}
    line = format_line(st) if style is not None else ""
    if style is not None and not line:
        line = "{style}"                         # включено, параметры ещё не заданы
    body = b.src[b.style_len:]
    new_src = (line + "\n" + body) if line else body
    if new_src == b.src:
        return {"changed": False}
    title = p.title_of(b)
    write_file(root, b.file, replace_block(p.texts[b.file], b, new_src))
    msg = (f"Studio: ручное оформление «{title}»" if line else f"Studio: оформление «{title}» — снова автоматическое")
    h = gitops.commit(root, msg, author=gitops.STUDIO_AUTHOR, paths=[b.file])
    return {"changed": True, "commit": h, "message": msg, "src": new_src}


def insert_block(root: Path, after_uid: str | None, new_src: str, file: str | None = None, message=None, doc=None,
                 author=gitops.STUDIO_AUTHOR, who: str = "Studio") -> dict:
    gitops.ensure_repo(root)
    gitops.checkpoint_external(root)
    p = Project(root, doc)
    b = p.block_by_uid(after_uid) if after_uid else None
    f = b.file if b else (file or (p.files[-1] if p.files else "text/01.md"))
    text = p.texts.get(f, "")
    new_text = insert_after(text, b, new_src)
    write_file(root, f, new_text)
    nb = parse_text(new_src)
    what = KIND_RU.get(nb[0].kind, "элемент").lower() if nb else "элемент"
    verb = "добавлена" if what.endswith("а") else "добавлено" if what.endswith("е") else "добавлен"
    msg = message or f"{who}: {verb} {what}" + (f" после «{p.title_of(b)}»" if b else "")
    h = gitops.commit(root, msg, author=author, paths=[f])
    return {"changed": True, "commit": h, "message": msg}


# ----------------------------------------------------------- что изменилось ----
def _blocks_at(root: Path, rev: str, files: list[str]) -> dict[str, list[Block]]:
    out = {}
    for f in files:
        t = gitops.show(root, rev, f) if rev else None
        out[f] = parse_text(t, f) if t is not None else []
    return out


_TOKEN_RE = re.compile(r"\{[^{}\n]*\}|\$[^$\n]*\$|\[@[^\]\n]*\]|@(?:fig|tab|eq|sec|app):[\w:.\-]+|\*\*|\w+|\s+|.", re.S)


def word_diff(old: str, new: str) -> list[list[str]]:
    """Пословная разница: [["=", текст], ["-", удалено], ["+", вставлено], …]. Вставки ({R}, {=x}, $…$,
    [@k], @fig:…) — неделимые «слова»."""
    a, b = _TOKEN_RE.findall(old or ""), _TOKEN_RE.findall(new or "")
    out: list[list[str]] = []

    def put(op, txt):
        if not txt:
            return
        if out and out[-1][0] == op:
            out[-1][1] += txt
        else:
            out.append([op, txt])
    sm = difflib.SequenceMatcher(a=a, b=b, autojunk=False)
    for op, a1, a2, b1, b2 in sm.get_opcodes():
        if op == "equal":
            put("=", "".join(a[a1:a2]))
        else:
            put("-", "".join(a[a1:a2]))
            put("+", "".join(b[b1:b2]))
    return out


def _hist_title(b: Block) -> str:
    from lde.textfmt import plain_text
    txt = (plain_text(b.inl) if b.inl else (b.math or b.text or "")).strip()
    txt = re.sub(r"\s+", " ", txt)
    return KIND_RU.get(b.kind, b.kind) + (f" «{txt[:60]}{'…' if len(txt) > 60 else ''}»" if txt else "")


def _hist_where(blocks: list[Block]) -> dict:
    where, cur = {}, ""
    for b in blocks:
        if b.kind == "heading":
            cur = re.sub(r"\s*\{[^}]*\}\s*$", "", b.text).strip()
        where[id(b)] = cur
    return where


def changes(root: Path, doc=None, base: str | None = None, target: str | None = None) -> dict:
    """Изменения от версии base (по умолчанию — отметка «просмотрено») до target (по умолчанию —
    текущее состояние, включая несохранённое). По элементам, с пословной разницей."""
    p = Project(root, doc)
    base = base or gitops.get_seen(root)
    if not base:
        return {"seen": None, "base": None, "target": target, "groups": [], "other": [], "count": 0}
    tdir = "text/" if p.doc_key == "main" else f"documents/{p.doc_key}/"
    ls = lambda rev: {f for f in gitops.git(root, "ls-tree", "-r", "--name-only", rev, tdir, check=False).split()
                      if f.endswith(".md")}
    files = sorted(ls(base) | (ls(target) if target else set(p.files)))
    old = _blocks_at(root, base, files)
    newb = _blocks_at(root, target, files) if target else None
    items = []
    for f in files:
        ob = [b for b in old.get(f, []) if b.kind != "comment"]
        if target:
            nb = [b for b in newb.get(f, []) if b.kind != "comment"]
            nwhere = _hist_where(nb)
            title, where, uid = _hist_title, (lambda b: nwhere.get(id(b), "")), (lambda b: None)
        else:
            nb = [b for b in p.blocks if b.file == f and b.kind != "comment"]
            title, where, uid = p.title_of, (lambda b: p.heading_path.get(id(b), "")), (lambda b: b.uid)
        sm = difflib.SequenceMatcher(a=[b.src for b in ob], b=[b.src for b in nb], autojunk=False)
        for op, a1, a2, b1, b2 in sm.get_opcodes():
            if op == "equal":
                continue
            olds, news = ob[a1:a2], nb[b1:b2]
            k = 0
            while k < min(len(olds), len(news)) and olds[k].kind == news[k].kind:
                items.append({"op": "changed", "uid": uid(news[k]), "kind": news[k].kind,
                              "title": title(news[k]), "old": olds[k].src, "new": news[k].src, "file": f,
                              "where": where(news[k]), "words": word_diff(olds[k].src, news[k].src)})
                k += 1
            for b in news[k:]:
                items.append({"op": "added", "uid": uid(b), "kind": b.kind, "title": title(b), "new": b.src,
                              "file": f, "where": where(b)})
            prev_uid = uid(news[k - 1]) if k else (uid(nb[b1 - 1]) if b1 > 0 else None)
            for b in olds[k:]:
                items.append({"op": "removed", "kind": b.kind, "title": _hist_title(b) + " (удалён)",
                              "old": b.src, "file": f, "after_uid": prev_uid, "where": ""})
    # перенос: удалённый и добавленный блок с тем же текстом — «перенесён», а не два изменения
    added = {}
    for it in items:
        if it["op"] == "added":
            added.setdefault(it["new"], []).append(it)
    moved = []
    for it in items:
        if it["op"] == "removed" and added.get(it["old"]):
            a = added[it["old"]].pop()
            a["op"] = "moved"
            moved.append(it)
    items = [it for it in items if not any(it is m for m in moved)]
    groups = {}
    for it in items:
        g = it["where"] or it["file"]
        groups.setdefault(g, []).append(it)
    summary = []
    for g, lst in groups.items():
        cnt = {}
        for it in lst:
            key = (it["op"], it["kind"])
            cnt[key] = cnt.get(key, 0) + 1
        parts = []
        for (op, kind), n in cnt.items():
            sign = {"added": "+", "removed": "−", "changed": "изменено: ", "moved": "перенесено: "}[op]
            parts.append(f"{sign}{n} {_kind_plural(kind, n)}")
        summary.append({"where": g, "summary": ", ".join(parts), "items": lst})
    other = []
    diff = gitops.git(root, "diff", "--name-only", base, *([target] if target else []), check=False).split()
    for f in sorted(set(diff)):
        if f.startswith("text/") or (f.startswith("documents/") and f.endswith(".md")):
            continue
        what = {"quantities.toml": "величины", "abbreviations.toml": "сокращения", "refs.bib": "источники",
                "lde.toml": "настройки документа"}.get(f, "")
        if not what and f.startswith("calc/"):
            what = f"расчёт {f.split('/')[1]}" + (" (параметры)" if f.endswith("calc.toml") else " (скрипт)")
        other.append({"file": f, "what": what or f})
    return {"seen": base, "base": base, "target": target, "groups": summary, "other": other,
            "count": len(items) + len(other)}


def _kind_plural(kind, n):
    forms = {"para": ("абзац", "абзаца", "абзацев"), "figure": ("рисунок", "рисунка", "рисунков"),
             "table": ("таблица", "таблицы", "таблиц"), "formula": ("формула", "формулы", "формул"),
             "heading": ("заголовок", "заголовка", "заголовков"), "list": ("перечисление", "перечисления", "перечислений"),
             "code": ("листинг", "листинга", "листингов")}
    one, few, many = forms.get(kind, ("элемент", "элемента", "элементов"))
    if n % 10 == 1 and n % 100 != 11:
        return one
    if 2 <= n % 10 <= 4 and not 12 <= n % 100 <= 14:
        return few
    return many


def revert_change(root: Path, item: dict, doc=None) -> dict:
    op = item["op"]
    if op == "changed":
        return save_block(root, item["uid"], item["new"], item["old"], message=f"Studio: откат правки «{item['title']}»", doc=doc,
                          keep_style=False)
    if op == "added":
        return save_block(root, item["uid"], item["new"], "", message=f"Studio: откат — убран добавленный элемент «{item['title']}»", doc=doc)
    if op == "removed":
        return insert_block(root, item.get("after_uid"), item["old"], file=item.get("file"),
                            message="Studio: откат — возвращён удалённый элемент", doc=doc)
    return {"changed": False}
