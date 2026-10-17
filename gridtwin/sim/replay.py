"""Readable step-through of a run: python -m gridtwin.sim.replay runs/<id>"""
import json
import sys

from gridtwin.sim.events import load


def _short(obj, n=160):
    s = json.dumps(obj, default=str)
    return s if len(s) <= n else s[: n - 3] + "..."


def render(event) -> str:
    d, t = event["data"], event["type"]
    head = f"{event['seq']:>4} {event['t_sim'] or '':16} {event['actor']:<16}"
    if t == "tool_call":
        body = f"→ {d['name']}({_short(d['args'], 100)})"
    elif t == "tool_result":
        body = f"  ← {_short(d['result'])}"
    elif t == "gate":
        body = f"GATE {d['route']} unsafe={d['unsafe']:.2f} {d['reason']}"
    elif t == "verdict":
        body = f"{'PASS' if d['pass'] else 'FAIL'} {d.get('check_id')} [{d.get('kind')}] {_short(d.get('evidence'), 100)}"
    elif t == "judge":
        m = d["metrics"]
        body = (f"JUDGE status={m['status']} expected={m['expected_status']} success={m['success']} "
                f"false_done={m['false_done']} resolved={m['resolved']} safety={m['safety_violations']}")
    elif t in ("goal", "claim", "final", "supervisor", "page", "lesson"):
        body = f"{t.upper()} " + _short(d, 200)
    else:
        body = f"{t} {_short(d)}"
    return f"{head} {body}"


def main(argv=None):
    argv = argv or sys.argv[1:]
    if not argv:
        sys.exit("usage: python -m gridtwin.sim.replay runs/<id>")
    for e in load(argv[0]):
        print(render(e))


if __name__ == "__main__":
    main()
