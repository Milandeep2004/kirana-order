"""Measure the rule-based capture on a labelled set of Hinglish order messages (tests/eval/messages.json).

The messages were written by the author, not collected from real shopkeepers, so the figures show how the
rules behave on expected phrasing; they are not a field accuracy. Run:  python scripts/eval_capture.py
A gold line with sku null means "a person must look at this line" (unclear pack, unit or an item we do not sell).
"""
import json
from pathlib import Path

from app import capture as cap
from app.seed import SKUS

ROOT = Path(__file__).resolve().parent.parent
CATALOG = [cap.CatalogItem(code, name, category, kw, stock) for code, name, category, _ppc, _price, stock, kw, _c in SKUS]


def judge(text, gold):
    """Return a list of (outcome, detail) per gold line."""
    got = cap.capture(text, CATALOG)
    out = []
    if len(got) != len(gold):
        return [("lines_mismatch", f"{text!r}: expected {len(gold)} lines, got {len(got)}")] * max(len(gold), 1)
    for g, l in zip(gold, got):
        flagged = l.confidence != "high" or l.unit_question or l.sku_code is None
        if g["sku"] is None:  # must reach a person
            out.append(("must_flag_ok" if flagged else "MUST_FLAG_MISSED", f"{l.original_text!r} -> {l.sku_code} {l.confidence}"))
            continue
        unit_ok = (l.unit == g["unit"]) if g["unit"] else True
        right = l.sku_code == g["sku"] and l.qty == g["qty"] and unit_ok
        in_alts = g["sku"] in [a.code for a in l.alternatives]
        if right and not flagged:
            out.append(("correct_clean", ""))
        elif right:
            out.append(("correct_flagged", f"{l.original_text!r} {l.confidence} {l.note}"))
        elif flagged:
            out.append(("wrong_flagged" if not in_alts and l.sku_code != g["sku"] else "wrong_flagged_alt_ok", f"{l.original_text!r} -> {l.sku_code} x{l.qty} {l.unit} ({l.confidence})"))
        else:
            out.append(("WRONG_CONFIDENT", f"{l.original_text!r} -> {l.sku_code} x{l.qty} {l.unit} but expected {g['sku']} x{g['qty']} {g['unit']}"))
    return out


def run():
    data = json.loads((ROOT / "tests" / "eval" / "messages.json").read_text())
    counts, details = {}, {}
    for m in data:
        for kind, d in judge(m["text"], m["lines"]):
            counts[kind] = counts.get(kind, 0) + 1
            details.setdefault(kind, []).append(d)
    return len(data), sum(counts.values()), counts, details


if __name__ == "__main__":
    nmsg, nlines, counts, details = run()
    print(f"{nmsg} messages, {nlines} gold lines")
    for k in sorted(counts, key=lambda k: -counts[k]):
        print(f"  {k:24s} {counts[k]:3d}")
    for k in ("WRONG_CONFIDENT", "MUST_FLAG_MISSED", "lines_mismatch", "wrong_flagged", "wrong_flagged_alt_ok"):
        for d in details.get(k, []):
            print(f"[{k}] {d}")
