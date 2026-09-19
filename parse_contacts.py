#!/usr/bin/env python3
"""Build synchronized contact exports from the source-linked review workbook.

Run this after updating Contact_directory_review.xlsx, then run build.py to embed
these data in the explorer. No OCR, network or third-party packages needed.
"""
import csv
import json
from collections import defaultdict

from build_xref import build_matches
from contact_data import ROOT, astra_record, load_contacts, phone_text, quality_report, write_json

CSV_FIELDS = ["page", "name", "address", "phones", "emails", "notes",
              "in_flight_logs", "flight_count", "raw", "id", "column", "pages", "ref",
              "status", "block_type", "needs_review", "review_flags", "source_url",
              "phone_lines", "email_lines"]


def csv_record(contact):
    row = {key: contact.get(key, "") for key in CSV_FIELDS}
    row.update(
        phones="; ".join(phone_text(p) for p in contact["phones"]),
        emails="; ".join(contact["emails"]),
        in_flight_logs="; ".join(contact["flight_log_names"]),
        review_flags="; ".join(contact["review_flags"]),
        phone_lines="\n".join(contact["phone_lines"]),
        email_lines="\n".join(contact["email_lines"]),
    )
    return row


def write_csv(path, contacts):
    with open(path, "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=CSV_FIELDS, lineterminator="\n")
        writer.writeheader()
        writer.writerows(csv_record(c) for c in contacts)


def write_exports(contacts, matches):
    """Keep candidate-match annotations and all derived exports in sync."""
    by_contact = defaultdict(list)
    for match in matches:
        by_contact[match["contact_id"]].append(match)
    for contact in contacts:
        candidates = by_contact[contact["id"]]
        contact["flight_log_names"] = sorted({m["passenger"] for m in candidates})
        contact["flight_count"] = sum(m["flights"] for m in candidates)
        contact["flight_log_matches"] = [
            {k: m[k] for k in ("passenger", "flights", "match", "score")}
            for m in candidates
        ]
    write_json(ROOT / "contacts.json", contacts)
    write_csv(ROOT / "contacts.csv", contacts)
    write_json(ROOT / "astra_contacts.json", [astra_record(c) for c in contacts])
    write_json(ROOT / "xref.json", matches)
    write_csv(ROOT / "contacts_review.csv", [c for c in contacts if c["needs_review"]])
    report = quality_report(contacts)
    write_json(ROOT / "contacts_quality.json", report)
    return report


def main():
    contacts = load_contacts()
    flights = json.loads((ROOT / "flights.json").read_text(encoding="utf-8"))
    matches = build_matches(flights, contacts)
    report = write_exports(contacts, matches)
    print(f"Wrote {len(contacts)} source records across {len(report['source_pages'])} PDF pages; "
          f"{report['source_lines']} complete source lines.")
    print(f"Review queue: {report['records_needing_review']} records; "
          f"candidate passenger matches: {len(matches)}.")
    print("Run python3 build.py to refresh the explorer.")


if __name__ == "__main__":
    main()
