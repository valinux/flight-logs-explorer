#!/usr/bin/env python3
"""Parse tesseract TSV output (per-column crops) into structured contacts.

Input:  ocr/page-XXX<band>.tsv  (psm 4 word-level TSV with bounding boxes)
Output: contacts.json, contacts.csv
Also cross-references contact names against passengers in flights.json.
"""
import csv
import glob
import json
import re
import statistics
from pathlib import Path

OCR_DIR = Path("ocr")

# handwritten pages: OCR output is garbage, replaced by manual transcriptions
HANDWRITTEN_PAGES = {93, 94, 95}

DIGIT_FIX = str.maketrans({
    "O": "0", "o": "0", "Q": "0",
    "l": "1", "I": "1", "|": "1", "!": "1", "i": "1",
    "Z": "2", "z": "2",
    "S": "5", "s": "5", "$": "5",
    "G": "6",
    "B": "8",
    "q": "9",
})

EMAIL_RE = re.compile(r"[A-Za-z0-9._%+\-]+@[A-Za-z0-9.\-]+\.[A-Za-z]{1,3}")
LABEL_RE = re.compile(r"\(([A-Za-z]{1,3})\)")
MIN_CONF = 25


def load_words(tsv_path):
    words = []
    with open(tsv_path, newline="", encoding="utf-8", errors="replace") as f:
        for row in csv.DictReader(f, delimiter="\t", quoting=csv.QUOTE_NONE):
            if row.get("level") != "5":
                continue
            text = (row.get("text") or "").strip()
            if not text:
                continue
            try:
                conf = float(row["conf"])
            except (ValueError, KeyError):
                continue
            if conf < MIN_CONF:
                continue
            words.append({
                "t": text,
                "x": int(row["left"]), "y": int(row["top"]),
                "w": int(row["width"]), "h": int(row["height"]),
            })
    return words


def fix_digits(token):
    """Map common OCR letter->digit confusions, but only for tokens that end
    up mostly numeric (phone-number contexts)."""
    fixed = token.translate(DIGIT_FIX)
    digits = sum(c.isdigit() for c in fixed)
    letters = sum(c.isalpha() for c in fixed)
    if digits >= 2 and digits >= letters:
        return fixed
    return token


def fix_line(line):
    return " ".join(fix_digits(tok) for tok in line.split(" "))


def cluster_lines(words):
    """Group words into lines by vertical overlap; return list of
    (y_center, text) sorted top to bottom."""
    if not words:
        return []
    mh = statistics.median(w["h"] for w in words)
    words = sorted(words, key=lambda w: w["y"] + w["h"] / 2)
    lines = []
    for w in words:
        yc = w["y"] + w["h"] / 2
        for line in lines:
            if abs(yc - line["yc"]) <= max(10, 0.55 * mh):
                line["words"].append(w)
                n = len(line["words"])
                line["yc"] = (line["yc"] * (n - 1) + yc) / n
                break
        else:
            lines.append({"yc": yc, "words": [w]})
    out = []
    for line in lines:
        ws = sorted(line["words"], key=lambda w: w["x"])
        text = " ".join(w["t"] for w in ws)
        text = re.sub(r"\s+", " ", text).strip()
        if text:
            out.append((line["yc"], text))
    out.sort(key=lambda l: l[0])
    return out


def split_blocks(lines):
    """Split a column's lines into contact blocks at large vertical gaps."""
    if not lines:
        return []
    pitches = [lines[i + 1][0] - lines[i][0] for i in range(len(lines) - 1)]
    pitches = [p for p in pitches if p > 4]
    med = statistics.median(pitches) if pitches else 30
    blocks, cur = [], [lines[0][1]]
    for i in range(1, len(lines)):
        gap = lines[i][0] - lines[i - 1][0]
        if gap > 1.65 * med:
            blocks.append(cur)
            cur = []
        cur.append(lines[i][1])
    blocks.append(cur)
    return blocks


PHONE_LINE_RE = re.compile(
    r"(?:\+?\d[\d .\-/]{5,}\d)\s*(?:\([A-Za-z]{1,3}\))?"
)


def looks_like_details(line):
    fixed = fix_line(line)
    digits = sum(c.isdigit() for c in fixed)
    return digits >= 6 or "@" in line or line.lower().startswith("email")


def is_noise_block(lines):
    text = " ".join(lines)
    if len(text) <= 3:
        return True
    if all(len(l) <= 2 for l in lines) and not any(c.isdigit() for c in text):
        return True
    return False


def extract_block(page, col, lines):
    raw = "\n".join(lines)
    emails = EMAIL_RE.findall(raw)

    phones = []
    for line in lines:
        fixed = fix_line(line)
        for m in PHONE_LINE_RE.finditer(fixed):
            num = m.group(0)
            lab = ""
            lm = LABEL_RE.search(fixed[m.end():m.end() + 8])
            if lm:
                lab = lm.group(1).lower()
            digits = re.sub(r"\D", "", num)
            if len(digits) >= 6:
                phones.append({"number": num.strip(), "label": lab})

    name = lines[0].strip(" .,;:")
    addr_lines = []
    for line in lines[1:]:
        fixed = fix_line(line)
        rest = PHONE_LINE_RE.sub("", fixed)
        rest = EMAIL_RE.sub("", rest)
        rest = re.sub(r"(?i)^email\s*:?", "", rest).strip(" ,;:-")
        if rest and not re.fullmatch(r"[\d .\-()/]+", rest):
            addr_lines.append(rest)
    return {
        "page": page,
        "column": col,
        "name": name,
        "address": ", ".join(addr_lines),
        "phones": phones,
        "emails": emails,
        "raw": raw,
    }


def norm_name(s):
    s = s.lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def band_key(path):
    m = re.search(r"page-\d{3}([A-Z0-9]*)\.tsv$", path)
    tag = m.group(1)
    if tag == "":
        return 0
    if tag == "L":
        return 1
    if tag == "R":
        return 2
    return int(tag)


def main():
    # ---- flight-log passenger index for cross-reference ----
    with open("flights.json") as f:
        flights = json.load(f)
    h = flights["headers"]
    i_first, i_last = h.index("First Name"), h.index("Last Name")
    pax = {}  # (last, first_initial) -> {"name": display, "flights": n}
    for r in flights["rows"]:
        first, last = str(r[i_first]).strip(), str(r[i_last]).strip()
        if not last or last in ("?", "No Records"):
            continue
        key = (norm_name(last), norm_name(first)[:1])
        if not key[0] or not key[1]:
            continue
        e = pax.setdefault(key, {"name": f"{first} {last}".strip(), "flights": 0})
        e["flights"] += 1

    contacts = []
    empty_pages = []
    prev = None  # last contact, for cross-column/page continuations

    for page in range(1, 96):
        if page in HANDWRITTEN_PAGES:
            prev = None
            continue
        files = sorted(glob.glob(str(OCR_DIR / f"page-{page:03d}*.tsv")), key=band_key)
        if not files:
            empty_pages.append(page)
            prev = None
            continue
        got_any = False
        for fp in files:
            col = Path(fp).stem.replace(f"page-{page:03d}", "")
            words = load_words(fp)
            if len(words) < 5:
                continue
            got_any = True
            lines = cluster_lines(words)
            for blines in split_blocks(lines):
                blines = [l for l in blines if l.strip()]
                if not blines or is_noise_block(blines):
                    continue
                if looks_like_details(blines[0]) and prev is not None:
                    extra = extract_block(page, col, blines)
                    prev["raw"] += "\n" + extra["raw"]
                    prev["phones"].extend(extra["phones"])
                    prev["emails"].extend(e for e in extra["emails"] if e not in prev["emails"])
                    if extra["address"]:
                        prev["address"] = (prev["address"] + ", " + extra["address"]).strip(", ")
                    continue
                c = extract_block(page, col, blines)
                contacts.append(c)
                prev = c
        if not got_any:
            empty_pages.append(page)
            prev = None

    # ---- manual transcriptions of the handwritten pages ----
    with open("manual_contacts.json") as f:
        contacts.extend(json.load(f))

    # ---- cross-reference with flight logs ----
    for c in contacts:
        matches = {}
        parts = re.split(r"\s+&\s+|\s+and\s+", c["name"])
        for part in parts:
            part = part.strip()
            if not part:
                continue
            n = norm_name(part)
            tokens = n.split()
            if not tokens:
                continue
            if "," in part:
                last = norm_name(part.split(",", 1)[0])
                first = norm_name(part.split(",", 1)[1] if "," in part else "")
            else:
                last = tokens[-1]
                first = tokens[0]
            fi = first[:1]
            for (l, f_init), e in pax.items():
                if l == last and (not fi or not f_init or fi == f_init):
                    matches[e["name"]] = e["flights"]
        c["flight_log_names"] = sorted(matches)
        c["flight_count"] = sum(matches.values())

    contacts.sort(key=lambda c: (c["page"], c["column"]))

    with open("contacts.json", "w") as f:
        json.dump(contacts, f, indent=1, ensure_ascii=False)

    with open("contacts.csv", "w", newline="") as f:
        w = csv.writer(f)
        w.writerow(["page", "name", "address", "phones", "emails", "notes",
                    "in_flight_logs", "flight_count", "raw"])
        for c in contacts:
            w.writerow([
                c["page"], c["name"], c["address"],
                "; ".join(p["number"] + (f" ({p['label']})" if p["label"] else "") for p in c["phones"]),
                "; ".join(c["emails"]),
                c.get("notes", ""),
                "; ".join(c["flight_log_names"]), c["flight_count"],
                c["raw"].replace("\n", " | "),
            ])

    n_pax = sum(1 for c in contacts if c["flight_log_names"])
    print(f"contacts: {len(contacts)}  pages-empty/unreadable: {empty_pages}")
    print(f"with phones: {sum(1 for c in contacts if c['phones'])}  "
          f"with emails: {sum(1 for c in contacts if c['emails'])}  "
          f"cross-referenced to flight logs: {n_pax}")


if __name__ == "__main__":
    main()
