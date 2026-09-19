"""Lossless import of the source-linked directory transcription (stdlib only).

The review workbook contains entry boundaries, visual corrections and line-level
page/column provenance discarded by the old TSV parser. The PDF scan remains
authoritative. Field parsing must never repair a letter or digit by guessing.
"""
import json
import posixpath
import re
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
from xml.etree import ElementTree as ET

ROOT = Path(__file__).resolve().parent
WORKBOOK = ROOT / "Contact_directory_review.xlsx"
NS = "{http://schemas.openxmlformats.org/spreadsheetml/2006/main}"
REL = "{http://schemas.openxmlformats.org/officeDocument/2006/relationships}"


def read_sheets(path):
    """Resolve sheets by name and columns by header, not workbook positions."""
    with zipfile.ZipFile(path) as z:
        shared = []
        if "xl/sharedStrings.xml" in z.namelist():
            shared = ["".join(t.text or "" for t in si.iter(NS + "t"))
                      for si in ET.fromstring(z.read("xl/sharedStrings.xml"))]
        relationships = {
            r.get("Id"): r.get("Target")
            for r in ET.fromstring(z.read("xl/_rels/workbook.xml.rels"))
        }
        sheets = {}
        for sheet in ET.fromstring(z.read("xl/workbook.xml")).iter(NS + "sheet"):
            target = relationships[sheet.get(REL + "id")]
            target = (target.lstrip("/") if target.startswith("/") else
                      posixpath.normpath(posixpath.join("xl", target)))
            rows = []
            for row in ET.fromstring(z.read(target)).iter(NS + "row"):
                cells = {}
                for cell in row.findall(NS + "c"):
                    col = re.match(r"[A-Z]+", cell.get("r")).group()
                    value = cell.find(NS + "v")
                    text = (value.text or "") if value is not None else ""
                    if cell.get("t") == "s":
                        text = shared[int(text)] if text else ""
                    elif cell.get("t") == "inlineStr":
                        text = "".join(t.text or "" for t in cell.iter(NS + "t"))
                    cells[col] = text
                rows.append(cells)
            if rows:
                header = rows[0]
                sheets[sheet.get("name")] = [
                    {label: row.get(col, "") for col, label in header.items() if label}
                    for row in rows[1:] if any(row.values())
                ]
    return sheets


EMAIL_RE = re.compile(r"[\w.!#$%&'*+/=?^`{|}~\-]+@[\w.-]+", re.UNICODE)
PHONE_RE = re.compile(
    r"^(?:\((?P<prefix>[A-Za-z]{1,4})\)\s*)?"
    r"(?P<number>\+?(?:\(\d{1,5}\)|[\d?])"
    r"(?:[\d? .\-/–−]|\(\d{1,5}\))*[\d?])(?P<rest>.*)$"
)
PHONE_LABEL_RE = re.compile(r"^\s*[({]([A-Za-z]{1,4})[)}]\s*")


def is_street_address(line):
    return bool(re.search(r"\b\d+(?:st|nd|rd|th)\s+(?:avenue|ave\.?|street|st\.?|road|rd\.?)\b", line, re.I)
                and len(re.sub(r"\D", "", re.split(r"\s+\d+(?:st|nd|rd|th)\b", line, maxsplit=1, flags=re.I)[0])) < 7)


def extract_phones(lines):
    phones, issues = [], set()
    for line in lines:
        if is_street_address(line):
            issues.add("unparsed_phone_line")
            continue
        if re.search(r"\[[^\]]*(?:unclear|illegible|uncertain|unreadable)[^\]]*\]", line, re.I):
            phones.append({"number": line, "label": "", "uncertain": True})
            issues.add("uncertain_phone")
            continue
        # Workbook buckets are heuristic too: dates and prose can appear here.
        # Retain these in phone_lines/raw, without calling them numbers.
        if re.search(r"\((?:19|20)\d{2}\s*[-–/]\s*(?:19|20)\d{2}\)", line) or re.fullmatch(
                r"(?:19|20)\d{2}\s*[-–/]\s*(?:19|20)\d{2}", line.strip()):
            issues.add("unparsed_phone_line")
            continue
        candidate = re.sub(r"^Email:\s*", "", line.strip(), flags=re.I)
        candidate = re.sub(r"^([A-Za-z]{1,4}):\s*", r"(\1)", candidate)
        # Printed annotations can start with a digit (2nd home, 8D). Keep that
        # digit out of the telephone number without altering the source line.
        annotation = ""
        tail = re.search(r"\s+(?:\d+(?:st|nd|rd|th)\b|\d{1,3}[A-Za-z](?=\s|[-(]|$))", candidate)
        if tail and len(re.sub(r"\D", "", candidate[:tail.start()])) >= 7:
            annotation = candidate[tail.start():].strip()
            candidate = candidate[:tail.start()]
        candidate = re.sub(r"(?<=\d)(?=(?:FAX|fax|hF|[hwpfcob]|asst|pager|desk|Offic|CT|uk)(?:\s|$|[(/]))", " ", candidate)
        match = PHONE_RE.match(candidate)
        if match and len(re.sub(r"\D", "", match["number"])) >= 7:
            number, rest = match["number"].strip(), match["rest"].strip()
            # Don't publish a clean-looking prefix of a corrupted number.
            suffix_label = re.fullmatch(r"[hwpfcob]{1,2}|fax", rest, re.I)
            extension = re.match(r"(?:x|ext\.?)\s*\d+", rest, re.I)
            if (rest and rest[0].isalnum() and not match["rest"][0].isspace()
                    and not suffix_label and not extension):
                issues.add("uncertain_phone")
                phones.append({"number": line, "label": "", "uncertain": True})
                continue
            label_match = PHONE_LABEL_RE.match(rest)
            label = label_match[1] if label_match else (rest if suffix_label else match["prefix"] or "")
            remainder = rest[label_match.end():] if label_match else ("" if suffix_label else rest)
            annotation = " ".join(p for p in (remainder,annotation) if p)
            phone = {"number": number, "label": label}
            if annotation:
                phone["annotation"] = annotation
            if "?" in number:
                phone["uncertain"] = True
                issues.add("uncertain_phone")
            phones.append(phone)
        elif len(re.sub(r"\D", "", line)) >= 6:
            phones.append({"number": line, "label": "", "uncertain": True})
            issues.add("uncertain_phone")
        else:
            issues.add("unparsed_phone_line")
    return phones, issues


def extract_emails(lines):
    emails, issues = [], set()
    for i, line in enumerate(lines):
        if re.fullmatch(r"\s*email\s*:?\s*", line, re.I):
            continue
        if re.match(r"^\s*(?:Email:\s*)?(?:www\.|https?://)", line, re.I):
            # The workbook field also holds websites; these are not bad emails.
            continue
        if re.search(r"\[[^\]]*(?:unclear|illegible|uncertain|unreadable)[^\]]*\]", line, re.I):
            # A suffix after an uncertainty marker is not a complete address.
            issues.add("uncertain_email")
            continue
        # Collapse only separator whitespace; never invent a missing ending.
        text = re.sub(r"\s*@\s*", "@", line)
        text = re.sub(r"(?<=\w)\s*\.\s*(?=\w)", ".", text)
        # A slash following an email domain separates multiple source addresses.
        text = re.sub(r"(@[\w.-]+)/(?=[^\s/]*@)", r"\1 ", text)
        found = []
        for match in EMAIL_RE.finditer(text):
            prefix = re.sub(r"^\s*email\s*:?\s*", "", text[:match.start()], flags=re.I).strip()
            if prefix and re.fullmatch(r"[\w.-]+", prefix):
                # A printed space inside a possible local part must not cause
                # its first word to disappear (e.g. 'elizabeth saltzman@...').
                issues.add("uncertain_email")
                continue
            found.append(match.group().rstrip(".,;"))
        if found and i and lines[i - 1].rstrip().endswith("-"):
            # A wrapped local part must not become a plausible but wrong
            # address containing only its second half. Preserve both raw lines.
            issues.add("wrapped_email")
            continue
        if not found:
            issues.add("unparsed_email_line")
        for email in found:
            if email not in emails:
                emails.append(email)
            domain = email.rsplit("@", 1)[1]
            if not re.fullmatch(r"[\w-]+(?:\.[\w-]+)*\.[A-Za-z]{2,}", domain):
                issues.add("incomplete_email")
    return emails, issues


def phone_text(phone):
    return (phone["number"] + (f" ({phone['label']})" if phone["label"] else "")
            + (" " + phone["annotation"] if phone.get("annotation") else ""))


def load_contacts(path=WORKBOOK):
    sheets = read_sheets(path)
    for name in ("Directory", "Contact lines", "Raw readings"):
        if name not in sheets:
            raise ValueError(f"Missing worksheet: {name}")
    raw_rows = sheets["Raw readings"]
    raw_by_id = {row["Entry ID"]: row for row in raw_rows}
    if len(raw_by_id) != len(raw_rows):
        raise ValueError("Duplicate entry IDs in Raw readings")
    lines_by_id = defaultdict(list)
    for row in sheets["Contact lines"]:
        lines_by_id[row["Entry ID"]].append({
            "page": int(row["PDF page"]), "column": int(row["Column"]),
            "order": int(row["Line order"]),
            "text": row["Complete source line / transcription"],
        })
    contacts, seen = [], set()
    for row in sheets["Directory"]:
        entry_id = row["Entry ID"]
        if entry_id in seen:
            raise ValueError(f"Duplicate entry ID: {entry_id}")
        seen.add(entry_id)
        heading = row["Source block type"] == "Section heading"
        if entry_id not in raw_by_id or (not lines_by_id.get(entry_id) and not heading):
            raise ValueError(f"Missing source text for {entry_id}")
        raw = raw_by_id[entry_id]["Primary transcription"]
        lines = sorted(lines_by_id[entry_id], key=lambda line: line["order"])
        if [line["order"] for line in lines] != list(range(1, len(lines) + 1)):
            raise ValueError(f"Missing or duplicate source lines for {entry_id}")
        if not heading and "\n".join(line["text"] for line in lines) != raw:
            raise ValueError(f"Source readings disagree for {entry_id}")
        pages = sorted({int(p) for p in re.findall(r"\d+", row["PDF page(s)"])} |
                       {line["page"] for line in lines})
        location = re.search(r"p\.(\d+) c\.(\d+)", row["Page / column reference"])
        if not pages or location is None:
            raise ValueError(f"Missing source location for {entry_id}")
        phone_lines = [line for line in row["Phone / fax lines"].splitlines() if line.strip()]
        email_lines = [line for line in row["Email / web lines"].splitlines() if line.strip()]
        phones, issues = extract_phones(phone_lines)
        emails, email_issues = extract_emails(email_lines)
        issues.update(email_issues)
        status = row["Transcription status"]
        if row["Source block type"] == "Unassigned source fragment":
            issues.add("uncertain_entry_association")
        if not status.startswith("Visually transcribed"):
            issues.add("machine_transcription")
        if re.search(r"\?|\[(?:[^\]]*(?:illegible|unclear|uncertain|partially|unreadable))", raw, re.I):
            issues.add("uncertain_source_text")
        if re.search(r"\[redacted\]", raw, re.I):
            issues.add("redacted_source_text")
        contacts.append({
            "id": entry_id, "page": int(location[1]), "column": location[2],
            "pages": ", ".join(map(str, pages)), "source_pages": pages,
            "ref": row["Page / column reference"], "name": row["Name / heading as read"].strip(),
            "block_type": row["Source block type"],
            "address": row["Address / other lines"].replace("\n", ", "),
            "phones": phones, "emails": emails,
            "phone_lines": phone_lines, "email_lines": email_lines,
            "status": status, "notes": row["Review notes"], "raw": raw,
            "source_lines": lines, "source": "Jeffrey_Epstein_UNKNOWN.pdf",
            "source_url": f"Contact_directory_review.html#{entry_id}",
            "review_flags": sorted(issues), "needs_review": bool(issues),
        })
    if seen != set(raw_by_id) or seen != set(lines_by_id):
        raise ValueError("Entry IDs differ between the directory and source-text sheets")
    return contacts


def astra_record(contact):
    """Compatibility format, derived from the same records as contacts.csv."""
    out = {key: contact[key] for key in (
        "id", "name", "block_type", "pages", "ref", "emails", "address", "status", "notes",
        "raw", "phone_lines", "email_lines", "source_url", "needs_review", "review_flags",
    )}
    out["phones"] = [phone_text(p) for p in contact["phones"]]
    return out


def quality_report(contacts):
    report = {
        "source": "Jeffrey_Epstein_UNKNOWN.pdf",
        "transcription_source": "Contact_directory_review.xlsx",
        "records": len(contacts),
        "source_pages": sorted({p for c in contacts for p in c["source_pages"]}),
        "source_lines": sum(len(c["source_lines"]) for c in contacts),
        "statuses": dict(Counter(c["status"] for c in contacts)),
        "block_types": dict(Counter(c["block_type"] for c in contacts)),
        "records_needing_review": sum(c["needs_review"] for c in contacts),
        "review_flags": dict(Counter(flag for c in contacts for flag in c["review_flags"])),
        "accuracy_note": "Coverage is not accuracy. Machine text and scan-limited visual "
                         "transcriptions are not independently verified. Compare with the PDF scan.",
    }
    audit_path = ROOT / "review/change_log.json"
    if audit_path.exists():
        audit = json.loads(audit_path.read_text())
        entries = audit.get("entries", {})
        reviewed = {c["id"] for c in contacts if c["id"] in entries
                    and entries[c["id"]]["text"] == c["raw"]
                    and entries[c["id"]]["name"] == c["name"]}
        report["recorded_visual_review"] = {
            "source_sha256": audit["source_sha256"],
            "records": len(reviewed),
            "fully_reviewed_pages": [p for p in report["source_pages"]
                                     if all(c["id"] in reviewed for c in contacts if p in c["source_pages"])],
            "audit_log": "review/change_log.json",
        }
    return report


def write_json(path, data):
    Path(path).write_text(json.dumps(data, ensure_ascii=False, indent=1) + "\n", encoding="utf-8")
