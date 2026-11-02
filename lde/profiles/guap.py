"""Профиль ГУАП (отчёт/реферат по разделу ВКР): титульный лист по образцу кафедры.

Отличия от базового ГОСТ 7.32: титул с таблицами «преподаватель» и «работу выполнил»,
структурные заголовки — как в методичке (с прописной буквы, не капслоком), перечень
сокращений — в порядке объявления.
"""
from __future__ import annotations

import re

from lde.fmt import latex_escape
from lde.profiles import Profile

FIELDS = [
    ("org", "Вуз (сокращённо)"),
    ("department", "Кафедра"),
    ("teacher_position", "Должность преподавателя"),
    ("teacher", "Преподаватель (И. О. Фамилия)"),
    ("doctype", "Вид работы (РЕФЕРАТ, ОТЧЁТ О ЛАБОРАТОРНОЙ РАБОТЕ…)"),
    ("subject", "Строка перед темой (ПЕРВЫЙ РАЗДЕЛ ВКР НА ТЕМУ)"),
    ("topic", "Тема"),
    ("group", "Номер группы"),
    ("author", "Студент (И. О. Фамилия)"),
    ("city", "Город"),
    ("year", "Год"),
]


class GuapProfile(Profile):
    key = "guap"
    title = "ГУАП: отчёт/реферат (титул кафедры)"
    structural_upper = False
    abbr_sort = False
    bib_number_dot = False    # как в документе кафедры: «1 Теоретические основы…»
    # отчёт по разделу ВКР: введение и заключение — в полной ВКР, не в разделе
    required_structure = ("СОДЕРЖАНИЕ", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
    fields = FIELDS
    headings = {
        "toc": "Содержание",
        "abbreviations": "Список использованных сокращений",
        "symbols": "Список обозначений",
        "bibliography": "Список использованных источников",
    }

    def fields_from_lines(self, lines):
        f = {}
        texts = [l["text"] for l in lines]
        for i, t in enumerate(texts):
            u = t.upper()
            if i == 0 and len(t) < 60:
                f["org"] = t
            elif u.startswith("КАФЕДРА"):
                f["department"] = t
            elif u == "ПРЕПОДАВАТЕЛЬ" and i + 1 < len(texts):
                parts = re.split(r"\s{3,}", texts[i + 1])
                if len(parts) >= 2:
                    f["teacher_position"], f["teacher"] = parts[0], parts[-1]
            elif re.match(r"^(РЕФЕРАТ|ОТЧЕТ|ОТЧЁТ|ПОЯСНИТЕЛЬНАЯ ЗАПИСКА|КУРСОВ)", u) and "ЗАЩИЩЕН" not in u:
                f["doctype"] = t
            elif "НА ТЕМУ" in u:
                m = re.match(r"^(.*?НА ТЕМУ)\s*(.*)$", t, re.I)
                f["subject"], f["topic"] = m.group(1).strip(), m.group(2).strip()
            elif u.startswith("СТУДЕНТ"):
                parts = re.split(r"\s{3,}", t)
                nums = [x for x in parts if re.fullmatch(r"[\dА-Яа-я]{3,6}", x)]
                f["group"] = nums[0] if nums else ""
                f["author"] = parts[-1]
            m = re.match(r"^(.+?)\s+((?:19|20)\d\d)$", t)
            if m and i == len(texts) - 1:
                f["city"], f["year"] = m.group(1), m.group(2)
        return f

    def titlepage_lines(self, p):
        """Титул для DOCX (строками; таблицы подписей — упрощённо, см. open-questions)."""
        t = lambda k, d="": self.t(p, k, d)
        rows = [(t("org", "ГУАП"), "center", False, 0), (t("department"), "center", False, 1),
                ("ОТЧЕТ ЗАЩИЩЕН С ОЦЕНКОЙ", "left", False, 2), ("ПРЕПОДАВАТЕЛЬ", "left", False, 1),
                (f"{t('teacher_position')}\u2003\u2003\u2003{t('teacher')}", "center", False, 0),
                (t("doctype", "РЕФЕРАТ"), "center", True, 3), (t("subject"), "center", False, 1),
                (t("topic"), "center", True, 0), ("РАБОТУ ВЫПОЛНИЛ", "left", False, 3),
                (f"СТУДЕНТ гр. № {t('group')}\u2003\u2003\u2003{t('author')}", "left", False, 1),
                (f"{t('city')} {t('year')}", "center", False, -1)]
        return [{"text": x, "align": a, "bold": b, "space": sp} for x, a, b, sp in rows if x.strip()]

    def titlepage_latex(self, p):
        e = lambda k, d="": latex_escape(self.t(p, k, d))
        small = r"\fontsize{10}{12}\selectfont"
        return rf"""\begin{{titlepage}}\thispagestyle{{empty}}\setlength{{\parindent}}{{0pt}}\singlespacing
{{\centering {e('org', 'ГУАП')}\par\bigskip {e('department')}\par}}
\vspace{{1.5\baselineskip}}
ОТЧЕТ\\ ЗАЩИЩЕН С ОЦЕНКОЙ\par\medskip
ПРЕПОДАВАТЕЛЬ\par\medskip
\noindent\begin{{tabular}}{{@{{}}p{{0.34\linewidth}}p{{0.04\linewidth}}p{{0.22\linewidth}}p{{0.04\linewidth}}p{{0.30\linewidth}}@{{}}}}
\centering {e('teacher_position')} && && \centering {e('teacher')}\tabularnewline\cline{{1-1}}\cline{{3-3}}\cline{{5-5}}
\centering{small} должность, уч. степень, звание && \centering{small} подпись, дата && \centering{small} инициалы, фамилия\tabularnewline
\end{{tabular}}
\vspace{{2\baselineskip}}
{{\centering
\fbox{{\parbox{{0.96\linewidth}}{{\centering\vspace{{0.6\baselineskip}}
\textbf{{\large {e('doctype', 'РЕФЕРАТ')}}}\par\vspace{{1.2\baselineskip}}
{e('subject')}\par\smallskip
\textbf{{{e('topic')}}}\par\vspace{{0.8\baselineskip}}}}}}\par}}
\vspace{{2\baselineskip}}
РАБОТУ ВЫПОЛНИЛ\par\medskip
\noindent\begin{{tabular}}{{@{{}}p{{0.25\linewidth}}p{{0.1\linewidth}}p{{0.03\linewidth}}p{{0.22\linewidth}}p{{0.03\linewidth}}p{{0.29\linewidth}}@{{}}}}
СТУДЕНТ гр. № & \centering {e('group')} &&&& \centering {e('author')}\tabularnewline\cline{{2-2}}\cline{{4-4}}\cline{{6-6}}
&&& \centering{small} подпись, дата && \centering{small} инициалы, фамилия\tabularnewline
\end{{tabular}}
\vfill
{{\centering {e('city')} {e('year')}\par}}
\end{{titlepage}}\onehalfspacing\setcounter{{page}}{{2}}"""
