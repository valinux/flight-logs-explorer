"""Small helpers for recording an actual image review, separate from OCR drafts."""
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parent


def approve_indexed(page, names, edits):
    """Convenient line-number notation for the just-displayed OCR draft."""
    candidates = json.loads((ROOT / "review_work/candidates.json").read_text())
    replacements = {}
    for (entry_id, column), changes in edits.items():
        entry = next(r for r in candidates if r["id"]==entry_id)
        part = next(p for p in entry["parts"] if p["page"]==page and p["column"]==column)
        for index, value in changes.items():
            replacements[(entry_id,part["lines"][index])] = value
    approve_page(page,names,replacements)


def approve_page(page, names, replacements, additions=None):
    """Call only after checking every source entry/line on this PDF page.

    Names are explicit for entries starting on the page. Replacements are exact
    source-line edits, so a changed OCR draft cannot silently reuse old decisions.
    """
    candidates = json.loads((ROOT / "review_work/candidates.json").read_text())
    changes = dict(replacements)
    entries = []
    for record in candidates:
        parts = [dict(p, lines=list(p["lines"])) for p in record["parts"] if p["page"] == page]
        if not parts:
            continue
        for part in parts:
            corrected = []
            for line in part["lines"]:
                replacement = changes.pop((record["id"], line), line)
                corrected.extend(replacement if isinstance(replacement,list) else [replacement])
            part["lines"] = corrected
        if additions and record["id"] in additions:
            parts = additions[record["id"]]
        entries.append({"id":record["id"],"name":names.get(record["id"]),"parts":parts})
    if changes:
        raise ValueError(f"Edits did not match current OCR draft: {changes}")
    path = ROOT / "review/pages" / f"{page:03d}.json"
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps({
        "page":page,"reviewed_on":"2026-09-19",
        "method":"Visual comparison of every entry on the original PDF scan; enlarged crops for ambiguous readings.",
        "source_sha256":hashlib.sha256((ROOT/"Jeffrey_Epstein_UNKNOWN.pdf").read_bytes()).hexdigest(),
        "entries":entries,
    },ensure_ascii=False,indent=1)+"\n")
    print(f"Recorded visual review of page {page}: {len(entries)} entry fragments")
