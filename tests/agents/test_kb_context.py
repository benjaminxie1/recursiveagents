"""kb_context must show each cached ref's URL and enough text that a citation built from it passes."""
import re

from gridtwin.agents import dod
from gridtwin.agents.full import kb_context
from gridtwin.kb import KB

URL = "https://www.usbr.gov/power/data/fist/fist3_31/fist3-31.pdf#page=15"
# The quoted sentence starts past character 300, so a 300-character cut would hide it.
LEAD = ("Dissolved gas analysis is the most important diagnostic tool for oil-filled transformers. " * 4).strip()
QUOTE = "acetylene above a few ppm indicates high-energy arcing"
SNIPPET = f"{LEAD} Any {QUOTE}, and the transformer should be investigated before it is loaded further."


def _kb(tmp_path):
    kb = KB(tmp_path / "kb")
    kb.add_ref("acetylene dissolved gas transformer arcing",
               [{"url": URL, "title": "FIST 3-31 Transformer Diagnostics", "snippet": SNIPPET}])
    return kb


def test_refs_show_exact_url_and_text_past_300_chars(tmp_path):
    _, rtxt, _ = kb_context(_kb(tmp_path), "Transformer acetylene is rising. Find out why.", "planner")
    assert f"url: {URL}" in rtxt
    assert QUOTE in rtxt
    assert SNIPPET.index(QUOTE) > 300


def test_citation_built_from_kb_context_passes(tmp_path):
    kb = _kb(tmp_path)
    _, rtxt, _ = kb_context(kb, "Transformer acetylene is rising. Find out why.", "planner")
    url = re.search(r"url: (\S+)", rtxt).group(1)
    check = {"kind": "citation", "action": "page_human", "url": url, "quote": QUOTE}
    assert dod.check_citation(check, tmp_path / "kb")["pass"] is True


def test_no_refs_renders_none(tmp_path):
    _, rtxt, _ = kb_context(KB(tmp_path / "kb"), "Revenue is down, find out why.", "planner")
    assert rtxt == "(none)"
