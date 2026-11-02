"""Карта положений элементов на страницах PDF (из сборки, map.json): щелчок → элемент, элемент → области.

Метка — {uid, page, y (pt от низа страницы), kind, float?, part?}. Используется Studio (документ и
правая колонка режима разбора правок); та же логика на JS — на странице рецензента (review.js).
"""
from __future__ import annotations

MARGIN = 56          # поля страницы (pt): граница области, если ниже нет меток


def _top(m: dict) -> float:
    return m["y"] - 4 if m.get("kind") == "formula" else m["y"] + 17


def _floats(marks: list[dict]) -> dict:
    out = {}
    for m in marks:
        if m.get("float"):
            out.setdefault(m["uid"], {})[m.get("part") or "start"] = m
    return out


def locate(marks: list[dict], pages: list[dict], page: int, x: float, y: float) -> dict | None:
    """Щелчок (страница, x, y от верхнего левого угла, pt) → {uid, rects}."""
    if not pages or page > len(pages):
        return None
    ph = pages[page - 1]["h"]
    yb = ph - y
    for uid, d in _floats(marks).items():
        s, e = d.get("start"), d.get("end")
        if s and e and s["page"] == page and e["page"] == page and e["y"] - 6 <= yb <= s["y"] + 6:
            return {"uid": uid, "rects": [{"page": page, "top": ph - s["y"], "bottom": ph - e["y"] + 4}]}
    flow = [m for m in marks if not m.get("float")]
    best = None
    for m in flow:
        if (m["page"], -_top(m)) <= (page, -yb):
            if best is None or (m["page"], -_top(m)) >= (best["page"], -_top(best)):
                best = m
    if not best:
        return None
    return {"uid": best["uid"], "rects": element_rects(marks, pages, best["uid"]) or []}


def element_rects(marks: list[dict], pages: list[dict], uid: str) -> list[dict]:
    """Области элемента целиком: от его метки до следующей (с переходом на следующую страницу)."""
    ms = [m for m in marks if m["uid"] == uid]
    if not ms:
        return []
    h = lambda n: pages[n - 1]["h"] if 0 < n <= len(pages) else 842
    fl = _floats(marks).get(uid)
    if fl and fl.get("start") and fl.get("end") and fl["start"]["page"] == fl["end"]["page"]:
        s, e = fl["start"], fl["end"]
        return [{"page": s["page"], "top": h(s["page"]) - s["y"], "bottom": h(s["page"]) - e["y"] + 4}]
    m = ms[0]
    flow = sorted([x for x in marks if not x.get("float")], key=lambda x: (x["page"], -_top(x)))
    i = next((k for k, x in enumerate(flow) if x is m), None)
    nxt = flow[i + 1] if i is not None and i + 1 < len(flow) else None
    ph = h(m["page"])
    if nxt and nxt["page"] == m["page"]:
        return [{"page": m["page"], "top": ph - _top(m), "bottom": ph - _top(nxt)}]
    rects = [{"page": m["page"], "top": ph - _top(m), "bottom": ph - MARGIN}]
    if nxt and nxt["page"] == m["page"] + 1:
        nh = h(nxt["page"])
        if nh - _top(nxt) > MARGIN + 20:
            rects.append({"page": nxt["page"], "top": MARGIN, "bottom": nh - _top(nxt)})
    return rects
