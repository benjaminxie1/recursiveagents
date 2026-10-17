"""Codes & standards library for viewer/codes.html (GET /api/codes).

Joins three things the repo already has: the standards ids and requirement catalog in sim/plant.yaml, the
component taxonomy (which standards apply to which asset type), and the cached clause text in kb/refs/
(one JSON per web search, each result a title, url and snippet). Each cached result is filed under one
publication and, where its query or title names one, a clause number. A requirement links to a clause when one
of its standards ids covers that clause (CEC-691 covers 691.4 and 691.10; NFPA-70B covers all of NFPA 70B).
Read-only and stdlib + yaml, like the rest of serve.py.
"""
import json
import re
from pathlib import Path
from urllib.parse import urlsplit

REPO = Path(__file__).resolve().parents[1]
REFS = REPO / "kb" / "refs"

COLLECTIONS = [
    ("ca", "California codes"),
    ("national", "National standards"),
    ("federal", "Federal regulations and guidance"),
    ("oem", "OEM manuals"),
]

# id, short name, full title, collection, regex on query + title (first match wins), clause regex,
# and the plant.yaml standards ids that cite the whole publication or one clause prefix of it.
PUBLICATIONS = [
    ("CEC", "CEC / NEC", "California Electrical Code, Title 24 Part 3 (adopts NFPA 70, NEC)", "ca",
     r"\bNEC\b|\b69[01]\.\d", r"\b69[01]\.\d+(?:\([A-Z]\))?", {"CEC-690.41": "690.41", "CEC-690.11": "690.11", "CEC-691": "691"}),
    ("CFC", "CFC", "California Fire Code, Title 24 Part 9, Chapter 12 Energy Systems", "ca",
     r"fire code|\bCFC\b", r"\b120[57](?:\.\d+)*", {"CFC-1205": "1205", "CFC-1205.5.1": "1205.5.1", "CFC-1207": "1207"}),
    ("CCR8", "Cal/OSHA 8 CCR", "California Code of Regulations, Title 8, Electrical Safety Orders", "ca",
     r"Cal/OSHA|Title 8|\b2320\.\d", r"\b2320\.\d+", {"CCR8-2320.4": "2320.4"}),
    ("CPUC", "CPUC GO 167-C", "CPUC General Order 167-C: generating and energy storage maintenance and operation", "ca",
     r"CPUC|167-C", None, {}),
    ("NFPA70E", "NFPA 70E", "NFPA 70E: Standard for Electrical Safety in the Workplace", "national",
     r"70E", r"Article \d+", {"NFPA-70E-120": ""}),
    ("NFPA70B", "NFPA 70B", "NFPA 70B: Standard for Electrical Equipment Maintenance", "national", r"70B", None, {"NFPA-70B": ""}),
    ("NFPA855", "NFPA 855", "NFPA 855: Standard for the Installation of Stationary Energy Storage Systems", "national",
     r"NFPA 855", None, {"NFPA-855": ""}),
    ("UL1741", "UL 1741", "UL 1741: Inverters, Converters, Controllers and Interconnection System Equipment", "national",
     r"UL 1741", None, {"UL-1741": ""}),
    ("IEEE1547", "IEEE 1547", "IEEE 1547-2018: Interconnection and Interoperability of Distributed Energy Resources", "national",
     r"1547", r"[Cc]lause \d+", {"IEEE-1547": ""}),
    ("C57104", "IEEE C57.104", "IEEE C57.104-2019: Interpretation of Gases Generated in Mineral Oil-Immersed Transformers", "national",
     r"C57\.104|acetylene|\bDGA\b", None, {"IEEE-C57.104": ""}),
    ("C5791", "IEEE C57.91", "IEEE C57.91: Guide for Loading Mineral-Oil-Immersed Transformers", "national",
     r"C57\.91|top oil", None, {"IEEE-C57.91": ""}),
    ("IEC62446", "IEC 62446-1", "IEC 62446-1: PV systems, testing, documentation and maintenance", "national",
     r"62446", None, {}),
    ("IEC61724", "IEC 61724-1", "IEC 61724-1: Photovoltaic system performance monitoring", "national", r"61724", None, {}),
    ("OSHA1910", "OSHA 29 CFR 1910", "OSHA 29 CFR 1910 Subpart S and 1910.269: electrical safety and lockout/tagout", "federal",
     r"\bOSHA\b|1910\.", r"1910\.\d+(?:\([a-z0-9]+\))*", {}),
    ("OEMCB", "Combiner and connector manuals", "Combiner box and PV connector OEM manuals (SMA String-Combiner, Stäubli MC4)", "oem",
     r"String-Combiner|St(?:ä|a)ubli|MC4", None, {"OEM-CB-MANUAL": ""}),
    ("OEMINV", "Inverter manuals", "Inverter OEM installation and O&M manuals (SMA Sunny Central / Highpower / Tripower, Solis)", "oem",
     r"\bSMA\b|Solis|Sunny", None, {"OEM-INV-MANUAL": ""}),
    ("OEMMOD", "Module manuals", "PV module OEM installation and maintenance manuals (Canadian Solar, Risen)", "oem",
     r"Canadian Solar|Risen|module (?:maintenance|broken)", None, {"OEM-MODULE-MANUAL": ""}),
    ("GUIDE", "O&M guidance", "DOE FEMP, NREL, Sandia, EPA, Bureau of Reclamation and industry O&M guidance", "federal",
     r".", None, {}),
]


def _clause_key(pub_re, text):
    m = re.search(pub_re, text) if pub_re else None
    return m.group(0) if m else ""


def _covers(prefix, clause):
    return prefix == "" or clause == prefix or clause.startswith(prefix + ".") or clause.startswith(prefix + "(")


def _assets():
    from gridtwin.sim.plant import BLOCKS, BREAKERS, COMBINERS, INVERTERS

    return sorted(set(INVERTERS) | set(BREAKERS) | set(COMBINERS) | set(BLOCKS) | {"MET-1", "XFMR-1", "POI-1", "BESS-1"})


def build(refs_dir=REFS):
    import yaml

    from gridtwin.sim.plant import _applies

    doc = yaml.safe_load((REPO / "gridtwin" / "sim" / "plant.yaml").read_text())
    titles, taxonomy, reqs = doc.get("standards") or {}, doc.get("taxonomy") or {}, doc.get("requirements") or []
    assets = _assets()
    std_pub = {sid: (p[0], pre) for p in PUBLICATIONS for sid, pre in p[6].items()}

    pubs = {p[0]: {"id": p[0], "name": p[1], "title": p[2], "collection": p[3], "standards": sorted(p[6]), "clauses": []}
            for p in PUBLICATIONS}
    for path in sorted(Path(refs_dir).glob("*.json")):
        try:
            data = json.loads(path.read_text())
        except ValueError:
            continue
        for r in data.get("results") or []:
            text = f"{r.get('title', '')} {data.get('query', '')}"  # the title names the clause the source is about
            p = next(p for p in PUBLICATIONS if re.search(p[4], text, re.I))
            num = _clause_key(p[5], text)
            pub = pubs[p[0]]
            clause = next((c for c in pub["clauses"] if num and c["number"] == num), None)
            if clause is None:
                clause = {"id": f"{p[0]}:{num or len(pub['clauses']) + 1}", "number": num, "heading": r.get("title", ""),
                          "sources": [], "standards": [], "requirements": [], "assets": []}
                pub["clauses"].append(clause)
            clause["sources"].append({"title": r.get("title", ""), "url": r.get("url", ""), "snippet": r.get("snippet", ""),
                                      "domain": urlsplit(r.get("url", "")).netloc.removeprefix("www."),
                                      "query": data.get("query", ""), "fetched_at": r.get("fetched_at") or data.get("fetched_at"),
                                      "file": path.name})

    out_reqs = []
    for r in reqs:
        targets = [a for a in assets if _applies(r["applies_to"], a)]
        out_reqs.append({**r, "assets": targets, "component": (taxonomy.get(r["applies_to"]) or {}).get("component"),
                         "component_code": (taxonomy.get(r["applies_to"]) or {}).get("component_code")})
    for pub in pubs.values():
        for c in pub["clauses"]:
            sids = [s for s in pub["standards"] if _covers(std_pub[s][1], c["number"])]
            c["standards"] = sids
            c["requirements"] = [r["id"] for r in out_reqs if set(r["standards"]) & set(sids)]
            c["assets"] = sorted({a for pre, t in taxonomy.items() if set(t.get("standards") or []) & set(sids)
                                  for a in assets if _applies(pre, a)})
        pub["clauses"].sort(key=lambda c: (c["number"] == "", [int(x) if x.isdigit() else x for x in re.findall(r"\d+|\D+", c["number"])]))
        pub["requirements"] = sorted({r for c in pub["clauses"] for r in c["requirements"]} |
                                     {r["id"] for r in out_reqs if set(r["standards"]) & set(pub["standards"])})

    standards = [{"id": sid, "title": t, "publication": std_pub.get(sid, ("", ""))[0], "clause": std_pub.get(sid, ("", ""))[1],
                  "assets": sorted({a for pre, tx in taxonomy.items() if sid in (tx.get("standards") or []) for a in assets if _applies(pre, a)})}
                 for sid, t in titles.items()]
    return {"collections": [{"id": k, "name": n} for k, n in COLLECTIONS],
            "publications": [p for p in pubs.values() if p["clauses"] or p["standards"]],
            "standards": standards, "requirements": out_reqs, "assets": assets}


if __name__ == "__main__":
    d = build()
    for p in d["publications"]:
        print(f"{p['id']:9} {len(p['clauses']):2} clauses  reqs={','.join(p['requirements'])}")
        for c in p["clauses"]:
            print(f"   {c['number'] or '-':14} {len(c['sources'])} {','.join(c['requirements'])}  {c['heading'][:60]}")
