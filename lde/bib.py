"""Библиография: refs.bib (BibTeX) → описания по ГОСТ Р 7.0.100-2018 (упрощённо).

LDE форматирует список сам — одинаково для PDF и DOCX (одна реализация, одинаковые номера).
Поле `formatted = {…}` задаёт готовое описание (так переносятся записи из импортированных
отчётов, оформленные автором вручную). Поле `file = {sources/…/x.pdf}` — файл источника.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path

DASH = " – "


@dataclass
class BibEntry:
    key: str
    type: str
    fields: dict = field(default_factory=dict)
    line: int = 0

    def get(self, k, default=""):
        return self.fields.get(k, default)


def _read_value(s: str, i: int):
    while i < len(s) and s[i] in " \t\r\n":
        i += 1
    if i >= len(s):
        return "", i
    if s[i] == "{":
        depth, j = 0, i
        while j < len(s):
            if s[j] == "\\":
                j += 2
                continue
            if s[j] == "{":
                depth += 1
            elif s[j] == "}":
                depth -= 1
                if depth == 0:
                    return s[i + 1:j], j + 1
            j += 1
        return s[i + 1:], len(s)
    if s[i] == '"':
        j = i + 1
        while j < len(s) and s[j] != '"':
            j += 2 if s[j] == "\\" else 1
        return s[i + 1:j], j + 1
    m = re.match(r"[^,}\s]+", s[i:])
    return (m.group(0), i + m.end()) if m else ("", i)


def parse_bib(text: str) -> tuple[list[BibEntry], list[str]]:
    entries, errors = [], []
    for m in re.finditer(r"@(\w+)\s*\{\s*([^,\s]+)\s*,", text):
        typ, key = m.group(1).lower(), m.group(2)
        if typ in ("comment", "preamble", "string"):
            continue
        line = text.count("\n", 0, m.start()) + 1
        i = m.end()
        fields = {}
        while i < len(text):
            fm = re.compile(r"\s*([\w\-]+)\s*=\s*").match(text, i)
            if not fm:
                break
            val, i = _read_value(text, fm.end())
            fields[fm.group(1).lower()] = re.sub(r"\s+", " ", val).strip()
            cm = re.compile(r"\s*,").match(text, i)
            if cm:
                i = cm.end()
            else:
                break
        entries.append(BibEntry(key, typ, fields, line))
    seen = set()
    for e in entries:
        if e.key in seen:
            errors.append(f"refs.bib:{e.line}: ключ {e.key} объявлен дважды")
        seen.add(e.key)
    return entries, errors


def load_bib(path: Path) -> tuple[dict[str, BibEntry], list[str]]:
    if not path.exists():
        return {}, []
    ents, errs = parse_bib(path.read_text(encoding="utf-8"))
    return {e.key: e for e in ents}, errs


def _clean(s: str) -> str:
    s = s.replace("{", "").replace("}", "").replace("\\&", "&").replace("~", " ")
    s = s.replace("--", "–")
    return s.strip()


def _authors(s: str):
    """'Cumming, I. G. and Wong, F. H.' → [('Cumming', 'I. G.'), ...]"""
    out = []
    for a in re.split(r"\s+and\s+", _clean(s)):
        a = a.strip()
        if not a:
            continue
        if a.lower() in ("others", "др."):
            out.append(("и др.", ""))
            continue
        if "," in a:
            last, first = [x.strip() for x in a.split(",", 1)]
        else:
            parts = a.split()
            last, first = parts[-1], " ".join(parts[:-1])
        ini = []
        for p in re.split(r"[\s.]+", first):
            if p:
                if "-" in p:
                    ini.append("-".join(x[0] + "." for x in p.split("-") if x))
                else:
                    ini.append(p[0] + ".")
        out.append((last, " ".join(ini)))
    return out


def is_russian(e: BibEntry) -> bool:
    return bool(re.search(r"[А-Яа-яЁё]", e.get("title") + e.get("booktitle")))


def format_entry(e: BibEntry) -> str:
    """Библиографическое описание (простой текст; тире – с пробелами)."""
    if e.get("formatted"):
        return _clean(e.get("formatted"))
    ru = is_russian(e)
    pg, vol, no, P = ("с.", "Т.", "№", "С.") if ru else ("p.", "Vol.", "No.", "P.")
    auth = _authors(e.get("author")) if e.get("author") else []
    title = _clean(e.get("title"))
    sub = _clean(e.get("subtitle"))
    t = title + (f" : {sub}" if sub else "")
    year = _clean(e.get("year") or e.get("date", "")[:4])
    city = _clean(e.get("address") or e.get("location"))
    publ = _clean(e.get("publisher") or e.get("institution") or e.get("school"))
    pages = _clean(e.get("pages"))
    edition = _clean(e.get("edition"))
    head = ""
    resp = ""
    real = [a for a in auth if a[0] != "и др."]
    if real and len(real) <= 3:
        head = f"{real[0][0]}, {real[0][1]} ".replace(" ,", ",")
        resp = ", ".join(f"{a[1]} {a[0]}".strip() for a in real)
    elif real:
        resp = f"{real[0][1]} {real[0][0]} [{'и др.' if ru else 'et al.'}]"
    parts = []
    body = head + t
    if resp:
        body += f" / {resp}"
    if e.get("editor"):
        ed = ", ".join(f"{a[1]} {a[0]}" for a in _authors(e.get("editor")))
        body += f" ; {'под ред.' if ru else 'ed. by'} {ed}"
    typ = e.type
    if typ in ("article",):
        j = _clean(e.get("journal") or e.get("journaltitle"))
        tail = f" // {j}{DASH}{year}"
        if e.get("volume"):
            tail += f".{DASH}{vol} {_clean(e.get('volume'))}"
        if e.get("number"):
            tail += (", " if e.get("volume") else f".{DASH}") + f"{no} {_clean(e.get('number'))}"
        if pages:
            tail += f".{DASH}{P} {pages}"
        return body + tail + "."
    if typ in ("incollection", "inbook", "inproceedings"):
        bt = _clean(e.get("booktitle"))
        tail = f" // {bt}"
        if city or publ:
            tail += f".{DASH}{city}{' : ' if city and publ else ''}{publ}"
        if year:
            tail += f", {year}" if (city or publ) else f".{DASH}{year}"
        if pages:
            tail += f".{DASH}{P} {pages}"
        return body + tail + "."
    if typ in ("online", "electronic", "www"):
        url = _clean(e.get("url"))
        urld = _clean(e.get("urldate"))
        s = f"{t} : [{'сайт' if ru else 'website'}]{DASH}URL: {url}"
        if urld:
            s += f" (дата обращения: {urld})"
        return s + "."
    parts.append(body)
    if edition:
        parts.append(f"{edition}")
    if city or publ or year:
        s = city
        if publ:
            s += (" : " if city else "") + publ
        if year:
            s += (", " if s else "") + year
        parts.append(s)
    if e.get("pagetotal"):
        parts.append(f"{_clean(e.get('pagetotal'))} {pg}")
    elif pages and typ == "book":
        parts.append(f"{pages} {pg}")
    return (f".{DASH}".join(p.rstrip(".") for p in parts if p) + ".").replace("..", ".")


def cite_text(numbers: list[tuple[int, str]]) -> str:
    """[(номер, локатор)] → '[3, с. 45]' / '[1, 2]' / '[15–18]' / '[3, с. 4; 7]'."""
    if not numbers:
        return "[?]"
    if any(loc for _, loc in numbers):
        return "[" + "; ".join(f"{n}, {loc}" if loc else f"{n}" for n, loc in numbers) + "]"
    ns = sorted(set(n for n, _ in numbers))
    groups, start, prev = [], ns[0], ns[0]
    for x in ns[1:] + [None]:
        if x is not None and x == prev + 1:
            prev = x
            continue
        if prev - start >= 2:
            groups.append(f"{start}–{prev}")
        elif prev != start:
            groups += [str(start), str(prev)]
        else:
            groups.append(str(start))
        if x is not None:
            start = prev = x
    return "[" + ", ".join(groups) + "]"


def replace_entry(text: str, key: str, typ: str, fields: dict) -> str:
    """Заменить (или добавить) одну запись в тексте refs.bib, остальное не трогая."""
    import re as _re
    fields = {k: v for k, v in fields.items() if str(v).strip()}
    body = ",\n".join(f"  {k:<9} = {{{v}}}" for k, v in fields.items())
    entry = f"@{typ or 'book'}{{{key},\n{body},\n}}"
    m = _re.search(r"@\w+\s*\{\s*" + _re.escape(key) + r"\s*,", text)
    if not m:
        return text.rstrip("\n") + "\n\n" + entry + "\n"
    depth, j = 0, text.index("{", m.start())
    while j < len(text):                      # конец записи — по балансу скобок
        depth += (text[j] == "{") - (text[j] == "}")
        if depth == 0:
            break
        j += 1
    return text[:m.start()] + entry + text[j + 1:]
