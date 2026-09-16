#!/usr/bin/env python3
"""Extract the 'Directory' sheet of Contact_directory_review.xlsx -> astra_contacts.json"""
import json
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

sheet = ET.fromstring(z.read("xl/worksheets/sheet2.xml"))
rows = []
for row in sheet.find(f"{NS}sheetData").findall(f"{NS}row"):
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
    if cells:
        width = max(cells) + 1
        rows.append([cells.get(i, "") for i in range(width)])

header = rows[0]
print("header:", header)
out = []
for r in rows[1:]:
    r = r + [""] * (len(header) - len(r))
    out.append({
        "id": r[0],
        "name": r[1].strip(),
        "block_type": r[2],
        "pages": r[3],
        "ref": r[4],
        "phones": [l for l in r[5].split("\n") if l.strip()],
        "emails": [l for l in r[6].split("\n") if l.strip()],
        "address": r[7].replace("\n", ", "),
        "status": r[8],
        "notes": r[9],
    })

with open("astra_contacts.json", "w") as f:
    json.dump(out, f, ensure_ascii=False, separators=(",", ":"))
print("entries:", len(out))
print("sample:", json.dumps(out[2], ensure_ascii=False)[:300])
