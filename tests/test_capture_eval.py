"""Guards the numbers reported in the TDD. The message set is the author's own (see scripts/eval_capture.py)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval_capture import run  # noqa: E402
from tests.test_capture import CATALOG, capture  # noqa: E402


def test_no_confident_wrong_line_and_nothing_that_needs_a_person_slips_through():
    nmsg, nlines, counts, details = run()
    assert nmsg >= 30 and nlines >= 40
    assert counts.get("WRONG_CONFIDENT", 0) == 0, details.get("WRONG_CONFIDENT")
    assert counts.get("MUST_FLAG_MISSED", 0) == 0, details.get("MUST_FLAG_MISSED")
    assert counts.get("lines_mismatch", 0) == 0, details.get("lines_mismatch")
    assert counts.get("correct_clean", 0) / nlines >= 0.7


def test_size_written_without_a_unit_picks_the_right_pack():
    assert capture("3 peti detergent 500", CATALOG)[0].sku_code == "SKU-DT-500"
    assert capture("chai patti 100 wala 4 peti", CATALOG)[0].sku_code == "SKU-TE-100"
    # ... but a plain quantity that is not a pack size is left alone
    l = capture("5 peti biscuit chhota", CATALOG)[0]
    assert l.qty == 5 and l.sku_code == "SKU-BS-075"


def test_dus_is_ten_and_a_trailing_size_is_not_mistaken_for_the_quantity():
    l = capture("dus peti biscuit chhota 75", CATALOG)[0]
    assert (l.qty, l.unit, l.sku_code) == (10, "case", "SKU-BS-075")
