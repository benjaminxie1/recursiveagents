import json

import pytest

from gridtwin import kb as kbmod
from gridtwin.kb import KB, build_plant_profile, normalize_url, snapshot, tokenize


class FakeLog:
    def __init__(self):
        self.events = []

    def emit(self, actor, type, data):
        e = {"seq": len(self.events) + 1, "actor": actor, "type": type, "data": data}
        self.events.append(e)
        return e

    def of(self, op):
        return [e for e in self.events if e["type"] == "lesson" and e["data"]["op"] == op]


PROFILE = """# Plant profile

## Inverters

INV-1, INV-2, INV-3 and INV-4. Points: ac_kw, heatsink_c, fan_rpm, status. Controls: restart, set_curtail_pct.

## Battery BESS-1

2 MW / 4 MWh battery. Points: soc_pct, p_kw, max_cell_c, rack_alarm. Control: set_dispatch_kw.

## Transformer XFMR-1

34.5/115 kV step-up. Points: oil_top_c, dga_h2_ppm, buchholz.
"""


@pytest.fixture
def kbdir(tmp_path):
    d = tmp_path / "kb"
    (d / "plant").mkdir(parents=True)
    (d / "plant" / "profile.md").write_text(PROFILE)
    (d / "refs").mkdir()
    (d / "lessons").mkdir()
    (d / "lessons" / ".gitkeep").write_text("")
    return d


def lesson(run_id, symptoms, fix, verdict_seqs=(), outcome="DONE"):
    return {"run_id": run_id, "symptoms": list(symptoms), "fix": fix, "steps": [fix],
            "outcome": outcome, "verdict_seqs": list(verdict_seqs), "checks": []}


SYM = ["INV-1 heatsink_c rising past normal", "INV-1 status derated near noon", "fan_rpm reads 0"]


def test_tokenize_keeps_asset_tokens_and_parts():
    toks = tokenize("Trip on 52-I4, INV-3 and POI-1; CB-2.3 ac_kw.")
    for t in ("52-i4", "52", "i4", "inv-3", "inv", "3", "poi-1", "poi", "cb-2.3", "cb", "ac_kw", "kw", "trip"):
        assert t in toks
    assert "and" not in toks


def test_bm25_ranks_relevant_doc_first(kbdir):
    k = KB(kbdir)
    k.add_ref("battery cell temperature limits", [
        {"url": "https://example.com/bess-manual", "title": "BESS manual", "snippet": "Rack temperature and cell limits."},
    ])
    k.add_ref("inverter manual", [
        {"url": "https://example.com/inv", "title": "Inverter manual", "snippet": "Fan replacement and heatsink cleaning."},
    ])
    hits = k.search("battery soc_pct state of charge")
    assert hits[0]["kind"] == "plant" and "BESS-1" in hits[0]["title"]
    hits = k.search("heatsink fan inverter manual", kinds=("refs",))
    assert hits[0]["id"] == "https://example.com/inv"
    assert all(h["kind"] == "refs" for h in hits)
    assert set(hits[0]) == {"kind", "id", "title", "text", "score"}
    hits = k.search("XFMR-1 dga")
    assert "XFMR-1" in hits[0]["title"]
    assert k.search("") == []


def test_write_lesson_fills_fields_and_unique_ids(kbdir):
    log = FakeLog()
    k = KB(kbdir, log=log)
    a = k.write_lesson(lesson("r1", ["comms ping fails on INV-3"], "reseat the card", [5]))
    b = k.write_lesson(lesson("r1", ["breaker 52-I4 open"], "close it", []))
    assert a["id"] == "L-r1" and b["id"] == "L-r1-2"
    assert a["status"] == "active" and a["verified"] is True and a["created_at"]
    assert b["verified"] is False
    assert json.loads((kbdir / "lessons" / "L-r1.json").read_text())["fix"] == "reseat the card"
    writes = log.of("write")
    assert [e["data"]["id"] for e in writes] == ["L-r1", "L-r1-2"]
    assert writes[0]["actor"] == "planner" and set(writes[0]["data"]) == {"id", "op", "text"}


def test_verified_contradicting_lesson_supersedes_and_files_update(kbdir):
    log = FakeLog()
    k = KB(kbdir, log=log)
    old = k.write_lesson(lesson("r1", SYM, "reset_alarm on INV-1", [3]))
    new = k.write_lesson(lesson("r2", SYM[:2] + ["fan stopped"], "curtail INV-1 then dispatch a tech", [9, 10]),
                         actor="supervisor")
    old_file = json.loads((kbdir / "lessons" / f"{old['id']}.json").read_text())
    new_file = json.loads((kbdir / "lessons" / f"{new['id']}.json").read_text())
    assert old_file["status"] == "superseded" and old_file["superseded_by"] == new["id"]
    assert new_file["status"] == "active" and new_file["supersedes"] == old["id"]
    sup = log.of("supersede")
    assert len(sup) == 1 and sup[0]["data"]["id"] == old["id"] and sup[0]["actor"] == "supervisor"
    assert new["id"] in sup[0]["data"]["text"]


def test_superseded_lessons_never_retrieved(kbdir):
    log = FakeLog()
    k = KB(kbdir, log=log)
    old = k.write_lesson(lesson("r1", SYM, "reset_alarm on INV-1", [3]))
    new = k.write_lesson(lesson("r2", SYM, "curtail INV-1 then dispatch a tech", [9]))
    assert [l["id"] for l in k.lessons()] == [new["id"]]
    assert {l["id"] for l in k.lessons(include_superseded=True)} == {old["id"], new["id"]}
    hits = k.search("INV-1 heatsink_c derated fan_rpm reset_alarm", k=10)
    ids = [h["id"] for h in hits]
    assert new["id"] in ids and old["id"] not in ids
    # a fresh instance reading the same folder agrees
    assert old["id"] not in [h["id"] for h in KB(kbdir).search("reset_alarm", k=10)]
    reads = log.of("read")
    assert [e["data"]["id"] for e in reads] == [new["id"]]


def test_unverified_lesson_never_supersedes(kbdir):
    log = FakeLog()
    k = KB(kbdir, log=log)
    old = k.write_lesson(lesson("r1", SYM, "curtail INV-1 then dispatch a tech", [3]))
    base = k.write_lesson(lesson("baseline", SYM, "reset_alarm on INV-1", []))
    assert base["verified"] is False and base["supersedes"] is None
    assert {l["id"] for l in k.lessons()} == {old["id"], base["id"]}
    assert json.loads((kbdir / "lessons" / f"{old['id']}.json").read_text())["status"] == "active"
    assert log.of("supersede") == []


def test_same_fix_or_unrelated_symptoms_do_not_supersede(kbdir):
    k = KB(kbdir, log=FakeLog())
    a = k.write_lesson(lesson("r1", SYM, "Curtail INV-1, then dispatch a tech.", [3]))
    b = k.write_lesson(lesson("r2", SYM, "curtail inv-1 then dispatch a tech", [4]))
    c = k.write_lesson(lesson("r3", ["breaker 52-I4 tripped", "export_kw down at POI-1"], "close 52-I4", [5]))
    assert {l["id"] for l in k.lessons()} == {a["id"], b["id"], c["id"]}


def test_search_emits_one_read_per_lesson_returned(kbdir):
    log = FakeLog()
    k = KB(kbdir, log=log)
    k.write_lesson(lesson("r1", ["breaker 52-I4 tripped"], "close 52-I4 after checking downstream", [2]))
    k.write_lesson(lesson("r2", ["INV-3 last_update_ts stale"], "dispatch comms tech", [4]))
    hits = k.search("52-I4 breaker tripped", k=1, actor="worker:ops")
    assert len(hits) == 1 and hits[0]["kind"] == "lessons"
    reads = log.of("read")
    assert len(reads) == 1 and reads[0]["actor"] == "worker:ops" and reads[0]["data"]["id"] == "L-r1"
    log.events.clear()
    k.search("battery soc_pct", kinds=("plant",))
    assert log.of("read") == []


def test_refs_find_and_add(kbdir):
    k = KB(kbdir)
    p = k.add_ref("Inverter fan manual", [{"url": "https://Docs.Example.com/Manual/", "title": "M", "snippet": "s"},
                                           {"title": "no url, dropped"}])
    assert p.parent == kbdir / "refs" and p.name.startswith("inverter-fan-manual-") and p.suffix == ".json"
    d = json.loads(p.read_text())
    assert d["query"] == "Inverter fan manual" and d["fetched_at"]
    assert len(d["results"]) == 1
    assert set(d["results"][0]) == {"url", "title", "snippet", "query", "fetched_at"}
    assert k.find_ref("docs.example.com/Manual")["title"] == "M"
    assert k.find_ref("http://DOCS.EXAMPLE.COM/Manual/")["title"] == "M"
    assert k.find_ref("docs.example.com/manual") is None  # path case is kept
    assert len(k.refs()) == 1
    assert normalize_url("HTTPS://A.B.com/x/") == "a.b.com/x"


def test_snapshot_copies_and_replaces(kbdir):
    k = KB(kbdir)
    k.add_ref("q", [{"url": "https://x.org/a", "title": "t", "snippet": "s"}])
    k.write_lesson(lesson("r1", ["s"], "f", [1]))
    snap = snapshot(kbdir, "v1")
    assert snap == kbdir / "snapshots" / "v1"
    assert (snap / "plant" / "profile.md").read_text() == PROFILE
    assert (snap / "lessons" / "L-r1.json").is_file()
    assert len(list((snap / "refs").glob("*.json"))) == 1
    (snap / "stale.txt").write_text("x")
    k.write_lesson(lesson("r2", ["t"], "g", [2]))
    snap2 = snapshot(kbdir, "v1")
    assert not (snap2 / "stale.txt").exists() and (snap2 / "lessons" / "L-r2.json").is_file()
    assert [l["id"] for l in KB(snap2).lessons()] == ["L-r1", "L-r2"]
    other = snapshot(kbdir, "v2", root=kbdir.parent / "elsewhere")
    assert other == kbdir.parent / "elsewhere" / "snapshots" / "v2" and (other / "plant" / "profile.md").exists()
    with pytest.raises(ValueError):
        snapshot(kbdir, "../x")


def test_build_plant_profile_generic(tmp_path):
    y = tmp_path / "plant.yaml"
    y.write_text("""
plant: {name: Test Farm, capacity_mwac: 5}
assets:
  - {id: INV-1, type: inverter, rated_kw: 1250, connects_to: 52-I1, points: [ac_kw, heatsink_c], controls: [restart]}
  - {id: 52-I1, type: breaker, rated_kv: 34.5, downstream: [BUS-1]}
  - {id: CB-1.1, type: combiner, strings: 12, inverter: INV-1, soiling: 0.8}
faults:
  - {id: secret-fault, asset: INV-1}
""")
    out = tmp_path / "out" / "profile.md"
    md = build_plant_profile(y, out)
    assert out.read_text() == md
    for s in ("INV-1", "inverter", "rated_kw=1250", "52-I1", "rated_kv=34.5", "CB-1.1", "strings=12",
              "ac_kw", "restart", "BUS-1", "## Topology", "## Assets"):
        assert s in md
    assert "secret-fault" not in md and "soiling" not in md
    with pytest.raises(FileNotFoundError):
        build_plant_profile(tmp_path / "missing.yaml", tmp_path / "x.md")


def test_build_plant_profile_grouped_layout(tmp_path):
    y = tmp_path / "plant.yaml"
    y.write_text("inverters:\n  INV-1: {rating_kw: 1250, breaker: 52-I1}\nbreakers:\n  - {id: 52-I1}\n"
                 "topology: 'INV-1 -> 52-I1 -> bus'\n")
    md = build_plant_profile(y)
    assert "| INV-1 | inverter |" in md and "| 52-I1 | breaker |" in md and "INV-1 -> 52-I1 -> bus" in md


def test_cli_snapshot_and_profile_missing(kbdir, tmp_path, capsys):
    assert kbmod.main(["snapshot", "cli", "--kb", str(kbdir)]) == 0
    assert (kbdir / "snapshots" / "cli" / "plant" / "profile.md").exists()
    assert kbmod.main(["profile", "--plant", str(tmp_path / "nope.yaml"), "--out", str(tmp_path / "p.md")]) == 1
    assert not (tmp_path / "p.md").exists()
