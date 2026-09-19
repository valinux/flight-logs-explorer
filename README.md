# ✈ Flight Logs & Contacts Explorer

**An offline, self-contained HTML explorer for the released Jeffrey Epstein flight logs
(5,001 entries) and the scanned 95-page contact directory fax — searchable flights,
source-linked contacts, and a fuzzy cross-reference between passengers and contacts.**

No server, no dependencies, no internet required: open one HTML file and explore.

**[▶ Try it live on GitHub Pages](https://valinux.github.io/flight-logs-explorer/)**

![Flights tab](screenshots/flights-tab.png)

---

## What's inside

### ✈ Flights tab
Every flight-log entry from `Flight_Logs.xlsx` (1995–2015), fully client-side:

- Instant global search (passengers, airports, tail numbers, flight numbers) with highlighting
- Facet filters: year, aircraft model, tail number, aircraft type, departure/arrival, known, data source
- Live stats (flights, passengers, aircraft, airports), sortable columns, column picker
- Row detail modal, pagination, CSV export of the current filter

### ☎ Contacts tab
The scanned fax contact directory (`Jeffrey_Epstein_UNKNOWN.pdf`), shown through the
**review edition** (`Contact_directory_review.html`) with 1,625 entries and a
**side-by-side scan-image comparison** for every record.

- Toggle button switches to a **contact table** using the same 1,625 source records.
  CSV export, complete-text search, transcription-status filters and source links
  use the visually reviewed transcription of all 95 pages.
- All 1,625 records have recorded visual reviews. The 90 records with scan or parsing
  limitations remain flagged; page coverage does not establish perfect character accuracy.
  Counts include headings, repeated listings and annotations, not unique people.
- [Read the source review and case-study report](review/REPORT.md).

![Contacts tab](screenshots/contacts-tab.png)

### ⇄ Cross-Reference tab
Fuzzy matching between **295 flight-log passengers** and **1,625 directory entries** —
**152 candidate matches across 103 passengers and 132 contacts**, in four confidence tiers:

| Tier | Meaning |
|---|---|
| **Exact** | Full first + last name match (either order) |
| **Nickname** | Same last name, related first names (Bill↔William, Mandy↔Amanda, ~90 diminutive groups + prefix matching) |
| **Initial** | Same last name, same first initial (abbreviated entries) |
| **Weak** | Single-name handwritten entries matched on rare first names — educated guesses, clearly labeled |

- Filter by match type, search, sort, CSV export
- Modal detail per match, with one-click jump to the passenger's flights

![Cross-reference tab](screenshots/xref-tab.png)

---

## How to use

1. Clone or download this repository.
2. Open **`flight_logs_explorer.html`** in any modern browser.
3. Keep `Contact_directory_review.html` in the same folder — the Contacts tab loads it
   in an embedded frame. Everything else is embedded in the main file.

That's it. Deep links supported: `#flights`, `#contacts`, `#xref`.

## Exports

Pre-built datasets, ready for your own analysis:

| File | Contents |
|---|---|
| `flights.json` | 5,001 flight-log rows, dates normalized to ISO |
| `contacts.json` / `contacts.csv` | 1,625 source records, complete text, source references, review flags and candidate flight matches |
| `astra_contacts.json` | Compatibility export derived from the same contact records |
| `xref.json` | 152 candidate passenger ↔ contact matches with confidence tiers |
| `contacts_review.csv` | 90 records with scan or parsing limitations |
| `contacts_quality.json` | Coverage, transcription-status totals and review-flag counts |

The flight, contact-table and cross-reference views have an **Export CSV** button
that downloads the filtered rows. Switch from the scan-comparison view to the
contact table to export contacts.

The contact CSV retains the original nine columns and adds stable entry IDs,
all source pages, transcription status and review metadata. `raw` contains quoted
multiline text; read it with a CSV parser rather than splitting on newlines.
`phone_lines` and `email_lines` retain the complete source fields, including text
that cannot be parsed confidently. Numbers remain strings with their original
leading zeros. `needs_review = false` means no automated flag was raised, not that
the contact details were independently verified.

## How to rebuild from source

Rebuilding the distributed datasets requires only Python 3's standard library:

```bash
python3 extract.py            # Flight_Logs.xlsx            -> flights.json
python3 apply_contact_reviews.py  # apply recorded scan reviews to workbook, HTML and TXT
python3 parse_contacts.py     # review workbook -> contacts, astra, xref and review reports
python3 build.py              # embed everything -> flight_logs_explorer.html
python3 -m unittest -v test_contacts.py
```

`Contact_directory_review.xlsx` is the shared transcription source. The importer
reads the **Directory**, **Contact lines** and **Raw readings** sheets by name,
preserves entry boundaries and continuations, and checks that contact text agrees
between sheets. It never converts letters in ordinary words to digits or fills
in missing phone digits or email endings. Source headings remain explicit records
and are excluded from passenger matching. No alternate PDF text-layer reading is
used to fill gaps or redactions.

`extract_astra.py` is a compatibility command that now rebuilds all contact exports;
`build_xref.py` can also recompute matches from `contacts.json`. The handwritten
transcriptions for pages 93–95 are included in the review workbook. The older
`manual_contacts.json`, `preprocess_cols.py`, `cols/` and `ocr/` are historical OCR
inputs and are no longer used to generate the distributed contact datasets.

The recorded corrections are in `review/pages/` (pages 1–10 and 13–95), with
pages 11–12 in `review/verified_transcriptions.txt`. Each page decision records the
PDF's SHA-256. `review/additions.json` restores missed contacts/headings;
`review/geometry.json` records explicit boundary corrections and the unassigned
page-62 fragment. `apply_contact_reviews.py` checks the source hash, requires every
fragment of an entry, and applies those decisions to the workbook, HTML and TXT.
It retains previous and corrected readings in `review/change_log.json`.

To change a reading, compare the source scan, update its review decision, and run
the three contact rebuild commands above. The tests check the full text, IDs,
source coverage, field parsing and agreement of every distributed export.

An optional second OCR draft can be generated with `review_ocr.py` and
`review_vision.m`. This review-only workflow needs macOS Vision and the optional
Python packages Pillow, PyMuPDF and NumPy; distributed exports need only Python's
standard library. OCR drafts never enter exports without recorded visual review.

```bash
python3 review_ocr.py 11 12    # deskew/reflow visible source lines into review_work/
clang -fobjc-arc -framework Foundation -framework AppKit -framework Vision review_vision.m -o /tmp/review_vision
/tmp/review_vision review_work/jobs.json
python3 review_ocr.py --collect
```

## Data sources & accuracy

- `Flight_Logs.xlsx` — flight logs as released in court filings / FOIA.
- `Jeffrey_Epstein_UNKNOWN.pdf` — scanned fax contact directory ("the black book"),
  a public court-released document. It is a degraded fax: OCR may contain digit-level
  errors. Every source record has now been visually reviewed, while unreadable
  characters, clipped fields and redactions remain explicitly unresolved.
  **The scan is authoritative** — compare against the source images before relying on
  any entry.
- Presence in these documents is **not evidence of wrongdoing**; they are published
  records reproduced here for research and journalism.

## Repository layout

```
flight_logs_explorer.html      ← the app (open this)
Contact_directory_review.html  ← review edition, embedded in the Contacts tab
Contact_directory_review.xlsx / .txt
Flight_Logs.xlsx, Jeffrey_Epstein_UNKNOWN.pdf   ← source documents
extract.py, contact_data.py, parse_contacts.py,
extract_astra.py, build_xref.py, build.py       ← pipeline
flights.json, contacts.json/.csv, astra_contacts.json,
xref.json                                    ← datasets
contacts_review.csv, contacts_quality.json    ← review queue and coverage report
apply_contact_reviews.py, review_tools.py     ← recorded source corrections
review/REPORT.md, review/pages/, review/change_log.json
review_ocr.py, review_vision.m                 ← optional OCR comparison workflow
test_contacts.py                             ← extraction regression checks
screenshots/                                    ← images used above
```
