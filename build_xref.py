#!/usr/bin/env python3
"""Cross-reference flight-log passengers with the contact-directory entries.

Matching tiers (best tier wins per passenger/contact pair):
  exact    - normalized first+last name identical (either order)
  nickname - same last name, first names related (nickname table or prefix >=3)
  initial  - same last name, same first initial (incl. abbreviated contacts)
  weak     - single-token contact name matching a unique/rare passenger first
             or last name (handwritten lists, abbreviations)

Output: xref.json
"""
import json
import re
import unicodedata

from contact_data import ROOT, phone_text

NICKNAME_GROUPS = [
    {"bill", "billy", "will", "william", "willie", "wills"},
    {"bob", "bobby", "rob", "robert", "robbie", "bert"},
    {"dick", "rich", "richard", "rick", "ricky", "ricardo"},
    {"ted", "teddy", "ed", "eddie", "edward", "edwin", "theo", "theodore"},
    {"jim", "jimmy", "james", "jamie", "jem"},
    {"joe", "joey", "joseph", "jo"},
    {"mike", "micky", "michael", "mick", "michel"},
    {"tony", "anthony", "antony"},
    {"chris", "christopher", "christophe", "christine", "christina", "kris", "tina"},
    {"alex", "alexander", "alexandra", "alexis", "sandy", "sasha"},
    {"dave", "davey", "david"},
    {"frank", "francis", "franklin", "fran"},
    {"fred", "freddy", "freddie", "frederic", "frederick", "fredric"},
    {"hank", "henry", "harry", "harold", "hal"},
    {"jack", "john", "johnny", "jon", "jonathan", "ian"},
    {"kate", "katie", "kathy", "katherine", "kathryn", "catherine", "cathy", "kay"},
    {"liz", "lizzy", "beth", "betty", "betsy", "elizabeth", "lisa", "elisa"},
    {"maggie", "meg", "megan", "margaret", "margot", "peggy", "peg"},
    {"matt", "matthew", "matty"},
    {"max", "maxwell"},
    {"nick", "nicky", "nicholas", "nicolas"},
    {"pat", "patty", "patrick", "patricia", "tricia"},
    {"phil", "phillip", "philip", "philippe"},
    {"ron", "ronnie", "ronald"},
    {"sam", "sammy", "samuel", "samantha"},
    {"steve", "steven", "stephen", "stefan", "stephane"},
    {"tom", "tommy", "thomas"},
    {"charlie", "charles", "chuck", "chas", "karl", "carl"},
    {"larry", "laurence", "lawrence", "lars"},
    {"terry", "terence", "teresa", "tess"},
    {"cindy", "cynthia", "cyndi"},
    {"debbie", "deb", "deborah", "debra"},
    {"sue", "susie", "susan", "susanna", "suzanne", "suzy"},
    {"becky", "rebecca"},
    {"mandy", "amanda"},
    {"ronnie", "veronica"},
    {"gerry", "jerry", "gerald", "geraldine", "jeremy"},
    {"al", "albert", "alan", "allan", "allen"},
    {"dan", "danny", "daniel", "danielle"},
    {"don", "donald", "donnie"},
    {"doug", "douglas"},
    {"greg", "gregory"},
    {"jeff", "jeffrey", "geoffrey", "geoff"},
    {"ken", "kenny", "kenneth"},
    {"leo", "leon", "leonard", "leonardo"},
    {"ray", "raymond"},
    {"vic", "vicky", "victor", "victoria"},
    {"vince", "vincent"},
    {"walt", "walter", "wally"},
    {"andy", "andrew", "drew"},
    {"ben", "benny", "benjamin"},
    {"marty", "martin"},
    {"pete", "peter"},
    {"stu", "stuart", "stewart"},
    {"nat", "nathan", "nathaniel", "natalie"},
    {"gabe", "gabriel", "gabrielle"},
    {"rudy", "rudolph", "rudolf"},
    {"carol", "caroline", "carolyn", "carrie"},
    {"jen", "jenny", "jennifer"},
    {"jess", "jesse", "jessica"},
    {"kim", "kimberly"},
    {"laurie", "laura", "lauren", "lori"},
    {"michelle", "shelly", "michele"},
    {"nicole", "nikki"},
    {"stephanie", "steph"},
    {"val", "valerie"},
    {"ann", "anne", "annie", "anna", "annette"},
    {"barbara", "barb", "barbie"},
    {"diana", "diane", "di"},
    {"ellen", "ellie", "eleanor", "nancy"},
    {"jane", "janet", "janice", "jan"},
    {"joan", "joanna", "joanne", "johanna"},
    {"julia", "julie", "jules"},
    {"marie", "maria", "mary", "molly", "polly"},
    {"pam", "pamela"},
    {"rachel", "rae"},
    {"sarah", "sara", "sally", "sadie"},
    {"emily", "em", "emma", "emmy"},
    {"gwen", "gwendolyn"},
    {"judy", "judith"},
    {"norma", "norman", "norm"},
    {"toby", "tobias"},
]
NICK2GROUP = {}
for i, g in enumerate(NICKNAME_GROUPS):
    for n in g:
        NICK2GROUP.setdefault(n, set()).add(i)

JUNK_NAMES = {
    "", "?", "??", "no records", "unknown", "1 female", "2 females", "1 male",
    "2 males", "female", "male", "n a", "na", "x", "name withheld", "minor",
    "redacted", "pilot", "crew", "staff", "child", "baby", "infant",
}


def norm(s):
    s = unicodedata.normalize("NFKD", s)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = s.lower()
    s = re.sub(r"[^a-z ]", " ", s)
    return re.sub(r"\s+", " ", s).strip()


def firsts_related(a, b):
    """Nickname-group match, or one is a >=3-char prefix of the other."""
    if not a or not b:
        return False
    if a == b:
        return True
    ga, gb = NICK2GROUP.get(a, set()), NICK2GROUP.get(b, set())
    if ga & gb:
        return True
    if len(a) >= 3 and len(b) >= 3 and (a.startswith(b) or b.startswith(a)):
        return True
    return False


def parse_person(part):
    """Return list of (first, last) interpretations for one name part."""
    part = part.strip()
    if not part:
        return []
    out = []
    if "," in part:
        last_raw, _, first_raw = part.partition(",")
        last, first = norm(last_raw), norm(first_raw)
        ftoks = first.split()
        first = ftoks[0] if ftoks else ""
        if last:
            out.append((first, last))
        return out
    toks = norm(part).split()
    if len(toks) == 1:
        out.append(("", toks[0]))  # single token: could be first OR last
    elif len(toks) >= 2:
        out.append((toks[0], toks[-1]))                     # First ... Last
        if len(toks) >= 3:
            out.append((toks[0], " ".join(toks[-2:])))      # First ... Last1 Last2
            out.append((toks[0], " ".join(toks[1:])))       # First Last-rest
    return out


def split_parts(name):
    """Split couple/joint entries into person parts."""
    name = re.sub(r"\([^)]*\)", " ", name)          # drop parenthetical notes
    name = re.sub(r"(?i)\b(dr|mr|mrs|ms|prof|sir|lady|lord|hon)\b\.?", " ", name)
    parts = re.split(r"\s+&\s+|\s+and\s+|\s*/\s*", name)
    return [p.strip(" .,;:") for p in parts if p.strip(" .,;:")]


def build_matches(flights, contacts):
    h = flights["headers"]
    i_first, i_last = h.index("First Name"), h.index("Last Name")

    # ---- passengers ----
    pax = {}  # key -> {name, first, last, flights}
    for r in flights["rows"]:
        first, last = str(r[i_first]).strip(), str(r[i_last]).strip()
        nf, nl = norm(first), norm(last)
        if not nl or nl in JUNK_NAMES or f"{nf} {nl}".strip() in JUNK_NAMES:
            continue
        if not re.search(r"[a-z]", nl):
            continue
        # clean trailing single-letter tokens ("Junkerman n" -> "junkerman")
        ntoks = nl.split()
        while len(ntoks) > 1 and len(ntoks[-1]) == 1:
            ntoks.pop()
        nl = " ".join(ntoks)
        if len(nl) < 2:
            continue
        key = (nf.split()[0] if nf else "", nl)
        e = pax.setdefault(key, {
            "name": f"{first} {last}".strip() or last,
            "first": key[0], "last": nl, "flights": 0,
        })
        e["flights"] += 1
    passengers = list(pax.values())

    # indexes
    by_last = {}
    for p in passengers:
        by_last.setdefault(p["last"], []).append(p)
    first_count = {}
    for p in passengers:
        if p["first"]:
            first_count.setdefault(p["first"], set()).add(p["last"])
    last_count = {l: len(v) for l, v in by_last.items()}

    # ---- match ----
    matches = {}  # (pax_key, contact_id) -> match dict

    def add(p, c, mtype, score):
        key = ((p["first"], p["last"]), c["id"])
        if key not in matches or matches[key]["score"] < score:
            matches[key] = {
                "passenger": p["name"], "flights": p["flights"],
                "contact_id": c["id"], "contact_name": c["name"],
                "pages": c["pages"], "ref": c["ref"],
                "phones": [phone_text(phone) for phone in c["phones"]], "emails": c["emails"],
                "address": c["address"], "status": c["status"],
                "source_url": c["source_url"], "review_flags": c["review_flags"],
                "match": mtype, "score": score,
            }

    for c in contacts:
        if c["block_type"] != "Entry / source block":
            continue
        cname = c["name"]
        if not cname or len(norm(cname)) < 2:
            continue
        single_token = len(norm(cname).split()) == 1
        for part in split_parts(cname):
            for first, last in parse_person(part):
                if first and last:
                    # exact last-name match required for all strong tiers
                    for p in by_last.get(last, []):
                        pf = p["first"]
                        if pf == first:
                            add(p, c, "exact", 100)
                        elif firsts_related(pf, first):
                            add(p, c, "nickname", 85)
                        elif pf and first and pf[0] == first[0]:
                            add(p, c, "initial", 70)
                elif last:
                    tok = last
                    if single_token:
                        # whole entry is one word (handwritten lists etc.)
                        if tok in first_count and len(first_count[tok]) <= 2:
                            for p in passengers:
                                if p["first"] == tok:
                                    add(p, c, "weak: first name only", 45)
                        else:
                            cands = [p for p in passengers
                                     if p["first"] and firsts_related(p["first"], tok)]
                            if 0 < len({p["last"] for p in cands}) <= 2:
                                for p in cands:
                                    add(p, c, "weak: first name only", 40)
                        if tok in by_last and last_count.get(tok, 99) <= 2:
                            for p in by_last[tok]:
                                add(p, c, "weak: last name only", 35)
                    else:
                        # person inside a couple/joint entry: only exact
                        # first-name equality, and only for rare first names
                        if tok in first_count and len(first_count[tok]) <= 2:
                            for p in passengers:
                                if p["first"] == tok:
                                    add(p, c, "weak: first name only", 40)

    return sorted(matches.values(), key=lambda m: (-m["score"], -m["flights"], m["passenger"]))


def main():
    with open(ROOT / "flights.json", encoding="utf-8") as f:
        flights = json.load(f)
    with open(ROOT / "contacts.json", encoding="utf-8") as f:
        contacts = json.load(f)
    out = build_matches(flights, contacts)
    from parse_contacts import write_exports
    write_exports(contacts, out)

    from collections import Counter
    tiers = Counter(m["match"] for m in out)
    print("matches:", len(out), dict(tiers))
    print("distinct passengers matched:", len({m['passenger'] for m in out}))
    print("distinct contacts matched:", len({m['contact_id'] for m in out}))
    for m in out[:25]:
        print(f"  [{m['match']:22}] {m['passenger']:25} <-> {m['contact_name'][:35]:35} p.{m['pages']} ({m['flights']} flights)")


if __name__ == "__main__":
    main()
