"""Regression checks for text preservation, field parsing and synchronized exports."""
import csv
import hashlib
import json
import re
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from build_xref import build_matches
from contact_data import (ROOT, WORKBOOK, astra_record, extract_emails, extract_phones,
                          load_contacts, phone_text, read_sheets)
from apply_contact_reviews import read_decisions


class FieldParsingTests(unittest.TestCase):
    def test_phone_labels_and_parenthesized_area_codes(self):
        phones, flags = extract_phones(["(212) 555-0100 (h)", "00 331 538 97260(w)"])
        self.assertEqual(phones, [{"number": "(212) 555-0100", "label": "h"},
                                  {"number": "00 331 538 97260", "label": "w"}])
        self.assertFalse(flags)

    def test_phone_annotations_extensions_and_optional_zero(self):
        for line in ("1 917 969 2158 (p) Rufus", "212 475 8000x101", "(o)44 (0) 20 7824 7526"):
            phones, flags = extract_phones([line])
            self.assertEqual(len(phones), 1)
            self.assertFalse(flags, line)
        phone = extract_phones(["1 917 969 2158 (p) Rufus"])[0][0]
        self.assertEqual(phone_text(phone), "1 917 969 2158 (p) Rufus")

    def test_corrupted_digits_are_preserved_and_flagged(self):
        line = "001 212 555 01O0"
        phones, flags = extract_phones([line])
        self.assertEqual(phones[0]["number"], line)
        self.assertIn("uncertain_phone", flags)

    def test_scan_uncertainty_cannot_become_a_complete_phone_prefix(self):
        line = "001 212 555 [unclear]100"
        phones, flags = extract_phones([line])
        self.assertEqual(phones[0]["number"], line)
        self.assertTrue(phones[0]["uncertain"])
        self.assertIn("uncertain_phone", flags)

    def test_dates_in_source_notes_are_not_phone_numbers(self):
        phones, flags = extract_phones(['"Palm Beach (2004-2005) Witness, source note."'])
        self.assertFalse(phones)
        self.assertIn("unparsed_phone_line", flags)

    def test_numeric_annotations_are_not_appended_to_phone_numbers(self):
        for suffix in ("2nd home", "71st St", "8D", "12C (fax)"):
            phones, flags = extract_phones(["212 555 0100 " + suffix])
            self.assertEqual(phones[0]["number"], "212 555 0100")
            self.assertEqual(phones[0]["annotation"], suffix)
            self.assertFalse(flags)

    def test_explicit_phone_labels_do_not_become_uncertain_digits(self):
        for line in ("W: 001 212 555 0100", "212 555 0100FAX", "212 555 0100hF", "Email: 0585 336319"):
            phones, flags = extract_phones([line])
            self.assertEqual(len(phones), 1)
            self.assertFalse(flags, line)

    def test_numbered_street_address_is_not_a_telephone(self):
        phones, flags = extract_phones(["1422 130th Avenue N.E. (w)", "(Hm)3441 134th Ave. N.E."])
        self.assertFalse(phones)

    def test_website_is_retained_in_source_field_without_false_email_flag(self):
        emails, flags = extract_emails(["Email: www.example.com"])
        self.assertFalse(emails)
        self.assertFalse(flags)

    def test_emails_are_not_limited_to_three_letter_domains(self):
        emails, flags = extract_emails(["Email:", "archivist@example.museum", "name @ example . com"])
        self.assertEqual(emails, ["archivist@example.museum", "name@example.com"])
        self.assertFalse(flags)

    def test_incomplete_email_is_not_completed(self):
        emails, flags = extract_emails(["Email: palexander@alexanderrogil"])
        self.assertEqual(emails, ["palexander@alexanderrogil"])
        self.assertIn("incomplete_email", flags)

    def test_slash_separated_emails_keep_both_local_parts(self):
        emails, flags = extract_emails(["one@example.com/two@example.org"])
        self.assertEqual(emails, ["one@example.com", "two@example.org"])
        self.assertFalse(flags)

    def test_wrapped_email_does_not_publish_only_its_last_half(self):
        emails, flags = extract_emails(["Email: Ste-", "ven_Bentinck@msn.com"])
        self.assertFalse(emails)
        self.assertIn("wrapped_email", flags)

    def test_unclear_email_does_not_publish_a_plausible_suffix(self):
        emails, flags = extract_emails(["Email: first[unclear]last@example.com"])
        self.assertFalse(emails)
        self.assertIn("uncertain_email", flags)

    def test_space_in_printed_local_part_is_not_silently_truncated(self):
        emails, flags = extract_emails(["Email: first last@example.com"])
        self.assertFalse(emails)
        self.assertIn("uncertain_email", flags)

    def test_reader_resolves_sheet_relationships_and_inline_strings(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "fixture.xlsx"
            with zipfile.ZipFile(path, "w") as z:
                z.writestr("xl/workbook.xml", '<workbook xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main" xmlns:r="http://schemas.openxmlformats.org/officeDocument/2006/relationships"><sheets><sheet name="Directory" sheetId="9" r:id="rId7"/></sheets></workbook>')
                z.writestr("xl/_rels/workbook.xml.rels", '<Relationships><Relationship Id="rId7" Target="/xl/worksheets/custom.xml"/></Relationships>')
                z.writestr("xl/worksheets/custom.xml", '<worksheet xmlns="http://schemas.openxmlformats.org/spreadsheetml/2006/main"><sheetData><row r="1"><c r="B1" t="inlineStr"><is><t>Name</t></is></c></row><row r="2"><c r="B2" t="inlineStr"><is><r><t>Sally </t></r><r><t>Sussex</t></r></is></c></row></sheetData></worksheet>')
            self.assertEqual(read_sheets(path), {"Directory": [{"Name": "Sally Sussex"}]})


class SourceReviewTests(unittest.TestCase):
    def test_split_boxes_in_one_column_preserve_both_fragments(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / "Jeffrey_Epstein_UNKNOWN.pdf").write_bytes(b"source fixture")
            pages = root / "review/pages"
            pages.mkdir(parents=True)
            payload = {"source_sha256":hashlib.sha256(b"source fixture").hexdigest(),"entries":[{
                "id":"E0001","name":"Example","parts":[
                    {"page":1,"column":1,"lines":["Example"]},
                    {"page":1,"column":1,"lines":["First line","Second line"]},
                ],
            }]}
            (pages / "001.json").write_text(json.dumps(payload))
            with patch("apply_contact_reviews.ROOT", root):
                decisions, _ = read_decisions()
                self.assertEqual(decisions["E0001"]["parts"][0]["lines"], ["Example","First line","Second line"])
                payload["source_sha256"] = "different source"
                (pages / "001.json").write_text(json.dumps(payload))
                with self.assertRaisesRegex(ValueError, "different PDF"):
                    read_decisions()


class ContactPipelineTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.contacts = load_contacts()
        cls.by_id = {c["id"]: c for c in cls.contacts}
        cls.exported = json.loads((ROOT / "contacts.json").read_text())

    def test_source_coverage_and_line_preservation(self):
        self.assertEqual(len(self.contacts), 1625)
        self.assertEqual({p for c in self.contacts for p in c["source_pages"]}, set(range(1, 96)))
        self.assertGreaterEqual(sum(len(c["source_lines"]) for c in self.contacts), 10915)
        for c in self.contacts:
            if c["block_type"] != "Section heading":
                self.assertEqual("\n".join(line["text"] for line in c["source_lines"]), c["raw"])

    def test_every_source_record_has_an_applied_visual_review(self):
        quality = json.loads((ROOT / "contacts_quality.json").read_text())
        audit = json.loads((ROOT / "review/change_log.json").read_text())
        self.assertEqual(set(audit["entries"]), set(self.by_id))
        self.assertEqual(quality["recorded_visual_review"]["records"], len(self.contacts))
        self.assertEqual(quality["recorded_visual_review"]["fully_reviewed_pages"], list(range(1,96)))
        self.assertEqual(audit["machine_entries_remaining"], 0)
        self.assertTrue({f"E{i:04}" for i in range(1,1615)} <= set(self.by_id))

    def test_additional_contact_and_annotation_have_correct_types(self):
        contact = self.by_id["E1624"]
        self.assertEqual(contact["name"], "London Airways")
        self.assertEqual(contact["phones"][0]["number"], "0207-403 2228")
        self.assertEqual(self.by_id["E1446"]["block_type"], "Source annotation")
        self.assertFalse(any("130th" in p["number"] for p in self.by_id["E1291"]["phones"]))
        self.assertIn("0101 212 734 7905", self.by_id["E0346"]["raw"])

    def test_scan_checked_regressions(self):
        self.assertEqual(self.by_id["E0001"]["phones"][0]["number"], "07944 574 202")
        self.assertEqual(self.by_id["E0011"]["name"], "Allan Paul")
        self.assertEqual(self.by_id["E0005"]["name"], "Agnew, Marie Claire & John")
        self.assertIn("19 Rue De Lille", self.by_id["E0003"]["address"])
        self.assertEqual(self.by_id["E0007"]["phones"][2]["annotation"], "Sally")
        self.assertNotIn("5a11y", self.by_id["E0007"]["raw"])

    def test_cross_page_continuation_keeps_both_locations(self):
        contact = self.by_id["E0013"]
        self.assertEqual(contact["source_pages"], [1, 2])
        self.assertEqual(contact["phones"][-1]["number"], "0207-637 8655")
        self.assertEqual(contact["source_lines"][-1]["page"], 2)

    def test_review_transcription_and_exports_agree(self):
        html = (ROOT / "Contact_directory_review.html").read_text()
        dataset = json.loads(re.search(r'<script id="dataset" type="application/json">(.*?)</script>', html, re.S)[1])
        review = {r["id"]: r for r in dataset["records"]}
        self.assertEqual(set(self.by_id), set(review))
        for c in self.contacts:
            self.assertEqual(c["name"], review[c["id"]]["name"])
            self.assertEqual(c["raw"], review[c["id"]]["text"])
        for actual, expected in zip(self.exported, self.contacts):
            for key in expected:
                self.assertEqual(actual[key], expected[key], (actual["id"], key))

    def test_all_export_formats_keep_ids_and_full_text(self):
        with open(ROOT / "contacts.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        self.assertEqual(len(rows), len(self.exported))
        for row, c in zip(rows, self.exported):
            self.assertEqual(row["id"], c["id"])
            self.assertEqual(row["raw"], c["raw"])
            self.assertEqual(row["name"], c["name"])
        astra = json.loads((ROOT / "astra_contacts.json").read_text())
        self.assertEqual(astra, [astra_record(c) for c in self.exported])

    def test_machine_text_remains_flagged_and_handwriting_retained(self):
        machine = [c for c in self.contacts if c["status"].startswith("Machine")]
        self.assertLess(len(machine), 1414)
        self.assertTrue(all(c["needs_review"] for c in machine))
        self.assertTrue(all("machine_transcription" in c["review_flags"] for c in machine))
        self.assertEqual({c["page"] for c in self.contacts if c["page"] >= 93}, {93, 94, 95})

    def test_visual_reviews_restore_missing_lines_and_correct_digits(self):
        chatwal = self.by_id["E0155"]
        self.assertEqual(chatwal["name"], "Chatwal, Vikram")
        self.assertIn("Hampshire Hotels & Resorts", chatwal["raw"])
        self.assertIn("1 212 474 9880", chatwal["raw"])
        self.assertIn("1 212 320 2900", chatwal["raw"])
        self.assertNotIn("machine_transcription", chatwal["review_flags"])
        self.assertEqual(self.by_id["E0161"]["name"], "Cicognani, Pietro & Alejandra")
        self.assertEqual(len(self.by_id["E0161"]["phones"]), 5)
        self.assertIn("(w)6 W. 14th Street", self.by_id["E0110"]["raw"])
        self.assertIn("0207-376 7308", self.by_id["E0132"]["raw"])

    def test_recovered_handwritten_headings_keep_original_entry_ids(self):
        self.assertEqual(self.by_id["E1615"]["name"], "Visitors Massage (P.B)")
        self.assertEqual(self.by_id["E1616"]["name"], "Important e-mail / addresses")
        self.assertEqual(self.by_id["E1615"]["block_type"], "Section heading")
        self.assertEqual(self.by_id["E0001"]["name"], "Abby")

    def test_unassigned_address_is_not_attached_to_a_named_contact(self):
        self.assertEqual(self.by_id["E1069"]["source_pages"], [61])
        fragment = self.by_id["E1617"]
        self.assertEqual(fragment["raw"], "London\nSW1X 7PA")
        self.assertIn("uncertain_entry_association", fragment["review_flags"])
        self.assertNotIn("FRANCE", self.by_id["E1124"]["raw"])
        self.assertEqual(self.by_id["E1618"]["name"], "FRANCE (FR)")

    def test_review_log_retains_previous_readings(self):
        log = json.loads((ROOT / "review/change_log.json").read_text())
        self.assertIn("1 212 474 gR80 Ww}", log["entries"]["E0155"]["previous_text"])
        for entry_id, entry in log["entries"].items():
            self.assertEqual(entry["text"], self.by_id[entry_id]["raw"])

    def test_cross_references_use_canonical_fields(self):
        flights = json.loads((ROOT / "flights.json").read_text())
        expected = build_matches(flights, self.contacts)
        actual = json.loads((ROOT / "xref.json").read_text())
        self.assertEqual(actual, expected)
        for match in actual:
            c = self.by_id[match["contact_id"]]
            self.assertEqual(match["phones"], [phone_text(p) for p in c["phones"]])
            self.assertEqual(match["emails"], c["emails"])
            self.assertEqual(c["block_type"], "Entry / source block")

    def test_missing_contact_lines_fail_instead_of_silently_dropping_text(self):
        sheets = read_sheets(WORKBOOK)
        sheets["Contact lines"] = [r for r in sheets["Contact lines"] if r["Entry ID"] != "E0001"]
        with patch("contact_data.read_sheets", return_value=sheets):
            with self.assertRaisesRegex(ValueError, "Missing source text for E0001"):
                load_contacts()

    def test_embedded_explorer_data_matches_exports(self):
        html = (ROOT / "flight_logs_explorer.html").read_text()
        for element, path in (("contact-data", "contacts.json"), ("xref-data", "xref.json")):
            embedded = re.search(r'<script id="' + element + r'" type="application/json">(.*?)</script>', html, re.S)[1]
            self.assertEqual(json.loads(embedded), json.loads((ROOT / path).read_text()))


if __name__ == "__main__":
    unittest.main()
