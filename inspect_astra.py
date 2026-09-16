#!/usr/bin/env python3
"""Dump the sheets of Contact_directory_review.xlsx to inspect structure."""
import re
import zipfile
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX = "Contact_directory_review.xlsx"


def col_index(cell_ref):
    letters = re.match(r"[A-Z]+", cell_ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


z = zipfile.ZipFile(XLSX)
shared = []
root = ET.fromstring(z.read("xl/sharedStrings.xml"))
for si in root.findall(f"{NS}si"):
    shared.append("".join(t.text or "" for t in si.iter(f"{NS}t")))

wb = z.read("xl/workbook.xml").decode()
print(re.findall(r"name='[^']*'|name=\"[^\"]*\"", wb)[:10])

for sheet in ("sheet1", "sheet2", "sheet3", "sheet4", "sheet5"):
    try:
        data = z.read(f"xl/worksheets/{sheet}.xml")
    except KeyError:
        continue
    root = ET.fromstring(data)
    dim = root.find(f"{NS}dimension")
    rows = root.find(f"{NS}sheetData").findall(f"{NS}row")
    print(f"\n=== {sheet} dim={dim.get('ref') if dim is not None else '?'} rows={len(rows)}")
    for row in rows[:4]:
        cells = {}
        for c in row.findall(f"{NS}c"):
            idx = col_index(c.get("r"))
            t = c.get("t", "n")
            v = c.find(f"{NS}v")
            if t == "s":
                cells[idx] = shared[int(v.text)] if v is not None and v.text else ""
            elif t == "inlineStr":
                is_el = c.find(f"{NS}is")
                cells[idx] = "".join(x.text or "" for x in is_el.iter(f"{NS}t")) if is_el is not None else ""
            else:
                cells[idx] = v.text if v is not None else ""
        width = max(cells) + 1 if cells else 0
        print([str(cells.get(i, ""))[:40] for i in range(width)])
