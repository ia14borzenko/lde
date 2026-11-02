#!/usr/bin/env python3
"""embeds.py — операции над OOXML-вложениями DOCX, которые нельзя сделать
через python-docx. Главное: замена SVG-картинок на PNG (дефект D7).

Зачем именно замена, а не «PNG fallback + asvg:svgBlip»:
MS-ODRAWXML 1.3.3 формально требует PNG как основной blip и SVG в
<asvg:svgBlip>. Практика (проверено на LibreOffice 26.2.6.3, 2026-10-31):
при наличии asvg:svgBlip LibreOffice пытается использовать SVG и рендерит
ПУСТОЕ место (даже с корректным PNG-fallback), т.е. документ ломается в
просмотрщике и в нашем QA-рендере. Поэтому по умолчанию SVG заменяется
растровым PNG (600 dpi, 8-бит) без svgBlip — рендерится и в Word, и в
LibreOffice; SVG-файлы остаются отдельными артефактами (out/math-svg/).

Режим `vector=True` всё же добавляет asvg:svgBlip (для Word 2016+), но
LibreOffice такой документ отрисует пустым — использовать только по
осознанному требованию заказчика.

Использование:
    from embeds import add_svg_png_fallbacks
    report = add_svg_png_fallbacks(Path("out/main.docx"))            # замена
    report = add_svg_png_fallbacks(Path("out/main.docx"), vector=True)  # blip+SVG
"""
from __future__ import annotations

import re
import subprocess
import tempfile
import zipfile
from pathlib import Path

NS_ASVG = "http://schemas.microsoft.com/office/drawing/2016/SVG/main"
DRAWING_REL = "http://schemas.openxmlformats.org/officeDocument/2006/relationships/image"
ASVG_EXT_URI = "{96DAC541-7B7A-43D3-8B79-37D633B846F1}"


def _svg_to_png(svg_bytes: bytes, density: int = 600) -> bytes | None:
    """SVG -> PNG через ImageMagick, 8-бит, белый фон."""
    for exe in ("magick", "convert"):
        try:
            with tempfile.TemporaryDirectory() as td:
                src = Path(td) / "in.svg"
                dst = Path(td) / "out.png"
                src.write_bytes(svg_bytes)
                cmd = [exe, "-density", str(density), "-background", "white",
                       str(src), "-flatten", "-depth", "8", str(dst)]
                subprocess.run(cmd, check=True, capture_output=True, timeout=90)
                if dst.exists() and dst.stat().st_size > 0:
                    return dst.read_bytes()
        except Exception:
            continue
    return None


def _blip_re() -> re.Pattern:
    return re.compile(r"<a:blip\b([^>]*?)(?:/>|>(.*?)</a:blip>)", re.S)


def add_svg_png_fallbacks(docx_path: Path, vector: bool = False) -> dict:
    """Заменить SVG в DOCX на PNG (или добавить PNG-fallback при vector=True).

    Идемпотентно: повторный вызов на документе без SVG ничего не меняет.
    """
    report = {"svg_parts": 0, "fallbacks_added": 0, "patched_blips": 0,
              "mode": "vector" if vector else "replace", "errors": []}
    try:
        with zipfile.ZipFile(docx_path) as z:
            names = z.namelist()
            svg_names = [n for n in names
                         if n.startswith("word/media/") and n.lower().endswith(".svg")]
            if not svg_names:
                return report
            report["svg_parts"] = len(svg_names)
            data = {n: z.read(n) for n in names}

        rels_name = "word/_rels/document.xml.rels"
        rels_xml = data[rels_name].decode("utf-8")
        target_to_rid: dict[str, str] = {}
        for rel in re.finditer(r"<Relationship\b[^>]*/>", rels_xml):
            tag = rel.group(0)
            tid = re.search(r'Id="([^"]+)"', tag)
            tgt = re.search(r'Target="([^"]+)"', tag)
            if tid and tgt:
                target_to_rid[tgt.group(1).lstrip("/")] = tid.group(1)

        existing_rids = set(re.findall(r'Id="(rId\d+)"', rels_xml))
        next_n = 1

        def new_rid() -> str:
            nonlocal next_n
            while f"rId{next_n}" in existing_rids:
                next_n += 1
            rid = f"rId{next_n}"
            existing_rids.add(rid)
            next_n += 1
            return rid

        svg_info: dict[str, dict] = {}
        for i, svg_name in enumerate(svg_names, 1):
            short = svg_name[len("word/"):]
            rid_svg = target_to_rid.get(short)
            if not rid_svg:
                report["errors"].append(f"нет relationship для {svg_name}")
                continue
            png_bytes = _svg_to_png(data[svg_name])
            if not png_bytes:
                report["errors"].append(f"не удалось конвертировать {svg_name} в PNG")
                continue
            png_part = f"word/media/lde-fallback-{i}.png"
            png_target = f"media/lde-fallback-{i}.png"
            data[png_part] = png_bytes
            rid_png = new_rid()
            rels_xml = rels_xml.replace(
                "</Relationships>",
                f'<Relationship Id="{rid_png}" Type="{DRAWING_REL}" '
                f'Target="{png_target}"/></Relationships>')
            svg_info[short] = {"rid_svg": rid_svg, "rid_png": rid_png,
                               "svg_name": svg_name}
            report["fallbacks_added"] += 1
        data[rels_name] = rels_xml.encode("utf-8")

        ct = data.get("[Content_Types].xml", b"").decode("utf-8")
        if 'Extension="png"' not in ct:
            ct = ct.replace("</Types>",
                            '<Default Extension="png" ContentType="image/png"/></Types>')
            data["[Content_Types].xml"] = ct.encode("utf-8")

        doc_xml = data["word/document.xml"].decode("utf-8")
        patched = 0
        for short, info in svg_info.items():
            rid_svg, rid_png = info["rid_svg"], info["rid_png"]
            blip_re = _blip_re()

            def repl(m: re.Match) -> str:
                attrs = m.group(1) or ""
                inner = m.group(2) or ""
                if f'r:embed="{rid_svg}"' not in attrs and \
                   f'r:embed="{rid_svg}"' not in inner:
                    return m.group(0)  # чужой blip
                if not vector:
                    # PNG — единственный blip; svgBlip убираем (совместимость с LO)
                    return f'<a:blip r:embed="{rid_png}"/>'
                new_attrs = re.sub(r'\s*r:embed="[^"]*"', "", attrs)
                ext = inner if "asvg:svgBlip" in inner else (
                    f'<a:extLst><a:ext uri="{ASVG_EXT_URI}">'
                    f'<asvg:svgBlip xmlns:asvg="{NS_ASVG}" r:embed="{rid_svg}"/>'
                    "</a:ext></a:extLst>")
                return f'<a:blip r:embed="{rid_png}"{new_attrs}>{ext}</a:blip>'

            new_xml, n = blip_re.subn(repl, doc_xml)
            if n:
                doc_xml = new_xml
                patched += 1
            else:
                report["errors"].append(f"blip не найден: {short} ({rid_svg})")
        data["word/document.xml"] = doc_xml.encode("utf-8")
        report["patched_blips"] = patched

        # при замене удаляем svg-части и их relationships (не оставляем «висячих» SVG)
        if not vector and svg_info:
            new_rels = data[rels_name].decode("utf-8")
            for short, info in svg_info.items():
                rid_svg = info["rid_svg"]
                new_rels = re.sub(
                    r'<Relationship[^>]*Id="' + re.escape(rid_svg) + r'"[^>]*/>', "",
                    new_rels)
                data.pop(info["svg_name"], None)
            data[rels_name] = new_rels.encode("utf-8")

        tmp = docx_path.with_suffix(".embeds.tmp.docx")
        with zipfile.ZipFile(tmp, "w", zipfile.ZIP_DEFLATED) as zout:
            for name in sorted(data):
                zi = zipfile.ZipInfo(name, date_time=(2026, 1, 1, 0, 0, 0))
                zi.compress_type = zipfile.ZIP_DEFLATED
                zout.writestr(zi, data[name])
        tmp.replace(docx_path)
    except Exception as exc:  # noqa: BLE001
        report["errors"].append(f"{type(exc).__name__}: {exc}")
    return report


if __name__ == "__main__":
    import argparse
    import json

    ap = argparse.ArgumentParser()
    ap.add_argument("docx")
    ap.add_argument("--vector", action="store_true",
                    help="PNG + asvg:svgBlip (LibreOffice отрисует пусто)")
    a = ap.parse_args()
    print(json.dumps(add_svg_png_fallbacks(Path(a.docx), vector=a.vector),
                     ensure_ascii=False, indent=2))
