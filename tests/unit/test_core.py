"""Юнит-тесты там, где логика ломается тихо: разбор формата, числа, типографика, линтер."""
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[2]))

from lde.fmt import FormatError, format_value  # noqa: E402
from lde.quantities import Quantity, latex_from_key, norm_math  # noqa: E402
from lde.textfmt import parse_inline, parse_text, replace_block  # noqa: E402
from lde.emit_latex import fix_math  # noqa: E402
from lde.bib import cite_text  # noqa: E402


class TestInline(unittest.TestCase):
    def kinds(self, s):
        return [(n.k, n.v) for n in parse_inline(s) if n.k != "text"]

    def test_inserts(self):
        k = self.kinds("Дальность {R_ц} и {=csa.R0 км}, см. @fig:x и (@eq:a) [@cw2005, с. 45; @b]. $x^2$")
        self.assertEqual(k[:4], [("q", "R_ц"), ("val", "csa.R0"), ("ref", "fig:x"), ("ref", "eq:a")])
        self.assertEqual(k[4][0], "cite")
        self.assertEqual(k[5], ("math", "x^2"))

    def test_cite_items(self):
        n = [x for x in parse_inline("[@a, с. 4; @b]") if x.k == "cite"][0]
        self.assertEqual(n.items, [("a", "с. 4"), ("b", "")])

    def test_email_not_ref(self):
        self.assertEqual(self.kinds("почта ivan@fig:x.ru"), [])

    def test_escape(self):
        self.assertEqual(self.kinds(r"\{R\} \$ \@fig:x"), [])

    def test_ref_trailing_dot(self):
        self.assertEqual(self.kinds("на рисунке @fig:a.")[0], ("ref", "fig:a"))

    def test_raw_command_not_quantity(self):
        self.assertEqual(self.kinds(r"\textbf{жирно}"), [])

    def test_offsets(self):
        s = "ab {R_ц} cd"
        n = [x for x in parse_inline(s) if x.k == "q"][0]
        self.assertEqual(s[n.s:n.e], "{R_ц}")


class TestBlocks(unittest.TestCase):
    SRC = """# Раздел {#sec:a}

Абзац строка 1
строка 2
$$ x = 1 $$ {#eq:x}
где {x} – икс.

![Подпись [@k]](calc/a/out/f.png){#fig:f width=0.5}

: Таблица {#tab:t}
| A | $|x|$ |
|---|:---:|
| 1 | 2 |

- пункт 1
- пункт 2

$$
a = b
$$
"""

    def test_kinds_and_lines(self):
        bl = parse_text(self.SRC, "t.md")
        self.assertEqual([b.kind for b in bl], ["heading", "para", "formula", "para", "figure", "table", "list", "formula"])
        self.assertEqual((bl[1].line, bl[1].end), (3, 4))
        self.assertEqual(bl[2].label, "eq:x")
        self.assertEqual(bl[4].path, "calc/a/out/f.png")
        self.assertEqual(bl[4].attrs["width"], "0.5")
        self.assertEqual(bl[5].header[1][0], "$|x|$")
        self.assertEqual(bl[5].aligns, ["l", "c"])
        self.assertEqual(bl[7].math, "a = b")

    def test_heading_trailing_quantity_not_attrs(self):
        b = parse_text("# Зависимость от {R_ц}")[0]
        self.assertEqual(b.text, "Зависимость от {R_ц}")
        self.assertEqual(b.label, "")

    def test_replace_block_minimal(self):
        bl = parse_text(self.SRC, "t.md")
        new = replace_block(self.SRC, bl[1], "Новый абзац")
        self.assertEqual(new.split("\n")[2], "Новый абзац")
        self.assertEqual(len(new.split("\n")), len(self.SRC.split("\n")) - 1)
        self.assertEqual(new.split("\n")[3:], self.SRC.split("\n")[4:])


class TestFormat(unittest.TestCase):
    def t(self, *a):
        return format_value(*a)["text"]

    def test_decimal_comma_and_prefix(self):
        self.assertEqual(self.t(3e9, "Гц"), "3 ГГц")
        self.assertEqual(self.t(5e-6, "с"), "5 мкс")
        self.assertEqual(self.t(1.49896229, "м"), "1,499 м")
        self.assertEqual(self.t(1800.0, "Гц"), "1800 Гц")
        self.assertEqual(self.t(15000, ""), "15 000")

    def test_options(self):
        self.assertEqual(self.t(1150.0, "м", None, "км"), "1,15 км")
        self.assertEqual(self.t(0.0427, "", None, ".2"), "0,04")
        self.assertEqual(self.t(150.0, "м/с", None, "-"), "150")
        with self.assertRaises(FormatError):
            format_value(1.0, "м", None, "Гц")

    def test_negative(self):
        self.assertEqual(self.t(-2.237, "дБ"), "−2,237 дБ")


class TestQuantities(unittest.TestCase):
    def test_latex(self):
        self.assertEqual(latex_from_key("R_ц"), r"R_{\text{ц}}")
        self.assertEqual(latex_from_key("L_max21"), r"L_{\mathrm{max}21}")
        self.assertEqual(latex_from_key("φ_1"), r"\varphi_{1}")
        self.assertEqual(latex_from_key("R_п.об"), r"R_{\text{п.об}}")
        self.assertEqual(latex_from_key("β′"), r"\beta'")
        self.assertEqual(latex_from_key("ΔL_БЛ"), r"\Delta L_{\text{БЛ}}")

    def test_variants(self):
        v = Quantity("f_оп").text_variants()
        for x in ("fоп", "f_оп", "f_(оп)", "f_{оп}"):
            self.assertIn(x, v)
        self.assertIn("φ₁", Quantity("φ_1").text_variants())

    def test_norm(self):
        self.assertEqual(norm_math(r"R_{\text{ц}}"), norm_math("R_{ц}"))


class TestMath(unittest.TestCase):
    def test_cyr_wrap(self):
        self.assertEqual(fix_math("R_{оп} + \\text{дБ}"), "R_{\\text{оп}} + \\text{дБ}")
        self.assertEqual(fix_math("R_{п.об}"), "R_{\\text{п.об}}")


class TestCite(unittest.TestCase):
    def test_ranges(self):
        self.assertEqual(cite_text([(15, ""), (16, ""), (17, ""), (18, "")]), "[15–18]")
        self.assertEqual(cite_text([(1, ""), (2, "")]), "[1, 2]")
        self.assertEqual(cite_text([(3, "с. 45")]), "[3, с. 45]")


if __name__ == "__main__":
    unittest.main()


class TestImportPieces(unittest.TestCase):
    def test_mtef_functions(self):
        from lde.importers.mtef import _merge_mathrm
        self.assertEqual(_merge_mathrm(r"Vt\mathrm{c}\mathrm{o}\mathrm{s}θ"), r"Vt\cos θ")
        self.assertEqual(_merge_mathrm(r"\mathrm{t}\mathrm{g}x"), r"\operatorname{tg}x")

    def test_normalize(self):
        from lde.importers.mathconv import normalize_latex
        self.assertEqual(normalize_latex(r"L_{\max}_{21}"), r"L_{\max21}")
        self.assertIn(r"\lambda", normalize_latex("λ = c/f"))
        self.assertEqual(normalize_latex(r"a{\thinsp}b"), r"a{\,}b")

    def test_symbol_font(self):
        from lde.importers.docx_import import symbol_char
        self.assertEqual(symbol_char("Symbol", "F077"), "ω")
        self.assertEqual(symbol_char("Symbol", "F057"), "Ω")

    def test_eq_slug(self):
        from lde.importers.docx_import import eq_slug
        self.assertEqual(eq_slug(r"\alpha=\frac{\lambda}{d}", "1"), "alpha")
        self.assertEqual(eq_slug(r"=1", "7"), "f7")


class TestBib(unittest.TestCase):
    def test_book(self):
        from lde.bib import BibEntry, format_entry
        e = BibEntry("cw", "book", {"author": "Cumming, I. G. and Wong, F. H.", "title": "Digital Processing of SAR Data",
                                    "address": "Boston", "publisher": "Artech House", "year": "2005", "pagetotal": "625"})
        self.assertEqual(format_entry(e), "Cumming, I. G. Digital Processing of SAR Data / I. G. Cumming, F. H. Wong. – Boston : Artech House, 2005. – 625 p.")

    def test_formatted(self):
        from lde.bib import BibEntry, format_entry
        self.assertEqual(format_entry(BibEntry("x", "misc", {"formatted": "Готовое описание."})), "Готовое описание.")


class TestDocuments(unittest.TestCase):
    """Несколько документов в одном проекте: своя нумерация и текст, общая база объектов."""

    def test_two_documents_share_base(self):
        import tempfile
        from lde.check import run_check
        from lde.cli_more import new_document
        from lde.project import Project, list_documents
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "text").mkdir()
            (root / "lde.toml").write_text('[document]\nname = "report"\nbibliography = true\n[title]\ntopic = "Отчёт"\n',
                                           encoding="utf-8")
            (root / "quantities.toml").write_text('["R_ц"]\nmeaning = "дальность"\nunit = "м"\n', encoding="utf-8")
            (root / "refs.bib").write_text("@misc{a, formatted = {Источник А.}}\n@misc{b, formatted = {Источник Б.}}\n",
                                           encoding="utf-8")
            (root / "text" / "01.md").write_text("# Раздел {#sec:a}\n\nДальность {R_ц} [@a].\n", encoding="utf-8")
            r = new_document(root, "pz", "Пояснительная записка")
            self.assertTrue(r["ok"], r)
            self.assertFalse(new_document(root, "pz")["ok"])
            self.assertFalse(new_document(root, "main")["ok"])
            (root / "documents" / "pz" / "01-текст.md").write_text(
                "# Второй {#sec:b}\n\nТа же величина {R_ц} [@b].\n", encoding="utf-8")
            self.assertEqual([d["key"] for d in list_documents(root)], ["main", "pz"])
            main, pz = Project(root), Project(root, "pz")
            self.assertEqual(pz.name, "pz")
            self.assertEqual(main.name, "report")
            self.assertNotEqual(main.build_dir, pz.build_dir)
            self.assertEqual(pz.config_file, "documents/pz/document.toml")
            self.assertIn("R_ц", pz.quantities)          # общая база
            self.assertEqual([b.file for b in pz.blocks if b.kind == "heading"], ["documents/pz/01-текст.md"])
            self.assertEqual(pz.cite_num.get("b"), 1)     # своя нумерация источников
            self.assertNotIn("a", pz.cite_num)
            self.assertTrue(pz.config["document"].get("bibliography"))  # наследование настроек
            # источник, процитированный только в другом документе, — не «нигде не цитируется»
            for p in (main, pz):
                self.assertFalse([i for i in run_check(p) if i.rule == "uncited"])


class TestManualStyle(unittest.TestCase):
    """{style …} над блоком: разбор, смещения вставок, правка из Studio, LaTeX."""

    SRC = "Абзац.\n\n{style align=center indent=0 size=12 bold=no before=-3}\nТекст {R_ц} и {=c.x}.\n\n$$ x $$ {#eq:x}\n"

    def test_parse_and_offsets(self):
        bs = parse_text(self.SRC, "t.md")
        b = bs[1]
        self.assertEqual(b.kind, "para")
        self.assertEqual(b.style, {"align": "center", "indent": 0.0, "size": 12.0, "bold": False, "before": -3.0})
        self.assertTrue(b.src.startswith("{style"))
        self.assertEqual(b.line, 3)
        q = [n for n in b.inl if n.k == "q"][0]
        self.assertEqual(b.src[q.s:q.e], "{R_ц}")
        self.assertEqual(b.line_of(q.s), 4)
        self.assertEqual(b.text, "Текст {R_ц} и {=c.x}.")
        self.assertEqual(bs[2].index, 2)          # строка оформления — не отдельный блок

    def test_errors(self):
        b = parse_text("{style align=middle size=200 foo=1}\nАбзац.\n")[0]
        self.assertEqual(len(b.errors), 3)
        dangling = parse_text("{style size=12}\n\nАбзац.\n")
        self.assertTrue(dangling[0].errors)
        self.assertFalse(dangling[1].style)
        f = parse_text("{style align=center indent=1}\n$$ x $$\n")[0]
        self.assertTrue(any("не действует" in e for _, e in f.errors))

    def test_style_line_ends_paragraph(self):
        bs = parse_text("Первый абзац\n{style bold}\nВторой\n")
        self.assertEqual([b.kind for b in bs], ["para", "para"])
        self.assertEqual(bs[1].style, {"bold": True})

    def test_clean_and_format(self):
        from lde.style import clean, format_line
        st = clean({"size": "12,5", "bold": True, "align": "", "keep": False}, "para")
        self.assertEqual(format_line(st), "{style size=12.5 bold=yes keep=no}")
        with self.assertRaises(ValueError):
            clean({"indent": 1}, "formula")

    def test_latex(self):
        from lde.style import latex_font
        t = latex_font({"size": 12, "line": 1})
        self.assertLess(t.index("setstretch"), t.index("fontsize"))   # иначе \setstretch сбросит кегль

    def test_studio_set_style(self):
        import subprocess
        import tempfile
        from lde.project import Project
        from lde.studio import model
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            (root / "text").mkdir()
            (root / "lde.toml").write_text("[document]\n", encoding="utf-8")
            (root / "text" / "01.md").write_text("# Раздел\n\nАбзац {R}.\n\nВторой.\n", encoding="utf-8")
            subprocess.run(["git", "init", "-q", str(root)], check=True)
            b = Project(root).blocks[1]
            r = model.set_style(root, b.uid, b.src, {"align": "right", "size": 10})
            self.assertTrue(r["changed"])
            txt = (root / "text" / "01.md").read_text(encoding="utf-8")
            self.assertIn("{style align=right size=10}\nАбзац {R}.", txt)
            b = Project(root).blocks[1]
            # правка текста из редактора (без строки оформления) оформление сохраняет
            model.save_block(root, b.uid, b.src, "Абзац {R} изменён.")
            b = Project(root).blocks[1]
            self.assertEqual(b.src, "{style align=right size=10}\nАбзац {R} изменён.")
            model.set_style(root, b.uid, b.src, None)
            self.assertEqual((root / "text" / "01.md").read_text(encoding="utf-8"),
                             "# Раздел\n\nАбзац {R} изменён.\n\nВторой.\n")


class TestMathStyle(unittest.TestCase):
    def test_strip_unit_matrix(self):
        from lde.emit_latex import strip_unit_matrix as s
        self.assertEqual(s(r"\exp\left\{\begin{matrix}j\pi\frac{a}{b}\end{matrix}\right\}"), r"\exp\left\{j\pi\frac{a}{b}\right\}")
        keep = r"\begin{matrix}a & b\end{matrix}"
        self.assertEqual(s(keep), keep)
        self.assertEqual(s(r"\begin{matrix}a=\\=\left\{\begin{matrix}x\end{matrix}\right\}\end{matrix}"),
                         r"\begin{matrix}a=\\=\left\{x\right\}\end{matrix}")
        self.assertEqual(s(r"x^\begin{matrix}2\end{matrix}"), r"x^{2}")

    def test_preamble_rules(self):
        pre = (Path(__file__).resolve().parents[2] / "lde" / "tex" / "preamble.tex").read_text(encoding="utf-8")
        self.assertIn(r"\RenewDocumentCommand{\frac}", pre)
        self.assertIn("integral.v1", pre)


class TestVersions(unittest.TestCase):
    def _repo(self, td):
        from lde import gitops
        r = Path(td)
        gitops.ensure_repo(r)
        (r / "a.txt").write_text("v1\n")
        gitops.commit(r, "создание")
        return r

    def test_undo_redo_restore(self):
        import tempfile
        from lde import gitops, versions as V
        with tempfile.TemporaryDirectory() as td:
            r = self._repo(td)
            V.create(r, "Отправлено", kind="share")
            for i in (2, 3):
                (r / "a.txt").write_text(f"v{i}\n")
                gitops.commit(r, f"правка {i}")
            self.assertEqual(V.undo(r)["subject"], "правка 3")
            self.assertEqual(V.undo(r)["subject"], "правка 2")
            with self.assertRaises(RuntimeError):       # первую точку не отменяем
                V.undo(r)
            self.assertEqual((r / "a.txt").read_text(), "v1\n")
            V.redo(r)
            self.assertEqual((r / "a.txt").read_text(), "v2\n")
            (r / "b.txt").write_text("x")
            gitops.commit(r, "новый файл")
            self.assertFalse(V.can_redo(r))             # после новой правки повторять нечего
            V.restore(r, "Отправлено")
            self.assertEqual((r / "a.txt").read_text(), "v1\n")
            self.assertFalse((r / "b.txt").exists())
            self.assertEqual([v["name"] for v in V.list_versions(r)], ["Отправлено"])
            pts = [p["subject"] for s in V.history(r) for p in s["points"]]
            self.assertEqual(pts[0], "Возвращено к версии «Отправлено»")

    def test_word_diff(self):
        from lde.studio.model import word_diff
        w = word_diff("Дальность {R_ц} равна {=c.R0}.", "Дальность {R_ц} цели равна {=c.R1}.")
        self.assertIn(["+", "цели "], w)
        self.assertIn(["-", "{=c.R0}"], w)     # вставка — неделимое «слово»
        self.assertIn(["+", "{=c.R1}"], w)


class TestMerge(unittest.TestCase):
    BASE = "# З {-}\n\nПервый: фильтр подавляет высокие и пропускает низкие.\n\nВторой удалят.\n\nТретий.\n\nЧетвёртый плохо.\n"

    def test_blocks_and_words(self):
        from lde.merge import merge_text
        ours = self.BASE.replace("фильтр", "RC-фильтр").replace("Второй удалят.\n\n", "").replace("плохо", "(А)")
        theirs = self.BASE.replace("низкие.", "низкие частоты.").replace("Третий.", "Третий, исправлен.").replace("плохо", "(Б)")
        text, n = merge_text(self.BASE, ours, theirs)
        self.assertEqual(n, 1)
        self.assertIn("RC-фильтр подавляет высокие и пропускает низкие частоты.", text)   # пословно
        self.assertNotIn("Второй удалят", text)
        self.assertIn("Третий, исправлен.", text)
        self.assertIn("<<<<<<< моё\nЧетвёртый (А).\n||||||| было\nЧетвёртый плохо.\n=======\nЧетвёртый (Б).\n>>>>>>> чужое", text)

    def test_conflict_block_parsed(self):
        from lde.merge import merge_text, parse_conflict
        text, _ = merge_text(self.BASE, self.BASE.replace("Третий.", "Т1."), self.BASE.replace("Третий.", "Т2."))
        bs = parse_text(text)
        c = [b for b in bs if b.kind == "conflict"]
        self.assertEqual(len(c), 1)
        self.assertEqual(parse_conflict(c[0].src)["theirs"], "Т2.")
        self.assertEqual([b.kind for b in bs].count("para"), 3)

    def test_delete_vs_edit_is_conflict(self):
        from lde.merge import merge_text
        _, n = merge_text(self.BASE, self.BASE.replace("Третий.\n\n", ""), self.BASE.replace("Третий.", "Третий!"))
        self.assertEqual(n, 1)

    def test_merge_block(self):
        from lde.merge import merge_block
        r = merge_block("Дальность {R} равна {=c.R}.", "Дальность цели {R} равна {=c.R}.", "Дальность {R} равна {=c.R} м.")
        self.assertEqual(r, {"ok": True, "text": "Дальность цели {R} равна {=c.R} м."})
        self.assertFalse(merge_block("a b c", "a X c", "a Y c")["ok"])


class TestReviewerInput(unittest.TestCase):
    """Ввод рецензента — недоверенный: LaTeX без опасных команд, вставки — только по известным номерам."""

    def test_safe_tex(self):
        from lde.share import safe_tex
        self.assertTrue(safe_tex(r"\frac{1}{2\pi}\int f\,dx + \htmlData{lde=q:@v3}{R}"))
        self.assertTrue(safe_tex(r"\begin{aligned}a&=b\\c&=d\end{aligned}"))
        for bad in (r"\input{/etc/passwd}", r"\def\x{1}", r"\immediate\write18{ls}", r"\begin{filecontents}x\end{filecontents}",
                    r"\csname input\endcsname", r"a % комментарий", r"\makeatletter\@input"):
            self.assertFalse(safe_tex(bad), bad)

    def test_reviewer_chips(self):
        from lde.share import from_reviewer_chip, from_reviewer_tex
        chips = {"v1": "{R}", "v2": "{=afc.f_c}", "v3": "@fig:afc", "v4": "[@ivanov2020]"}
        self.assertEqual(from_reviewer_chip("{@v1}", chips), "{R}")
        self.assertEqual(from_reviewer_chip("{=@v2}", chips), "{=afc.f_c}")
        self.assertEqual(from_reviewer_chip("@@v3", chips), "@fig:afc")
        self.assertEqual(from_reviewer_chip("[@@v4, с. 45]", chips), "[@ivanov2020, с. 45]")
        self.assertEqual(from_reviewer_chip("**жирно**", chips), "**жирно**")
        self.assertIsNone(from_reviewer_chip("{@v99}", chips))             # неизвестный номер
        self.assertIsNone(from_reviewer_chip("{R_secret}", chips))         # не номер — не вставка
        self.assertIsNone(from_reviewer_chip(r"$\input{x}$", chips))
        self.assertEqual(from_reviewer_tex(r"a+\htmlData{lde=q:@v1}{R}", chips), r"a+\q{R}")
        self.assertIsNone(from_reviewer_tex(r"\htmlData{lde=q:@v77}{R}", chips))


class TestFeedback(unittest.TestCase):
    def test_check_finds_tools_and_gaps(self):
        from lde.feedback import check
        r = check("нужно отправить документ руководителю на проверку по ссылке")
        self.assertTrue(any("lde share" in c["how"] for c in r["found"]))
        r = check("переименовать величину во всём документе")
        self.assertTrue(r["gaps"])

    def test_new_and_repeat(self):
        import os
        import tempfile
        from lde import feedback as fb
        with tempfile.TemporaryDirectory() as td:
            os.environ["LDE_FEEDBACK_DIR"] = td
            try:
                a = fb.new("missing", "Нет пакетного объявления величин", asked="«объяви 20 величин»", agent="t")
                b = fb.new("missing", "Нет пакетного объявления величин", asked="снова", agent="t")
                self.assertEqual(a["path"], b["path"])
                self.assertEqual(b["repeat"], 2)
                rows = fb.list_reports()
                self.assertEqual(len(rows), 1)
                self.assertEqual(rows[0]["repeats"], 2)
                fb.set_status(rows[0]["id"], "fixed")
                self.assertEqual(fb.list_reports(), [])
                with self.assertRaises(ValueError):
                    fb.new("other", "Неизвестный вид обращения")
            finally:
                del os.environ["LDE_FEEDBACK_DIR"]
