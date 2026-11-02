"""Профили оформления: базовый ГОСТ 7.32-2017 и надстройки (методички вузов/заказчиков).

Профиль задаёт титульный лист, названия структурных элементов и допустимые варианты
оформления. Реквизиты титула — в lde.toml [title].
"""
from __future__ import annotations

from lde.fmt import latex_escape

FIELDS_GOST = [
    ("ministry", "Министерство / ведомство"),
    ("org", "Организация-исполнитель"),
    ("orgshort", "Сокращённое название организации"),
    ("udc", "УДК"),
    ("regnum", "Регистрационный номер"),
    ("approver", "УТВЕРЖДАЮ: должность, И. О. Фамилия"),
    ("doctype", "Вид документа"),
    ("subject", "Наименование работы (по теме)"),
    ("topic", "Тема (наименование отчёта)"),
    ("worktype", "Вид отчёта (промежуточный, заключительный)"),
    ("supervisor", "Руководитель: должность, И. О. Фамилия"),
    ("author", "Исполнитель"),
    ("city", "Город"),
    ("year", "Год"),
]


class Profile:
    key = "gost-7.32"
    title = "ГОСТ 7.32-2017 (отчёт о НИР)"
    structural_upper = True
    abbr_sort = True
    bib_number_dot = True     # ГОСТ 7.32-2017 п. 6.16: «нумеровать арабскими цифрами с точкой»
    required_structure = ("СОДЕРЖАНИЕ", "ВВЕДЕНИЕ", "ЗАКЛЮЧЕНИЕ", "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ")
    fields = FIELDS_GOST
    headings = {
        "toc": "СОДЕРЖАНИЕ",
        "abbreviations": "ПЕРЕЧЕНЬ СОКРАЩЕНИЙ И ОБОЗНАЧЕНИЙ",
        "symbols": "ПЕРЕЧЕНЬ СОКРАЩЕНИЙ И ОБОЗНАЧЕНИЙ",
        "bibliography": "СПИСОК ИСПОЛЬЗОВАННЫХ ИСТОЧНИКОВ",
    }

    def heading(self, k: str) -> str:
        return self.headings[k]

    def preamble_extra(self, p) -> str:
        return ""

    def t(self, p, k, default=""):
        return str(p.config.get("title", {}).get(k, default) or "")

    def fields_from_lines(self, lines: list[dict]) -> dict:
        """Реквизиты из строк титула импортированного документа: без разбора — строками как есть."""
        return {"lines": [{"text": l["text"], "align": l.get("align", "center")} for l in lines]}

    def titlepage_lines(self, p) -> list[dict]:
        """Титул как список строк: {text, align, bold, space_before (строк)} — общий для PDF и DOCX."""
        custom = p.config.get("title", {}).get("lines")
        if custom:
            out = []
            for i, l in enumerate(custom):
                out.append({"text": str(l.get("text", "")), "align": l.get("align", "center"),
                            "bold": bool(l.get("bold", False)), "space": int(l.get("space", 1 if i else 0))})
            if out:
                out[-1]["space"] = -1
            return out
        t = lambda k, d="": self.t(p, k, d)
        L = []

        def add(text, align="center", bold=False, space=0, upper=False):
            if text:
                L.append({"text": text.upper() if upper else text, "align": align, "bold": bold, "space": space})

        add(t("ministry"))
        add(t("org", "Организация-исполнитель"), bold=True, upper=True, space=1 if t("ministry") else 0)
        add(f"({t('orgshort').upper()})" if t("orgshort") else "")
        add(f"УДК {t('udc')}" if t("udc") else "", align="left", space=2)
        add(f"Рег. № {t('regnum')}" if t("regnum") else "", align="left")
        if t("approver"):
            add("УТВЕРЖДАЮ", align="right", space=1)
            for part in t("approver").split(";"):
                add(part.strip(), align="right")
        add(t("doctype", "ОТЧЁТ О НАУЧНО-ИССЛЕДОВАТЕЛЬСКОЙ РАБОТЕ"), bold=True, upper=True, space=3)
        add(t("subject"), space=1)
        add(t("topic", p.title), bold=True, upper=True, space=1)
        add(f"({t('worktype')})" if t("worktype") else "", space=1)
        if t("supervisor"):
            add("Руководитель НИР:", align="right", space=4)
            add(t("supervisor"), align="right")
        if t("author"):
            add("Исполнитель:", align="right", space=1)
            add(t("author"), align="right")
        add(f"{t('city', 'Москва')} {t('year', '2026')}", space=-1)
        return L

    def titlepage_latex(self, p) -> str:
        if not p.doc_cfg.get("title_page", True):
            return ""
        out = ["\\begin{titlepage}\\thispagestyle{empty}\\setlength{\\parindent}{0pt}"]
        for ln in self.titlepage_lines(p):
            if ln["space"] == -1:
                out.append("\\vfill")
            elif ln["space"]:
                out.append(f"\\vspace{{{ln['space']}\\baselineskip}}")
            txt = latex_escape(ln["text"])
            if ln["bold"]:
                txt = f"\\textbf{{{txt}}}"
            env = {"center": "\\centering", "left": "\\raggedright", "right": "\\raggedleft"}[ln["align"]]
            out.append(f"{{{env} {txt}\\par}}")
        out.append("\\end{titlepage}\\setcounter{page}{2}")
        return "\n".join(out)


PROFILES: dict[str, type[Profile]] = {"gost-7.32": Profile}


def get_profile(key: str) -> Profile:
    if key not in PROFILES:
        # ленивые профили
        if key == "guap":
            from lde.profiles.guap import GuapProfile
            PROFILES["guap"] = GuapProfile
    return PROFILES.get(key, Profile)()


def detect_profile(lines: list[dict]) -> str:
    """Профиль по титулу импортированного документа."""
    head = " ".join(l["text"] for l in lines[:3]).upper()
    if "ГУАП" in head or "АЭРОКОСМИЧЕСКОГО ПРИБОРОСТРОЕНИЯ" in head:
        return "guap"
    return "gost-7.32"


def list_profiles() -> list[tuple[str, str]]:
    get_profile("guap")
    return [(k, v.title) for k, v in PROFILES.items()]
