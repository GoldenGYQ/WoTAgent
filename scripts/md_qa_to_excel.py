from __future__ import annotations

import argparse
import re
import zipfile
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class QAItem:
    question: str
    answer: str


_H_RE = re.compile(r"^###\s+(\d+)\.\s*(.+?)\s*$")


def _xml_escape(text: str) -> str:
    return (
        text.replace("&", "&amp;")
        .replace("<", "&lt;")
        .replace(">", "&gt;")
        .replace('"', "&quot;")
        .replace("'", "&apos;")
    )


def parse_qa_markdown(md_text: str) -> list[QAItem]:
    items: list[QAItem] = []
    current_q: str | None = None
    current_a: list[str] = []

    for raw_line in md_text.splitlines():
        line = raw_line.rstrip("\n")
        m = _H_RE.match(line.strip())
        if m:
            if current_q is not None:
                items.append(QAItem(question=current_q, answer="\n".join(current_a).strip()))
            current_q = m.group(2).strip()
            current_a = []
            continue
        if current_q is not None:
            current_a.append(line)

    if current_q is not None:
        items.append(QAItem(question=current_q, answer="\n".join(current_a).strip()))

    return items


def _cell_inline_str(cell_ref: str, value: str) -> str:
    v = _xml_escape(value)
    return f'<c r="{cell_ref}" t="inlineStr"><is><t xml:space="preserve">{v}</t></is></c>'


def _sheet_xml(items: list[QAItem]) -> str:
    rows: list[str] = []

    header_cells = [
        _cell_inline_str("A1", "序号"),
        _cell_inline_str("B1", "问题"),
        _cell_inline_str("C1", "答案"),
    ]
    rows.append(f'<row r="1">{"".join(header_cells)}</row>')

    for i, item in enumerate(items, start=1):
        r = i + 1
        cells = [
            _cell_inline_str(f"A{r}", str(i)),
            _cell_inline_str(f"B{r}", item.question),
            _cell_inline_str(f"C{r}", item.answer),
        ]
        rows.append(f'<row r="{r}">{"".join(cells)}</row>')

    rows_xml = "".join(rows)
    return (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<sheetData>"
        f"{rows_xml}"
        "</sheetData>"
        "</worksheet>"
    )


def write_xlsx(items: list[QAItem], output_path: Path, *, sheet_name: str = "Q&A") -> None:
    output_path.parent.mkdir(parents=True, exist_ok=True)

    content_types = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
        '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
        '<Default Extension="xml" ContentType="application/xml"/>'
        '<Override PartName="/xl/workbook.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet.main+xml"/>'
        '<Override PartName="/xl/worksheets/sheet1.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.worksheet+xml"/>'
        '<Override PartName="/xl/styles.xml" ContentType="application/vnd.openxmlformats-officedocument.spreadsheetml.styles+xml"/>'
        "</Types>"
    )

    rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="xl/workbook.xml"/>'
        "</Relationships>"
    )

    workbook = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" '
        'xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships">'
        "<sheets>"
        f'<sheet name="{_xml_escape(sheet_name)}" sheetId="1" r:id="rId1"/>'
        "</sheets>"
        "</workbook>"
    )

    workbook_rels = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
        '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/worksheet" Target="worksheets/sheet1.xml"/>'
        '<Relationship Id="rId2" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/styles" Target="styles.xml"/>'
        "</Relationships>"
    )

    styles = (
        '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>'
        '<styleSheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main">'
        "<fonts count=\"1\"><font><sz val=\"11\"/><color theme=\"1\"/><name val=\"Calibri\"/><family val=\"2\"/></font></fonts>"
        "<fills count=\"1\"><fill><patternFill patternType=\"none\"/></fill></fills>"
        "<borders count=\"1\"><border><left/><right/><top/><bottom/><diagonal/></border></borders>"
        "<cellStyleXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\"/></cellStyleXfs>"
        "<cellXfs count=\"1\"><xf numFmtId=\"0\" fontId=\"0\" fillId=\"0\" borderId=\"0\" xfId=\"0\"/></cellXfs>"
        "<cellStyles count=\"1\"><cellStyle name=\"Normal\" xfId=\"0\" builtinId=\"0\"/></cellStyles>"
        "</styleSheet>"
    )

    sheet = _sheet_xml(items)

    with zipfile.ZipFile(output_path, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        zf.writestr("[Content_Types].xml", content_types)
        zf.writestr("_rels/.rels", rels)
        zf.writestr("xl/workbook.xml", workbook)
        zf.writestr("xl/_rels/workbook.xml.rels", workbook_rels)
        zf.writestr("xl/styles.xml", styles)
        zf.writestr("xl/worksheets/sheet1.xml", sheet)


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True, help="Input Q&A markdown path")
    parser.add_argument("--output", required=True, help="Output .xlsx path")
    parser.add_argument("--sheet-name", default="Q&A", help="Excel sheet name")
    args = parser.parse_args()

    input_path = Path(args.input)
    output_path = Path(args.output)

    md_text = input_path.read_text(encoding="utf-8-sig")
    items = parse_qa_markdown(md_text)
    if not items:
        raise SystemExit("No Q&A items found. Expected headings like: ### 1. 问题")

    write_xlsx(items, output_path, sheet_name=str(args.sheet_name))


if __name__ == "__main__":
    main()
