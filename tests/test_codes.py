from gridtwin.codes import build


def test_codes_library_links_clauses_to_requirements_and_assets():
    d = build()
    pubs = {p["id"]: p for p in d["publications"]}
    veg = next(c for c in pubs["CFC"]["clauses"] if c["number"] == "1205.5.1")
    assert "REQ-PV-03" in veg["requirements"] and "B3" in veg["assets"]
    gfdi = next(c for c in pubs["CEC"]["clauses"] if c["number"].startswith("690.41"))
    assert {"REQ-INV-05", "REQ-CB-01"} <= set(gfdi["requirements"])
    assert sum(len(c["sources"]) for p in d["publications"] for c in p["clauses"]) >= 80
    assert all(r["assets"] for r in d["requirements"])
