import json

from scripts import demo_check


def fake_get(responses):
    return lambda base, path: responses.get(path, (200, b"ok"))


def run(responses):
    demo_check.results.clear()
    demo_check.check_server("http://x", get=fake_get(responses))
    return {name: status for status, name, _ in demo_check.results}


RUNS = (200, json.dumps([{"id": "demo-fixture"}]).encode())


def test_all_pass_with_healthy_server():
    out = run({"/api/runs": RUNS, "/api/viewpoints": (200, b'[{"id": "a"}]'), "/runs/demo-fixture/truth.jsonl": (404, b"")})
    assert "FAIL" not in out.values()
    assert out["truth.jsonl is refused"] == "PASS"


def test_flags_leaked_states_served_truth_and_dead_page():
    out = run({"/api/runs": RUNS, "/api/viewpoints": (200, b'[{"id": "a", "states": ["x"]}]'),
               "/wall.html": (404, b""), "/runs/demo-fixture/truth.jsonl": (200, b"{}")})
    assert out["/api/viewpoints hides photo states"] == "FAIL"
    assert out["truth.jsonl is refused"] == "FAIL"
    assert out["page /wall.html"] == "FAIL"


def test_scenarios_from_demo_md():
    assert "s06_stale_scada" in demo_check.demo_scenarios()


def test_model_check_skips_without_key(monkeypatch):
    monkeypatch.delenv("CRUSOE_API_KEY", raising=False)
    demo_check.results.clear()
    demo_check.check_models()
    assert demo_check.results[0][0] == "SKIP"


def test_e2e_check_needs_the_twin_registration(tmp_path):
    demo_check.results.clear()
    demo_check.check_e2e(repo=tmp_path, home=tmp_path)
    out = {name: status for status, name, _ in demo_check.results}
    assert out["twin registration _dev.registration.json"] == "FAIL"
    reg = tmp_path / "assets" / "splats" / "_dev.registration.json"
    reg.parent.mkdir(parents=True)
    reg.write_text(json.dumps({"views": {"inv3_display": {"pose": [0, 0, 0]}}}))
    demo_check.results.clear()
    demo_check.check_e2e(repo=tmp_path, home=tmp_path)
    assert ("PASS", "twin registration _dev.registration.json", "1 stations") in demo_check.results


def test_e2e_check_flags_missing_numpy(monkeypatch, tmp_path):
    monkeypatch.setattr(demo_check.importlib.util, "find_spec", lambda name: None)
    demo_check.results.clear()
    demo_check.check_e2e(repo=tmp_path, home=tmp_path)
    assert ("FAIL", "e2e demo imports numpy", "pip install -r requirements.txt") in demo_check.results
