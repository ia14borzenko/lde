"""Сделать копию DOCX с примечаниями рецензента (как их сохраняет Word): comments.xml + якоря.

    python tests/fixtures/make_review_docx.py in.docx out.docx "фрагмент абзаца=текст примечания" …
"""
import re
import sys
import zipfile

W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def main(src, dst, pairs):
    zin = zipfile.ZipFile(src)
    files = {n: zin.read(n) for n in zin.namelist()}
    doc = files["word/document.xml"].decode("utf-8")
    comments = []
    for k, (frag, text) in enumerate(pairs):
        # найти прогон с фрагментом и обернуть его якорями
        m = re.search(r"(<w:r>(?:(?!</w:r>).)*?<w:t[^>]*>[^<]*" + re.escape(frag) + r"[^<]*</w:t></w:r>)", doc, re.S)
        if not m:
            m = re.search(r"(<w:r[ >](?:(?!</w:r>).)*?" + re.escape(frag[:12]) + r"(?:(?!</w:r>).)*?</w:r>)", doc, re.S)
        if not m:
            raise SystemExit(f"не найден фрагмент: {frag}")
        run = m.group(1)
        wrapped = (f'<w:commentRangeStart w:id="{k}"/>{run}<w:commentRangeEnd w:id="{k}"/>'
                   f'<w:r><w:commentReference w:id="{k}"/></w:r>')
        doc = doc.replace(run, wrapped, 1)
        comments.append(f'<w:comment w:id="{k}" w:author="С. С. Сидоров" w:date="2026-11-01T10:00:00Z" w:initials="СС">'
                        f'<w:p><w:r><w:t xml:space="preserve">{text}</w:t></w:r></w:p></w:comment>')
    files["word/document.xml"] = doc.encode("utf-8")
    files["word/comments.xml"] = (f'<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
                                  f'<w:comments xmlns:w="{W}">' + "".join(comments) + "</w:comments>").encode("utf-8")
    rels = files["word/_rels/document.xml.rels"].decode("utf-8")
    rels = rels.replace("</Relationships>", '<Relationship Id="rIdLdeComments" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/comments" Target="comments.xml"/></Relationships>')
    files["word/_rels/document.xml.rels"] = rels.encode("utf-8")
    ct = files["[Content_Types].xml"].decode("utf-8")
    ct = ct.replace("</Types>", '<Override PartName="/word/comments.xml" ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.comments+xml"/></Types>')
    files["[Content_Types].xml"] = ct.encode("utf-8")
    with zipfile.ZipFile(dst, "w", zipfile.ZIP_DEFLATED) as z:
        for n, b in files.items():
            z.writestr(n, b)


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2], [a.split("=", 1) for a in sys.argv[3:]])
