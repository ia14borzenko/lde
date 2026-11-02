"""lde import-rde: разовая миграция проекта RDE (objects/…) в проект LDE поверх import-docx.

Что переносится:
  документ report.doc.rde (это DOCX) — через import-docx, но вставки RDE распознаются:
    rde:formula:vN → LaTeX из objects/formulas/<id>/formula.toml (точнее обратной конвертации OMML)
    rde:vN (constant) → вставка величины {ключ}
    rde:vN (figure) → рисунок-выход расчёта calc/<ключ>/out/<файл>
  артефакты compute → calc/<ключ>/ (скрипт + calc.toml; параметры берутся из словаря P скрипта,
    в скрипт добавляется одна строка P.update(lde_calc.params()))
  артефакт constants → quantities.toml (единицы; смысл — из строк пояснений «где X – …»)
  источники → sources/<ключ>/ и поле file у записи библиографии

Нумерация рисунков: RDE перенумеровал подписи, не меняя ссылок в тексте. Если доступен исходный
DOCX (--original или путь из import.json), номера в тексте сопоставляются с исходной нумерацией;
спорные случаи решаются по совпадению слов с подписью и перечисляются в отчёте импорта.
"""
from __future__ import annotations

import ast
import difflib
import json
import re
import shutil
import tomllib
import zipfile
from pathlib import Path

from lxml import etree

from lde.importers import mathconv
from lde.importers.docx_import import CAPTION_FIG, DocxImporter, M, Seg, W, slug, w, wv
from lde.quantities import latex_from_key

PARAM_UNITS = [  # единицы параметров по именам (угадываются; проверяются автором в Studio)
    (r"^(PRF|f\w*|F\w*|B)$", "Гц"), (r"^(R\w*|x\d*|y\d*|X\w*|dR|lam)$", "м"),
    (r"^(T\w*|t\w*)$", "с"), (r"^(V|c)$", "м/с"), (r"^(Kr)$", "Гц/с"),
]
PARAM_TITLES = {
    "c": "Скорость света", "f0": "Несущая частота", "V": "Скорость носителя", "B": "Девиация частоты",
    "Tp": "Длительность импульса", "Fs": "Частота дискретизации", "Ntau": "Число отсчётов по дальности",
    "PRF": "Частота повторения импульсов", "N": "Число импульсов", "R_ref": "Опорная дальность",
    "R0": "Дальность до цели", "x0": "Азимутальное положение цели", "f_ref": "Опорная доплеровская частота",
    "f_plot": "Доплеровская частота для графика",
}
STOP = {"chislennyy", "primer", "raschet", "odna", "tochechnaya", "cel", "dlya"}


def _param_unit(name: str) -> str:
    for rx, u in PARAM_UNITS:
        if re.match(rx, name):
            return u
    return ""


def _value_unit(path: str) -> str:
    parts = path.split(".")
    if len(parts) == 2 and parts[0] == "params":
        return _param_unit(parts[1])
    if any(x.lower().endswith("_db") or x.lower() == "db" for x in parts):
        return "дБ"
    last = parts[-1].lower()
    if last in ("width", "resolution", "res_m"):
        return "м"
    if last.endswith("_m") or last in ("range_m", "azimuth_m"):
        return "м"
    if last.endswith("_db") or "db" in path.lower().split(".")[-2:][0]:
        return "дБ"
    return ""


def calc_key_from_title(title: str) -> str:
    t = slug(title, 6, 60).split("-")
    t = [x for x in t if x not in STOP]
    return "-".join(t[:2])[:14].strip("-") or "calc"


def extract_params(script: str):
    """Литеральный словарь P = dict(...) в скрипте → (параметры, номер строки конца вызова)."""
    tree = ast.parse(script)
    for node in tree.body:
        if isinstance(node, ast.Assign) and len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) \
                and node.targets[0].id in ("P", "PARAMS", "params") and isinstance(node.value, ast.Call) \
                and getattr(node.value.func, "id", "") == "dict":
            params = {}
            for kw in node.value.keywords:
                try:
                    params[kw.arg] = ast.literal_eval(kw.value)
                except ValueError:
                    pass
            return node.targets[0].id, params, node.end_lineno
    return None, {}, 0


def toml_value(v) -> str:
    if isinstance(v, bool):
        return "true" if v else "false"
    if isinstance(v, int):
        return str(v)
    if isinstance(v, float):
        r = repr(v)
        return r if ("e" in r or "." in r or "inf" in r) else r + ".0"
    return json.dumps(v, ensure_ascii=False)


class RdeMigration:
    def __init__(self, src: Path, dst: Path, original: Path | None = None, calc_keys: dict | None = None):
        self.src = Path(src)
        self.dst = Path(dst)
        self.obj = self.src / "objects"
        self.original = Path(original) if original else None
        self.calc_keys = dict(calc_keys or {})
        self.notes: list[str] = []

    # --------------------------------------------------------------- objects ----
    def load_objects(self):
        self.formulas = {}
        for d in (self.obj / "formulas").glob("*"):
            p = d / "formula.toml"
            if p.exists():
                self.formulas[d.name] = tomllib.loads(p.read_text(encoding="utf-8"))
        self.artifacts = {}
        for d in (self.obj / "artifacts").glob("*"):
            p = d / "artifact.toml"
            if p.exists():
                self.artifacts[d.name] = (d, tomllib.loads(p.read_text(encoding="utf-8")))
        self.sources = []
        for d in (self.obj / "sources").glob("*"):
            p = d / "source.toml"
            if p.exists():
                self.sources.append((d, tomllib.loads(p.read_text(encoding="utf-8"))))
        docs = sorted((self.obj / "documents").glob("*/document.toml"))
        if not docs:
            raise RuntimeError("в проекте RDE нет документов (objects/documents/*/document.toml)")
        dd = docs[0].parent
        dt = tomllib.loads(docs[0].read_text(encoding="utf-8"))
        self.docx = dd / dt.get("file", "report.doc.rde")
        if not self.original:
            ij = dd / "import.json"
            if ij.exists():
                srcp = json.loads(ij.read_text(encoding="utf-8")).get("source")
                if srcp and Path(srcp).exists():
                    self.original = Path(srcp)
        # вставки RDE (customXml)
        self.inserts = {}
        with zipfile.ZipFile(self.docx) as z:
            for n in z.namelist():
                if n.startswith("customXml/item") and n.endswith(".xml") and "Props" not in n:
                    x = etree.fromstring(z.read(n))
                    for ins in x.iter("{https://rde.dev/ns/doc}insert"):
                        self.inserts[ins.get("id")] = dict(ins.attrib)

    # ------------------------------------------------------------------ calcs ----
    def migrate_calcs(self, rep):
        self.art_key = {}
        self.const_art = None
        for aid, (d, a) in self.artifacts.items():
            if a.get("type") == "constants":
                self.const_art = (aid, d, a)
                continue
            if a.get("type") != "compute":
                rep.lost.append(f"артефакт «{a.get('title')}» типа {a.get('type')} не перенесён")
                continue
            key = self.calc_keys.get(aid) or calc_key_from_title(a.get("title", aid))
            self.art_key[aid] = key
            cd = self.dst / "calc" / key
            cd.mkdir(parents=True, exist_ok=True)
            entry = a.get("runtime", {}).get("entry", "script.py")
            script = (d / entry).read_text(encoding="utf-8")
            var, params, end = extract_params(script)
            if var:
                lines = script.split("\n")
                lines.insert(end, f'{var}.update(__import__("lde_calc").params())  # LDE: параметры — из calc.toml')
                script = "\n".join(lines)
                rep.approx.append(f"расчёт «{a.get('title')}»: в скрипт добавлена одна строка — параметры из calc.toml; единицы параметров угаданы по именам")
            else:
                rep.approx.append(f"расчёт «{a.get('title')}»: словарь параметров не найден — параметры остались в скрипте")
            (cd / entry).write_text(script, encoding="utf-8")
            if (d / "readme.md").exists():
                shutil.copy2(d / "readme.md", cd / "readme.md")
            outs = [o[4:] if o.startswith("out/") else o for o in a.get("outputs", [])]
            vals = {}
            mj = d / "out" / "metrics.json"
            if mj.exists():
                def rec(prefix, x):
                    if isinstance(x, dict):
                        for k, v in x.items():
                            rec(f"{prefix}.{k}" if prefix else k, v)
                    elif isinstance(x, (int, float)) and not isinstance(x, bool):
                        u = _value_unit(prefix)
                        if u:
                            vals[prefix] = u
                rec("", json.loads(mj.read_text(encoding="utf-8")))
            t = [f'title = {json.dumps(a.get("title", key), ensure_ascii=False)}', f'script = "{entry}"',
                 f'timeout = {int(a.get("timeout_s", 600))}', "inputs = []",
                 "outputs = [" + ", ".join(json.dumps(o) for o in outs) + "]", "", "[params]"]
            for k, v in params.items():
                u = _param_unit(k)
                title = PARAM_TITLES.get(k, "")
                t.append(f'{k} = {{ value = {toml_value(v)}, unit = "{u}", title = "{title}" }}')
            if vals:
                t += ["", "[values]"]
                for k, u in vals.items():
                    t.append(f'{json.dumps(k)} = {{ unit = "{u}" }}')
            (cd / "calc.toml").write_text("\n".join(t) + "\n", encoding="utf-8")
            rep.inc("расчёты")

    def migrate_quantities(self, rep):
        if not self.const_art:
            return
        aid, d, a = self.const_art
        lines = ["# Величины (перенесены из «" + a.get("title", "") + "» RDE).",
                 "# Смысл взят из пояснений «где X – …» в тексте; где пусто — допишите.", ""]
        self.qkeys = set()
        for key, v in (a.get("values") or {}).items():
            self.qkeys.add(key)
            lines.append(f"[{json.dumps(key, ensure_ascii=False)}]")
            lines.append('meaning = ""')
            lines.append(f'unit = {json.dumps(v.get("unit", ""), ensure_ascii=False)}')
            lines.append("")
            rep.inc("величины")
        (self.dst / "quantities.toml").write_text("\n".join(lines), encoding="utf-8")

    # --------------------------------------------------------------- resolver ----
    def resolver(self, tag, sdt, block=False, paragraph=None):
        m = re.match(r"^rde:formula:(v\d+)$", tag or "")
        if m and sdt is not None and not block:
            content = sdt.find(w("sdtContent"))
            visible = content is not None and (
                any(etree.QName(e).namespace == M for e in content.iter())
                or "".join(content.itertext()).strip())
            if not visible:
                # пустая вставка RDE: в документе её не видно — не выдумываем содержимое
                self.empty_inserts.append(m.group(1))
                return []
        if m:
            ins = self.inserts.get(m.group(1), {})
            fid = re.match(r"rde://formula/([^@]+)", ins.get("target", ""))
            latex = None
            if fid and fid.group(1) in self.formulas:
                latex = self.formulas[fid.group(1)].get("latex")
            latex = latex or ins.get("cached")
            if not latex:
                return None
            seg = Seg("m", mathconv.normalize_latex(latex))
            seg.display = block
            self.counts["формулы из objects/formulas"] = self.counts.get("формулы из objects/formulas", 0) + 1
            if block and paragraph is not None:
                rest = "".join(t.text or "" for t in paragraph.iter(w("t"))
                               if not any(etree.QName(a).namespace == M for a in t.iterancestors()))
                return [seg, Seg("t", rest)]
            return seg
        m = re.match(r"^rde:(v\d+)$", tag or "")
        if m:
            ins = self.inserts.get(m.group(1), {})
            kind = ins.get("kind")
            if kind == "constant":
                key = ins.get("selector") or ins.get("target", "").rsplit("/", 1)[-1]
                self.counts["вставки величин RDE"] = self.counts.get("вставки величин RDE", 0) + 1
                return Seg("q", key)
            if kind == "figure":
                mm = re.match(r"rde://artifact/([^/]+)/out/(.+)$", ins.get("target", ""))
                if mm and mm.group(1) in self.art_key:
                    self.counts["рисунки-выходы расчётов"] = self.counts.get("рисунки-выходы расчётов", 0) + 1
                    return Seg("img", f"calc/{self.art_key[mm.group(1)]}/out/{mm.group(2)}")
        return None

    # ----------------------------------------------------- исходная нумерация ----
    def original_captions(self) -> dict:
        """Номер рисунка в исходном DOCX → текст подписи."""
        res = {}
        if not self.original or not self.original.exists():
            return res
        with zipfile.ZipFile(self.original) as z:
            x = etree.fromstring(z.read("word/document.xml"))
        for p in x.iter(w("p")):
            txt = "".join(t.text or "" for t in p.iter(w("t")))
            mm = CAPTION_FIG.match(txt)
            if mm:
                res.setdefault(mm.group(1), mm.group(2).strip())
        return res

    def setup_ref_hint(self, imp: DocxImporter):
        orig = self.original_captions()
        self.orig_caps = orig
        if not orig:
            return None

        def norm(s):
            return re.sub(r"[^а-яёa-z0-9 ]", " ", s.lower())

        def stems(s):
            ab = set(re.findall(r"\b[А-ЯЁA-Z]{2,4}\b", s))
            return {x[:4] for x in norm(s).split() if len(x) >= 4} | ab

        def ref_hint(kind, num, ctx, importer):
            if kind != "fig":
                return None
            if not hasattr(importer, "_orig_map"):
                omap = {}
                for it in importer.items:
                    if it.kind == "figure" and it.caption is not None:
                        cap = importer.plain(it.caption.segs)
                        best, score = None, 0.0
                        for on, ot in orig.items():
                            r = difflib.SequenceMatcher(None, norm(cap)[:80], norm(ot)[:80]).ratio()
                            if r > score:
                                best, score = on, r
                        if best and score > 0.75:
                            omap[best] = it
                importer._orig_map = omap
            a = importer._orig_map.get(num)          # по исходной нумерации автора
            b = importer.fig_by_num.get(num)          # по нумерации подписей RDE
            if a is None or b is None or a is b:
                tgt = a or b
                return tgt.label if tgt else None
            ca, cb = importer.plain(a.caption.segs), importer.plain(b.caption.segs)
            sc = stems(ctx)
            sa = len(sc & stems(ca)) / max(1, len(stems(ca)))
            sb = len(sc & stems(cb)) / max(1, len(stems(cb)))
            # по умолчанию — исходная нумерация автора; подписи RDE — только если контекст явно про них
            pick = b if sb > sa else a
            importer.rep.refs.append({"kind": "fig", "number": num, "label": pick.label, "where": "",
                                      "note": f"спорно: по исходной нумерации — «{ca[:50]}», по подписям RDE — «{cb[:50]}»; "
                                              f"выбрано по словам контекста: «{importer.plain(pick.caption.segs)[:50]}»"})
            importer.rep.approx.append(f"ссылка «рисунок {num}»: RDE перенумеровал подписи; выбрано «{importer.plain(pick.caption.segs)[:60]}» (проверьте)")
            return pick.label

        return ref_hint

    # ---------------------------------------------------------------- sources ----
    def migrate_sources(self, rep, bib_keys, bib_texts):
        bib_path = self.dst / "refs.bib"
        text = bib_path.read_text(encoding="utf-8")
        files_by_key: dict[str, list[str]] = {}
        for d, s in self.sources:
            ref = s.get("reference", {})
            surnames = [a.split(",")[0].strip() for a in ref.get("authors", [])]
            best = None
            for k, t in zip(bib_keys, bib_texts):
                if surnames and all(sn in t for sn in surnames):
                    best = k
                    break
            if not best:
                best = slug(surnames[0] if surnames else s.get("title", "src"), 1, 12) + str(ref.get("year", ""))
                auth = " and ".join(ref.get("authors", []))
                text += (f"\n@book{{{best},\n  author = {{{auth}}},\n  title = {{{ref.get('title', '')}}},\n"
                         f"  publisher = {{{ref.get('publisher', '')}}},\n  year = {{{ref.get('year', '')}}},\n}}\n")
                rep.approx.append(f"источник «{s.get('title')}» не найден в списке документа — добавлен как {best}")
            sd = self.dst / "sources" / best
            sd.mkdir(parents=True, exist_ok=True)
            for f in sorted((d / "files").glob("*")):
                shutil.copy2(f, sd / f.name)
                files_by_key.setdefault(best, []).append(f"sources/{best}/{f.name}")
            rep.inc("файлы источников", len(list((d / "files").glob("*"))))
        for key, files in files_by_key.items():
            text = re.sub(r"(@\w+\{" + re.escape(key) + r",\n)", r"\1  file = {" + "; ".join(files) + "},\n", text, count=1)
        bib_path.write_text(text, encoding="utf-8")

    # ------------------------------------------------------------- meanings ----
    def fill_meanings(self, rep):
        """Смысл величин — из строк пояснений «где X – смысл, единица;» (X — вставка, формула или текст)."""
        from lde.project import Project
        from lde.quantities import norm_math
        proj = Project(self.dst)
        qs = proj.quantities
        by_norm = {norm_math(q.tex): k for k, q in qs.items()}
        by_text = {}
        for k, q in qs.items():
            for v in q.text_variants():
                by_text.setdefault(v, k)
        found = {}
        for b in proj.blocks:
            if b.kind != "para":
                continue
            m = re.match(r"^\s*(?:где\s+)?(\{[^{}]+\}|\$[^$]+\$|[^\s–—]+)\s*[–—\-]\s*(.+?)\s*[;.]?\s*$", b.text)
            if not m:
                continue
            left, meaning = m.group(1), m.group(2)
            key = None
            if left.startswith("{"):
                key = left[1:-1]
            elif left.startswith("$"):
                key = by_norm.get(norm_math(left[1:-1]))
            else:
                key = by_text.get(left)
            if key not in qs or len(meaning) < 3:
                continue
            meaning = re.sub(r",\s*[^,]{1,10}$", "", meaning).strip()   # «…, м» — единица отдельно
            meaning = re.sub(r"\$[^$]*\$|\{[^{}]*\}", lambda x: x.group(0), meaning)
            found.setdefault(key, meaning)
        qp = self.dst / "quantities.toml"
        if not qp.exists():
            return
        t = qp.read_text(encoding="utf-8")
        for key, meaning in found.items():
            meaning = re.sub(r"[{}$\\]", "", meaning)
            t = re.sub(r'(\[' + re.escape(json.dumps(key, ensure_ascii=False)) + r'\]\nmeaning = )""',
                       lambda mm: mm.group(1) + json.dumps(meaning, ensure_ascii=False), t, count=1)
        qp.write_text(t, encoding="utf-8")
        rep.counts["смысл величин из пояснений «где …»"] = len(found)

    # -------------------------------------------------------------------- run ----
    def run(self, run_calcs=True, log=print):
        self.load_objects()
        self.dst.mkdir(parents=True, exist_ok=True)
        self.counts = {}
        self.empty_inserts = []
        imp = DocxImporter(self.docx, self.dst, resolver=self.resolver)
        imp.rep.counts = self.counts
        self.migrate_calcs(imp.rep)
        self.migrate_quantities(imp.rep)
        imp.ref_hint = self.setup_ref_hint(imp)
        if self.original:
            imp.rep.moved.append(f"исходная нумерация рисунков восстановлена по {self.original.name}")
        else:
            imp.rep.approx.append("исходный DOCX недоступен: ссылки на рисунки сопоставлены с подписями RDE как есть")
        imp.run(extra_config="")
        if self.empty_inserts:
            imp.rep.approx.append(f"пустые вставки формул RDE ({len(self.empty_inserts)}: {', '.join(self.empty_inserts)}) "
                                  "в документе не видны — пропущены (дефект RDE «вставка ушла не в тот абзац»)")
        self.migrate_sources(imp.rep, imp.bib_keys, imp.bib)
        self.fill_meanings(imp.rep)
        imp.write_report([str(p.relative_to(self.dst)) for p in sorted((self.dst / "text").glob("*.md"))])
        if run_calcs:
            from lde import calcs as cm
            from lde.project import Project
            proj = Project(self.dst)
            for k in cm.topo_order(proj.calcs, list(proj.calcs)):
                log(f"Расчёт «{proj.calcs[k].name}» …")
                r = cm.run_calc(proj.root, proj.calcs[k], proj.calcs)
                log(f"  {'готово за %.1f с' % r['duration'] if r['ok'] else 'упал: ' + r['error']}")
        return imp.rep


def import_rde(src, dst, original=None, run_calcs=True, calc_keys=None, log=print):
    return RdeMigration(Path(src), Path(dst), original, calc_keys).run(run_calcs=run_calcs, log=log)
