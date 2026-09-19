#!/usr/bin/env python3
"""Apply explicit visual-review decisions to every source-edition artifact.

Fresh OCR never enters the published data without a recorded image review.
The audit file retains each changed entry's previous reading and source hash.
"""
import hashlib
import json
import re
import unicodedata
import zipfile
from collections import Counter
from pathlib import Path
from xml.etree import ElementTree as ET

from contact_data import ROOT, WORKBOOK, NS, EMAIL_RE, is_street_address, read_sheets, write_json

STATUS = "Visually transcribed — scan-limited"


def read_decisions():
    decisions = {}
    manual = ROOT / "review/verified_transcriptions.txt"
    if manual.exists():
        record = part = None
        for line in manual.read_text().splitlines():
            if not line or line.startswith("#"):
                continue
            if line.startswith("@@ "):
                entry_id, name = line[3:].split(" | ", 1)
                record = {"id": entry_id, "name": name, "parts": []}
                decisions[entry_id] = record
            elif line.startswith("@ "):
                page, column = map(int, line[2:].split())
                part = {"page": page, "column": column, "lines": []}
                record["parts"].append(part)
            else:
                part["lines"].append(line)
    source_hash = hashlib.sha256((ROOT / "Jeffrey_Epstein_UNKNOWN.pdf").read_bytes()).hexdigest()
    for path in sorted((ROOT / "review/pages").glob("*.json")):
        page = json.loads(path.read_text())
        if page["source_sha256"] != source_hash:
            raise ValueError(f"Review is for a different PDF: {path}")
        for entry in page["entries"]:
            current = decisions.setdefault(entry["id"], {"id": entry["id"], "name": None, "parts": []})
            if entry["name"]:
                current["name"] = entry["name"]
            # Some source boxes split one entry twice within the same column.
            # Preserve both fragments in reading order before replacing a page.
            merged_parts = {}
            for part in entry["parts"]:
                key = (part["page"], part["column"])
                merged_parts.setdefault(key, dict(part, lines=[]))["lines"].extend(part["lines"])
            for part in merged_parts.values():
                key = (part["page"], part["column"])
                current["parts"] = [p for p in current["parts"] if (p["page"],p["column"]) != key]
                current["parts"].append(part)
            current["parts"].sort(key=lambda p: (p["page"], p["column"]))
    return decisions, source_hash


def normalize(text):
    return re.sub(r"[^a-z0-9]", "", unicodedata.normalize("NFKD", text).lower())


def categorize(name, lines):
    heading_lines = 1
    for count in range(1, min(4, len(lines))+1):
        if normalize("".join(lines[:count])) == normalize(name):
            heading_lines = count
            break
    phones, emails, other = [], [], []
    for line in lines[heading_lines:]:
        number = re.match(r"^(?:(?:Email|[A-Za-z]{1,4}):\s*)?(?:\([A-Za-z]{1,4}\)\s*)?([+\d(][\d ()./–?\-]*)", line, re.I)
        if number and len(re.sub(r"\D", "", number[1])) >= 7 and not EMAIL_RE.search(line) and not is_street_address(line):
            phones.append(line)
        elif "@" in line or re.match(r"email\b|www\.", line, re.I):
            emails.append(line)
        else:
            other.append(line)
    return phones, emails, other


def column_name(number):
    name = ""
    while number:
        number, remainder = divmod(number-1,26)
        name = chr(65+remainder)+name
    return name


def add_source_sections(dataset, sheets, decisions, source_hash):
    """Restore source headings missed by the original segmentation, keeping IDs."""
    path = ROOT / "review/additions.json"
    if not path.exists():
        return
    additions = json.loads(path.read_text())
    if additions["source_sha256"] != source_hash:
        raise ValueError("Additional source sections refer to a different PDF")
    existing = {r["id"]: r for r in dataset["records"]}
    for section in additions["entries"]:
        entry_id, name = section["id"], section["name"]
        kind = section.get("kind", "Section heading")
        page, column = section["page"], section["column"]
        ref = f"p.{page} c.{column}"
        text = "\n".join(section["lines"])
        decision = {"id":entry_id, "name":name, "notes":section.get("notes", ""), "parts":[{
            "page":page, "column":column, "lines":section["lines"],
        }]}
        decisions[entry_id] = decision
        if entry_id in existing:
            if existing[entry_id]["kind"] != kind or existing[entry_id]["pages"] != [page]:
                raise ValueError(f"New heading ID conflicts with an existing entry: {entry_id}")
            continue
        dataset["records"].append({
            "id":entry_id, "name":name, "status":STATUS, "manual":True,
            "kind":kind, "pages":[page], "locations":ref,
            "text":text, "alternate":"", "notes":"Source heading omitted by original segmentation.",
            "parts":[{"page":page,"column":column,"bbox":section["bbox"],
                      "continuation":False,"visual_scope":"Visually bounded source fragment"}],
        })
        sheets["Directory"].append({
            "Entry ID":entry_id,"Name / heading as read":name,"Source block type":kind,
            "PDF page(s)":str(page),"Page / column reference":ref,"Phone / fax lines":"",
            "Email / web lines":"","Address / other lines":"","Transcription status":STATUS,
            "Review notes":"Source heading omitted by original segmentation.",
        })
        sheets["Raw readings"].append({
            "Entry ID":entry_id,"Name / heading":name,"Source reference":ref,
            "Primary transcription":text,"Alternate PDF text-layer reading":"",
            "Transcription status":STATUS,"Review notes":"Source heading omitted by original segmentation.",
        })
    # Additions appear in source order without renumbering any original entry.
    dataset["records"].sort(key=lambda r:(r["parts"][0]["page"],r["parts"][0]["column"],r["parts"][0]["bbox"][1]))
    order = {r["id"]:i for i,r in enumerate(dataset["records"])}
    for name in ("Directory", "Raw readings"):
        sheets[name].sort(key=lambda r:order[r["Entry ID"]])


def correct_source_bounds(dataset, sheets, decisions, source_hash):
    path = ROOT / "review/geometry.json"
    if not path.exists():
        return
    corrections = json.loads(path.read_text())
    if corrections["source_sha256"] != source_hash:
        raise ValueError("Source-bound corrections refer to a different PDF")
    records = {r["id"]:r for r in dataset["records"]}
    for correction in corrections["entries"]:
        entry_id = correction["id"]
        empty = {(p["page"],p["column"]) for p in correction.get("remove_empty_parts",[])}
        reassigned = {(p["page"],p["column"]):p["to_id"] for p in correction.get("reassign_parts",[])}
        drop = empty | set(reassigned)
        decision = decisions[entry_id]
        for split in correction.get("extract_lines", []):
            part = next(p for p in decision["parts"] if (p["page"],p["column"])==(split["page"],split["column"]))
            target = next(p for p in decisions[split["to_id"]]["parts"] if (p["page"],p["column"])==(split["page"],split["column"]))
            if target["lines"] != split["lines"]:
                raise ValueError(f"Split source lines disagree for {entry_id}")
            for line in split["lines"]:
                part["lines"].remove(line)
        for part in decision["parts"]:
            key = (part["page"],part["column"])
            if key in empty and part["lines"]:
                raise ValueError(f"Cannot discard transcribed lines from {entry_id}")
            if key in reassigned:
                target = next(p for p in decisions[reassigned[key]]["parts"] if (p["page"],p["column"])==key)
                if part["lines"] != target["lines"]:
                    raise ValueError(f"Reassigned source lines disagree for {entry_id}")
        decision["parts"] = [p for p in decision["parts"] if (p["page"],p["column"]) not in drop]
        record = records[entry_id]
        record["parts"] = [p for p in record["parts"] if (p["page"],p["column"]) not in drop]
        for removed in correction.get("remove_boxes", []):
            record["parts"] = [p for p in record["parts"] if not (
                p["page"]==removed["page"] and p["column"]==removed["column"] and p["bbox"]==removed["bbox"])]
        for split in correction.get("extract_lines", []):
            original_part = next(p for p in record["parts"] if (p["page"],p["column"])==(split["page"],split["column"]))
            original_part["bbox"] = split["remaining_bbox"]
        for change in correction.get("bounds", []):
            original_part = next(p for p in record["parts"] if (p["page"],p["column"])==(change["page"],change["column"]))
            original_part["bbox"] = change["bbox"]
        if not record["parts"]:
            raise ValueError(f"Source-bound correction removed an entire record: {entry_id}")
        record["pages"] = sorted({p["page"] for p in record["parts"]})
        record["locations"] = "; ".join(f"p.{p['page']} c.{p['column']}" + (" (cont.)" if p["continuation"] else "") for p in record["parts"])
        row = next(r for r in sheets["Directory"] if r["Entry ID"]==entry_id)
        row["PDF page(s)"] = ", ".join(map(str,record["pages"]))
        row["Page / column reference"] = record["locations"]
        if correction.get("kind"):
            record["kind"] = row["Source block type"] = correction["kind"]
        if correction.get("notes"):
            decision["notes"] = correction["notes"]
        next(r for r in sheets["Raw readings"] if r["Entry ID"]==entry_id)["Source reference"] = record["locations"]


def update_sheet(content, rows):
    root = ET.fromstring(content)
    data = root.find(NS + "sheetData")
    header_row = data.find(NS + "row")
    header_styles = {re.sub(r"\d", "", c.get("r")): c.get("s") for c in header_row}
    # The caller supplies column names in the original order, including headers.
    template = list(data)[1] if len(data)>1 else header_row
    styles = {re.sub(r"\d", "", c.get("r")): c.get("s") for c in template}
    data.clear()
    for number, values in enumerate(rows,1):
        row = ET.SubElement(data, NS+"row", {"r":str(number)})
        for index, value in enumerate(values,1):
            letter = column_name(index)
            attrs = {"r":f"{letter}{number}", "t":"inlineStr"}
            if number == 1 and header_styles.get(letter):
                attrs["s"] = header_styles[letter]
            if number>1 and styles.get(letter):
                attrs["s"] = styles[letter]
            cell = ET.SubElement(row, NS+"c", attrs)
            inline = ET.SubElement(cell, NS+"is")
            ET.SubElement(inline, NS+"t", {"{http://www.w3.org/XML/1998/namespace}space":"preserve"}).text = str(value)
    ref = f"A1:{column_name(len(rows[0]))}{len(rows)}"
    for name in ("dimension", "autoFilter"):
        element = root.find(NS+name)
        if element is not None:
            element.set("ref",ref)
    return ET.tostring(root,encoding="utf-8",xml_declaration=True), ref


def main():
    decisions, source_hash = read_decisions()
    html_path = ROOT / "Contact_directory_review.html"
    html = html_path.read_text()
    match = re.search(r'(<script id="dataset" type="application/json">)(.*?)(</script>)', html, re.S)
    dataset = json.loads(match[2])
    sheets = read_sheets(WORKBOOK)
    add_source_sections(dataset, sheets, decisions, source_hash)
    correct_source_bounds(dataset, sheets, decisions, source_hash)
    source = {r["id"]:r for r in dataset["records"]}
    directory = {r["Entry ID"]:r for r in sheets["Directory"]}
    readings = {r["Entry ID"]:r for r in sheets["Raw readings"]}
    audit_path = ROOT / "review/change_log.json"
    audit = json.loads(audit_path.read_text()) if audit_path.exists() else {"source_sha256":source_hash,"entries":{}}
    completed = {}
    for entry_id, decision in decisions.items():
        if entry_id not in source:
            raise ValueError(f"Unknown reviewed entry: {entry_id}")
        original = source[entry_id]
        expected = {(p["page"],p["column"]) for p in original["parts"]}
        actual = {(p["page"],p["column"]) for p in decision["parts"]}
        if not decision["name"] or actual != expected:
            continue  # A continuation is not reviewed until all its parts are.
        if any(not p["lines"] for p in decision["parts"]):
            raise ValueError(f"Empty reviewed source fragment: {entry_id}")
        audit["entries"].setdefault(entry_id, {"previous_name":original["name"],"previous_text":original["text"],"previous_status":original["status"],"previous_notes":original["notes"],"previous_source_parts":original["parts"]})
        lines = [line for part in decision["parts"] for line in part["lines"]]
        text = "\n".join(lines)
        note = "Line-by-line visual review against the supplied PDF scan on 2026-09-19. Source spellings and historical dialing formats retained. Brackets identify unresolved source uncertainty; contact details are not independently validated."
        if decision.get("notes"):
            note += " " + decision["notes"]
        original.update(name=decision["name"], text=text, status=STATUS, manual=True, notes=note)
        phones, emails, other = categorize(decision["name"],lines)
        if original["kind"] == "Unassigned source fragment":
            other = lines
        row = directory[entry_id]
        row.update({"Name / heading as read":decision["name"],"Phone / fax lines":"\n".join(phones),"Email / web lines":"\n".join(emails),"Address / other lines":"\n".join(other),"Transcription status":STATUS,"Review notes":note})
        readings[entry_id].update({"Name / heading":decision["name"],"Primary transcription":text,"Transcription status":STATUS,"Review notes":note})
        completed[entry_id] = decision
        audit["entries"][entry_id].update(name=decision["name"],text=text,reviewed_on="2026-09-19",pages=sorted({p["page"] for p in decision["parts"]}))
    old_lines = {}
    for row in sheets["Contact lines"]:
        old_lines.setdefault(row["Entry ID"],[]).append(row)
    new_lines = []
    for row in sheets["Directory"]:
        entry_id = row["Entry ID"]
        if entry_id not in completed:
            new_lines.extend(old_lines.get(entry_id,[]))
            continue
        decision = completed[entry_id]
        order = 0
        for part in decision["parts"]:
            for line in part["lines"]:
                order += 1
                new_lines.append({"Entry ID":entry_id,"Parent name / heading":decision["name"],"PDF page":str(part["page"]),"Column":str(part["column"]),"Line order":str(order),"Complete source line / transcription":line,"Transcription status":STATUS})
    sheets["Contact lines"] = new_lines
    counts = Counter(r["status"] for r in dataset["records"])
    manual_count = sum(r["manual"] for r in dataset["records"])
    for row in sheets["Page index"]:
        p = int(row["PDF page"])
        records = [r for r in dataset["records"] if r["pages"][0]==p]
        row["Records starting"] = str(len(records))
        row["Source fragments"] = str(sum(part["page"]==p for r in dataset["records"] for part in r["parts"]))
        row["Visually transcribed records"] = str(sum(r["manual"] for r in records))
        row["Transcription method"] = "Visual transcription; scan-limited" if records and all(r["manual"] for r in records) else "Mixed / machine text; review required"
    # Preserve the workbook package and its existing formatting/table metadata.
    with zipfile.ZipFile(WORKBOOK) as z:
        payloads={name:z.read(name) for name in z.namelist()}
    overview=ET.fromstring(payloads["xl/worksheets/sheet1.xml"])
    overview_updates={
        "A5":str(len(source)),
        "C5":str(manual_count),"E5":str(len(source)-manual_count),
        "A8":f"ACCURACY LIMIT: {manual_count:,} source records have visual transcriptions and {len(source)-manual_count:,} remain machine readings. Visual review is scan-limited; brackets mark unresolved readings. Compare the original image. Review counts do not establish character-level accuracy or validate historical contact details.",
        "C29":"The repository includes contacts.csv / contacts.json, a source review queue, a coverage report and review/change_log.json with previous and corrected readings.",
    }
    for cell in overview.iter(NS+"c"):
        if cell.get("r") in overview_updates:
            value=overview_updates[cell.get("r")]
            for child in list(cell):cell.remove(child)
            cell.set("t","inlineStr")
            inline=ET.SubElement(cell,NS+"is")
            ET.SubElement(inline,NS+"t").text=value
    payloads["xl/worksheets/sheet1.xml"]=ET.tostring(overview,encoding="utf-8",xml_declaration=True)
    rel={r.get("Id"):r.get("Target").lstrip("/") for r in ET.fromstring(payloads["xl/_rels/workbook.xml.rels"])}
    changed_refs={}
    for sheet in ET.fromstring(payloads["xl/workbook.xml"]).iter(NS+"sheet"):
        name=sheet.get("name")
        if name not in ("Directory","Contact lines","Raw readings","Page index"):
            continue
        target=rel[sheet.get("{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id")]
        target=target if target.startswith("xl/") else "xl/"+target
        header=list(sheets[name][0])
        rows=[header]+[[row.get(k,"") for k in header] for row in sheets[name]]
        payloads[target],new_ref=update_sheet(payloads[target],rows)
        changed_refs[name]=new_ref
    for path,content in list(payloads.items()):
        if path.startswith("xl/tables/") and path.endswith(".xml"):
            root=ET.fromstring(content)
            table_name=root.get("name","").lower().replace("_","")
            for name,ref in changed_refs.items():
                if name.lower().replace(" ","") in table_name:
                    root.set("ref",ref)
                    af=root.find(NS+"autoFilter")
                    if af is not None:af.set("ref",ref)
                    payloads[path]=ET.tostring(root,encoding="utf-8",xml_declaration=True)
    tmp=WORKBOOK.with_suffix(".tmp.xlsx")
    with zipfile.ZipFile(tmp,"w",compression=zipfile.ZIP_DEFLATED) as z:
        for name,content in payloads.items():z.writestr(name,content)
    # Validate the written XML package before replacing any source edition.
    check=read_sheets(tmp)
    if len(check["Directory"])!=len(directory):raise ValueError("Workbook record loss")
    tmp.replace(WORKBOOK)
    payload=json.dumps(dataset,ensure_ascii=False,separators=(",",":")).replace("</","<\\/")
    html=html[:match.start(2)]+payload+html[match.end(2):]
    notice=(f'<div class="notice"><strong>Accuracy notice:</strong> {manual_count:,} entries have visual transcriptions; '
            f'{len(source)-manual_count:,} remain unverified machine transcriptions. '
            'Visual review is limited by the scan. Brackets mark unresolved readings; compare the source image. Redactions are not reconstructed.</div>')
    html=re.sub(r'<div class="notice">.*?</div>',lambda m:notice,html,count=1,flags=re.S)
    html_tmp = html_path.with_suffix(".tmp.html")
    html_tmp.write_text(html)
    html_tmp.replace(html_path)
    text_header=("SCANNED CONTACT DIRECTORY — REVIEW EDITION\nSource: Jeffrey_Epstein_UNKNOWN.pdf\n"
                 f"Coverage: all 95 PDF pages; {len(source):,} source records / headings.\n"
                 f"Visually transcribed: {manual_count:,}; machine transcription: {len(source)-manual_count:,}.\n"
                 "The scan is authoritative. Brackets mark uncertainty, not original wording.\n"
                 "Historical contact details and source allegations are not independently validated.\n"
                 "No redacted content or missing email endings have been reconstructed.\n\n")
    text_header+="\n\n".join(f"{r['id']} | {r['name']}\nSource: {r['locations']}\nStatus: {r['status']}\n{'-'*70}\n{r['text']}\nReview notes: {r['notes']}" for r in dataset["records"])
    text_path = ROOT/"Contact_directory_review.txt"
    text_tmp = text_path.with_suffix(".tmp.txt")
    text_tmp.write_text(text_header+"\n")
    text_tmp.replace(text_path)
    audit["reviewed_entries"]=len(completed)
    audit["visually_transcribed_entries"]=manual_count
    audit["machine_entries_remaining"]=len(source)-manual_count
    write_json(audit_path,audit)
    print(f"Applied {len(completed)} complete source reviews; {len(source)-manual_count} machine entries remain.")


if __name__=="__main__":
    main()
