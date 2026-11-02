"""Проект LDE: настройки, разделы, величины, сокращения, источники, расчёты → модель документа.

Структура каталога проекта:

    lde.toml            настройки документа (титул, профиль, нумерация, порядок разделов)
    text/*.md           текст по разделам (формат — lde/textfmt.py)
    quantities.toml     величины (обозначения)
    abbreviations.toml  сокращения
    refs.bib            источники (BibTeX); файлы источников — sources/<ключ>/
    calc/<ключ>/        расчёты: calc.toml + скрипт; out/ — последние удачные выходы
    figures/  data/     статичные рисунки и исходные данные
    out/                PDF, DOCX, отчёты (не в git)
    .lde/               кэш сборки и запусков (не в git)
"""
from __future__ import annotations

import os
import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from lde import bib as bibmod
from lde import calcs as calcmod
from lde.quantities import Quantity, load_quantities
from lde.textfmt import Block, Node, parse_text, walk

APPENDIX_LETTERS = "АБВГДЕЖИКЛМНПРСТУФХЦШЩЭЮЯ"  # ГОСТ 7.32-2017 п. 6.14.3: без Ё З Й О Ч Ъ Ы Ь

DEFAULT_CONFIG = {
    "document": {
        "profile": "gost-7.32",
        "numbering": "continuous",
        "block_spacing": "none",   # отступы до/после формул, заголовков, рисунков: none | line
        "hyphenation": True,       # переносы слов в основном тексте (false — без переносов, PDF и DOCX)
        "toc": True,
        "abbreviations": True,
        "symbols": False,
        "bibliography": True,
        "files": [],
        "name": "report",
    },
    "title": {},
}


@dataclass
class Issue:
    severity: str            # error | warning | info
    rule: str                # машинный код правила (только в JSON/подробностях)
    message: str             # человеческим языком
    file: str = ""
    line: int = 0
    col: int = 0
    hint: str = ""           # чем заменить / что сделать
    fix: dict | None = None  # {"old": "...", "new": "..."} — предложенная замена (не применяется сама)
    element: str = ""        # uid блока

    def to_dict(self):
        return {k: v for k, v in self.__dict__.items() if v not in ("", None, 0) or k in ("severity", "message")}

    def human(self) -> str:
        where = (f"{self.file}:{self.line}" if self.line else self.file) if self.file else ""
        sev = {"error": "ошибка", "warning": "замечание", "info": "к сведению"}[self.severity]
        s = f"{where + ': ' if where else ''}{sev}: {self.message}"
        if self.hint:
            s += f" → {self.hint}"
        return s


@dataclass
class Target:
    kind: str           # fig|tab|eq|sec|app
    number: str
    block: Block
    title: str = ""


def find_project(start: str | None = None) -> Path | None:
    if start:
        p = Path(start).expanduser().resolve()
        return p if (p / "lde.toml").exists() else None
    env = os.environ.get("LDE_PROJECT")
    if env and (Path(env) / "lde.toml").exists():
        return Path(env).resolve()
    p = Path.cwd().resolve()
    for q in [p, *p.parents]:
        if (q / "lde.toml").exists():
            return q
    return None


def _merge(a: dict, b: dict) -> dict:
    out = dict(a)
    for k, v in b.items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class Project:
    def __init__(self, root: Path, doc: str | None = None):
        self.root = Path(root).resolve()
        # документ проекта: main — основной (text/, lde.toml), прочие — documents/<ключ>/
        self.doc_key = doc if doc and doc != "main" else "main"
        self.issues: list[Issue] = []
        self.load()

    # ------------------------------------------------------------ loading ----
    def load(self):
        self.issues = []
        cfg_path = self.root / "lde.toml"
        try:
            raw = tomllib.loads(cfg_path.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            raw = {}
            self.issues.append(Issue("error", "config", f"lde.toml: ошибка TOML: {e}", "lde.toml"))
        self.config = _merge(DEFAULT_CONFIG, raw)
        if self.doc_key != "main":
            # настройки документа наследуются от проекта (оформление, реквизиты автора), кроме
            # состава текста и имени файла
            ddir = self.doc_dir
            dpath = ddir / "document.toml"
            if not dpath.exists():
                self.issues.append(Issue("error", "config", f"нет документа «{self.doc_key}» ({dpath.relative_to(self.root)})"))
                draw = {}
            else:
                try:
                    draw = tomllib.loads(dpath.read_text(encoding="utf-8"))
                except tomllib.TOMLDecodeError as e:
                    draw = {}
                    self.issues.append(Issue("error", "config", f"{dpath.relative_to(self.root)}: ошибка TOML: {e}"))
            base = dict(self.config)
            base["document"] = {k: v for k, v in self.config["document"].items() if k not in ("files", "name")}
            self.config = _merge(base, draw)
            self.config["document"].setdefault("name", self.doc_key)
        self.doc_cfg = self.config["document"]
        self.quantities, qerr = load_quantities(self.root / "quantities.toml")
        for e in qerr:
            self.issues.append(Issue("error", "quantities", e, "quantities.toml"))
        self.abbreviations = self._load_abbr()
        self.bib, berr = bibmod.load_bib(self.root / "refs.bib")
        for e in berr:
            self.issues.append(Issue("error", "bib", e, "refs.bib"))
        self.calcs = calcmod.load_calcs(self.root)
        for c in self.calcs.values():
            for e in c.errors:
                self.issues.append(Issue("error", "calc", e, f"calc/{c.key}/calc.toml"))
        self.files = self._text_files()
        self.texts: dict[str, str] = {}
        self.blocks: list[Block] = []
        for f in self.files:
            p = self.root / f
            try:
                t = p.read_text(encoding="utf-8")
            except FileNotFoundError:
                self.issues.append(Issue("error", "files", f"нет файла раздела {f} (указан в lde.toml)", "lde.toml"))
                continue
            self.texts[f] = t
            bl = parse_text(t, f)
            for b in bl:
                for ln, msg in b.errors:
                    self.issues.append(Issue("error", "syntax", msg, f, ln, element=b.uid))
                if b.kind == "conflict":
                    self.issues.append(Issue("error", "merge-conflict", "конфликт слияния: правили оба — выберите вариант "
                                             "(Studio: щёлкните по красной пометке; CLI: lde merge --resolve)", f, b.line,
                                             element=b.uid))
            self.blocks.extend(bl)
        self.resolve()

    def _load_abbr(self) -> dict[str, str]:
        p = self.root / "abbreviations.toml"
        if not p.exists():
            return {}
        try:
            d = tomllib.loads(p.read_text(encoding="utf-8"))
        except tomllib.TOMLDecodeError as e:
            self.issues.append(Issue("error", "abbr", f"abbreviations.toml: ошибка TOML: {e}", "abbreviations.toml"))
            return {}
        return {str(k): str(v) for k, v in d.items()}

    @property
    def doc_dir(self) -> Path:
        return self.root if self.doc_key == "main" else self.root / "documents" / self.doc_key

    @property
    def build_dir(self) -> Path:
        return self.root / ".lde" / ("build" if self.doc_key == "main" else f"build-{self.doc_key}")

    @property
    def config_file(self) -> str:
        """Где правятся настройки этого документа (относительно проекта)."""
        return "lde.toml" if self.doc_key == "main" else f"documents/{self.doc_key}/document.toml"

    def documents(self) -> list[dict]:
        return list_documents(self.root)

    def _text_files(self) -> list[str]:
        files = list(self.doc_cfg.get("files") or [])
        if files:
            return files
        td = self.root / "text" if self.doc_key == "main" else self.doc_dir
        if not td.is_dir():
            return []
        return [str(p.relative_to(self.root)) for p in sorted(td.glob("*.md"))]

    @property
    def name(self) -> str:
        return self.doc_cfg.get("name") or "report"

    @property
    def title(self) -> str:
        return self.config.get("title", {}).get("topic") or self.doc_cfg.get("title") or self.root.name

    # ---------------------------------------------------------- resolving ----
    def resolve(self):
        """Нумерация, метки, порядок цитат, зависимости."""
        mode = self.doc_cfg.get("numbering", "continuous")
        self.labels: dict[str, Target] = {}
        self.numbers: dict[int, str] = {}       # id(block) → номер (заголовки, рисунки, …)
        self.section_of: dict[int, str] = {}    # id(block) → номер раздела верхнего уровня («5», «А»)
        self.heading_path: dict[int, str] = {}  # id(block) → «5.2» ближайшего заголовка
        self.heading_title: dict[int, str] = {}  # id(block) → текст ближайшего заголовка
        cur_title = ""
        sec = [0, 0, 0]
        app_idx = -1
        in_app = False
        top = ""
        cur_heading = ""
        cnt = {"fig": 0, "tab": 0, "eq": 0}

        def obj_number(kind):
            cnt[kind] += 1
            if in_app:
                return f"{top}.{cnt[kind]}"
            if mode == "section" and top:
                return f"{top}.{cnt[kind]}"
            return str(cnt[kind])

        for b in self.blocks:
            if b.kind == "heading":
                if "appendix" in b.classes and b.level == 1:
                    in_app = True
                    app_idx += 1
                    letter = APPENDIX_LETTERS[app_idx] if app_idx < len(APPENDIX_LETTERS) else str(app_idx + 1)
                    top = letter
                    sec = [0, 0, 0]
                    cnt = {"fig": 0, "tab": 0, "eq": 0}
                    self.numbers[id(b)] = letter
                    cur_heading = letter
                elif b.unnumbered:
                    if b.level == 1 and not in_app:
                        top = ""
                    cur_heading = ""
                else:
                    lvl = b.level
                    if in_app:
                        if lvl == 1:
                            lvl = 2
                        sec[lvl - 1] += 1
                        for k in range(lvl, 3):
                            sec[k] = 0
                        num = top + "." + ".".join(str(x) for x in sec[1:lvl])
                    else:
                        sec[lvl - 1] += 1
                        for k in range(lvl, 3):
                            sec[k] = 0
                        num = ".".join(str(x) for x in sec[:lvl])
                        if lvl == 1:
                            top = str(sec[0])
                            if mode == "section":
                                cnt = {"fig": 0, "tab": 0, "eq": 0}
                    self.numbers[id(b)] = num
                    cur_heading = num
                if b.label:
                    want = "app" if "appendix" in b.classes else "sec"
                    self._add_label(b, want, self.numbers.get(id(b), ""), b.text)
            elif b.kind == "figure":
                self.numbers[id(b)] = obj_number("fig")
                if b.label:
                    self._add_label(b, "fig", self.numbers[id(b)], b.text)
            elif b.kind == "table":
                self.numbers[id(b)] = obj_number("tab")
                if b.label:
                    self._add_label(b, "tab", self.numbers[id(b)], b.text)
            elif b.kind == "formula":
                if b.label and not b.unnumbered:
                    self.numbers[id(b)] = obj_number("eq")
                    self._add_label(b, "eq", self.numbers[id(b)], b.math)
            if b.kind == "heading":
                cur_title = b.text
            self.section_of[id(b)] = top
            self.heading_path[id(b)] = cur_heading
            self.heading_title[id(b)] = cur_title

        # порядок цитирования и проверка ссылок
        self.cite_order: list[str] = []
        self.refs_used: set[str] = set()
        for b in self.blocks:
            for nd in self.inline_nodes(b):
                if nd.k == "cite":
                    for key, _ in nd.items:
                        if key not in self.cite_order:
                            self.cite_order.append(key)
                elif nd.k == "ref":
                    self.refs_used.add(nd.v)
        for k in self.config.get("bibliography", {}).get("include", []) or []:
            if k not in self.cite_order:
                self.cite_order.append(k)
        self.cite_num = {k: i + 1 for i, k in enumerate(self.cite_order)}

    def _add_label(self, b: Block, want: str, number: str, title: str):
        lab = b.label
        pref = lab.split(":", 1)[0] if ":" in lab else ""
        if pref != want:
            self.issues.append(Issue("error", "label-prefix",
                                     f"метка «{lab}» у объекта «{_kind_ru(want)}» должна начинаться с «{want}:»",
                                     b.file, b.line, element=b.uid, hint=f"{{#{want}:{lab.split(':', 1)[-1]}}}"))
        if lab in self.labels:
            o = self.labels[lab].block
            self.issues.append(Issue("error", "label-dup", f"метка «{lab}» уже есть в {o.file}:{o.line}",
                                     b.file, b.line, element=b.uid))
            return
        self.labels[lab] = Target(want, number, b, title)

    # ------------------------------------------------------------ helpers ----
    @staticmethod
    def inline_nodes(b: Block):
        """Все inline-узлы блока (включая вложенные) в порядке чтения."""
        seqs = []
        if b.kind in ("heading", "para", "figure", "table"):
            seqs.append(b.inl)
        if b.kind == "figure":
            for sf in b.subfigs:
                seqs.append(sf[0])
        if b.kind == "table":
            for c in b.header:
                seqs.append(c[2])
            for row in b.rows:
                for c in row:
                    seqs.append(c[2])
        if b.kind == "list":
            for it in b.items:
                seqs.append(it[4])
        for s in seqs:
            yield from walk(s)

    def block_calcs(self, b: Block) -> set[str]:
        """Расчёты, от которых зависит блок (рисунок-выход, таблица-выход, числа)."""
        deps = set()
        paths = [b.path] if b.kind == "figure" and b.path else []
        paths += [sf[1] for sf in b.subfigs]
        if b.kind == "table" and b.attrs.get("file"):
            paths.append(b.attrs["file"])
        for p in paths:
            m = re.match(r"^calc/([^/]+)/out/", p)
            if m:
                deps.add(m.group(1))
        from lde import mathins
        maths = [b.math] if b.kind == "formula" else []
        for nd in self.inline_nodes(b):
            if nd.k == "math":
                maths.append(nd.v)
        for m in maths:
            for kind, arg, _, _ in mathins.find(m):
                if kind == "val":
                    deps.add(arg.split(".", 1)[0])
        for nd in self.inline_nodes(b):
            if nd.k == "val":
                deps.add(nd.v.split(".", 1)[0])
            elif nd.k == "q":
                q = self.quantities.get(nd.v)
                if q and isinstance(q.value, str) and "." in q.value:
                    deps.add(q.value.split(".", 1)[0])
        return deps

    EXPL_RE = re.compile(r"^\s*(?:где\s+)?(\{[^{}]+\}|\$[^$]+\$|[^\s–—\-$]{1,14})\s*[–—\-]\s+\S")

    def explication_roles(self) -> dict[int, str]:
        """Пояснения к формуле: первая строка «где X – …» (gde), следующие «Y – …» (cont)."""
        roles: dict[int, str] = {}
        prev, prev_role = None, None
        for b in self.blocks:
            if b.kind == "comment":
                continue
            role = None
            if b.kind == "para":
                if prev is not None and prev.kind == "formula" and re.match(r"^\s*где\b", b.text):
                    role = "gde"
                elif prev_role in ("gde", "cont") and self.EXPL_RE.match(b.text):
                    role = "cont"
            if role:
                roles[id(b)] = role
            prev, prev_role = b, role
        return roles

    def calc_statuses(self) -> dict[str, calcmod.CalcStatus]:
        return {k: calcmod.status(self.root, c, self.calcs) for k, c in self.calcs.items()}

    def value(self, path: str) -> dict | None:
        """{=calc.path} → {'value','unit','digits',...} или None."""
        ck, _, rest = path.partition(".")
        c = self.calcs.get(ck)
        if not c or not rest:
            return None
        return calcmod.lookup_value(c, rest)

    def block_by_uid(self, uid: str) -> Block | None:
        for b in self.blocks:
            if b.uid == uid:
                return b
        return None

    def title_of(self, b: Block) -> str:
        """Короткое человеческое имя блока: «Рисунок 14», «Формула (5)», «абзац в 5.2»."""
        n = self.numbers.get(id(b), "")
        where = self.heading_path.get(id(b), "")
        if not where and self.heading_title.get(id(b)):
            where = f"разделе «{self.heading_title[id(b)]}»"
        if b.kind == "heading":
            return f"Раздел {n}" if n else f"Раздел «{b.text}»"
        if b.kind == "figure":
            return f"Рисунок {n}"
        if b.kind == "table":
            return f"Таблица {n}"
        if b.kind == "formula":
            return f"Формула ({n})" if n else f"формула в {where}" if where else "формула"
        if b.kind == "list":
            return f"перечисление в {where}" if where else "перечисление"
        if b.kind == "conflict":
            return f"конфликт слияния в {where}" if where else "конфликт слияния"
        return f"абзац в {where}" if where else "абзац"


def list_documents(root: Path) -> list[dict]:
    """Документы проекта: основной + documents/<ключ>/document.toml. Общая база — величины,
    расчёты, источники, сокращения, рисунки."""
    root = Path(root)
    out = []
    try:
        raw = tomllib.loads((root / "lde.toml").read_text(encoding="utf-8"))
    except Exception:
        raw = {}
    t = raw.get("title", {}) or {}
    out.append({"key": "main", "title": t.get("topic") or (raw.get("document", {}) or {}).get("title") or "Основной документ",
                "name": (raw.get("document", {}) or {}).get("name", "report")})
    dd = root / "documents"
    if dd.is_dir():
        for d in sorted(dd.iterdir()):
            f = d / "document.toml"
            if f.exists():
                try:
                    r = tomllib.loads(f.read_text(encoding="utf-8"))
                except Exception:
                    r = {}
                out.append({"key": d.name, "title": (r.get("title", {}) or {}).get("topic") or d.name,
                            "name": (r.get("document", {}) or {}).get("name", d.name)})
    return out


def _kind_ru(k: str) -> str:
    return {"fig": "рисунок", "tab": "таблица", "eq": "формула", "sec": "раздел", "app": "приложение"}[k]
