#!/usr/bin/env python3
"""Extract Flight_Logs.xlsx -> flights.json using only the stdlib."""
import json
import re
import zipfile
from datetime import datetime, timedelta
from xml.etree import ElementTree as ET

NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
XLSX = "Flight_Logs.xlsx"


def col_index(cell_ref):
    letters = re.match(r"[A-Z]+", cell_ref).group()
    n = 0
    for ch in letters:
        n = n * 26 + (ord(ch) - 64)
    return n - 1


def excel_date(serial):
    return (datetime(1899, 12, 30) + timedelta(days=float(serial))).date().isoformat()


z = zipfile.ZipFile(XLSX)

# shared strings
shared = []
root = ET.fromstring(z.read("xl/sharedStrings.xml"))
for si in root.findall(f"{NS}si"):
    text = "".join(t.text or "" for t in si.iter(f"{NS}t"))
    shared.append(text)

# styles: find which style indexes use a date number format
date_numfmts = {14, 15, 16, 17, 22, 27, 30, 36, 50, 57}
styles = ET.fromstring(z.read("xl/styles.xml"))
custom = {}
numfmts = styles.find(f"{NS}numFmts")
if numfmts is not None:
    for nf in numfmts.findall(f"{NS}numFmt"):
        code = nf.get("formatCode", "").lower()
        if any(tok in code for tok in ("d", "m", "y")) and "0" not in code.replace("m", ""):
            custom[int(nf.get("numFmtId"))] = True
        elif any(tok in code for tok in ("yy", "dd")):
            custom[int(nf.get("numFmtId"))] = True
cellxfs = styles.find(f"{NS}cellXfs")
date_styles = set()
for i, xf in enumerate(cellxfs.findall(f"{NS}xf")):
    fmt = int(xf.get("numFmtId", 0))
    if fmt in date_numfmts or custom.get(fmt):
        date_styles.add(i)

sheet = ET.fromstring(z.read("xl/worksheets/sheet1.xml"))
rows = []
for row in sheet.find(f"{NS}sheetData").findall(f"{NS}row"):
    cells = {}
    for c in row.findall(f"{NS}c"):
        idx = col_index(c.get("r"))
        t = c.get("t", "n")
        s = int(c.get("s", 0))
        v = c.find(f"{NS}v")
        if t == "s":
            cells[idx] = shared[int(v.text)] if v is not None and v.text else ""
        elif t == "inlineStr":
            is_el = c.find(f"{NS}is")
            cells[idx] = "".join(x.text or "" for x in is_el.iter(f"{NS}t")) if is_el is not None else ""
        elif v is None or v.text is None:
            cells[idx] = ""
        elif s in date_styles:
            cells[idx] = excel_date(v.text)
        else:
            num = float(v.text)
            cells[idx] = int(num) if num == int(num) else num
    if cells:
        width = max(max(cells) + 1, 22)
        rows.append([cells.get(i, "") for i in range(width)])

headers = [str(h).strip() for h in rows[0]]
data = rows[1:]

with open("flights.json", "w") as f:
    json.dump({"headers": headers, "rows": data}, f, separators=(",", ":"))

print("headers:", headers)
print("rows:", len(data))
print("sample:", data[0])
print("sample:", data[-1])
