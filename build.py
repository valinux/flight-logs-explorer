#!/usr/bin/env python3
"""Build the self-contained flight_logs_explorer.html from the template + data."""
import json

with open("flights.json") as f:
    flights = json.load(f)
with open("contacts.json") as f:
    contacts = json.load(f)
with open("xref.json") as f:
    xref = json.load(f)

with open("explorer_template.html") as f:
    html = f.read()

flight_payload = json.dumps(flights, separators=(",", ":")).replace("</", "<\\/")
contact_payload = json.dumps(contacts, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")
xref_payload = json.dumps(xref, separators=(",", ":"), ensure_ascii=False).replace("</", "<\\/")

assert "/*__FLIGHT_DATA__*/" in html and "/*__CONTACT_DATA__*/" in html and "/*__XREF_DATA__*/" in html
html = html.replace("/*__FLIGHT_DATA__*/", flight_payload)
html = html.replace("/*__CONTACT_DATA__*/", contact_payload)
html = html.replace("/*__XREF_DATA__*/", xref_payload)

with open("flight_logs_explorer.html", "w") as f:
    f.write(html)

print(f"wrote flight_logs_explorer.html, {len(html)} bytes "
      f"({len(flights['rows'])} flights, {len(contacts)} contacts, {len(xref)} cross-references)")
