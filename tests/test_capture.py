from app.capture import (
    CatalogItem, capture, clean_phrase, extract_quantity, make_anthropic_picker, sizes_in, split_message, strip_quantity,
)

CATALOG = [
    CatalogItem("SKU-BS-075", "Glucose biscuit 75 g, case of 96", "Biscuits", "biscuit,biskut,glucose,chhota,small,75", 340),
    CatalogItem("SKU-BS-200", "Glucose biscuit 200 g, case of 36", "Biscuits", "biscuit,biskut,glucose,bada,big,200", 120),
    CatalogItem("SKU-DT-1KG", "Detergent powder 1 kg, case of 20", "Home care", "detergent,washing powder,kapde ka powder,powder,1 kg,1 kilo", 85),
    CatalogItem("SKU-DT-500", "Detergent powder 500 g, case of 40", "Home care", "detergent,washing powder,powder,500,aadha kilo", 60),
    CatalogItem("SKU-TE-250", "Tea leaf 250 g, case of 40", "Tea", "tea,chai,chai patti,patti,250", 0),
    CatalogItem("SKU-TE-100", "Tea leaf 100 g, case of 100", "Tea", "tea,chai,chai patti,patti,100", 45),
]
MSG = "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti"


def by_text(lines):
    return {l.original_text: l for l in lines}


def test_brief_sample_message_maps_to_three_lines():
    lines = capture(MSG, CATALOG)
    assert [(l.qty, l.sku_code) for l in lines] == [
        (5, "SKU-BS-075"), (2, "SKU-DT-1KG"), (1, "SKU-TE-250"),
    ]
    assert all(l.unit == "case" and not l.unit_question for l in lines)


def test_each_line_keeps_original_words_and_two_alternatives():
    for l in capture(MSG, CATALOG):
        assert l.original_text and len(l.alternatives) == 2


def test_size_in_words_decides_between_variants():
    l = capture("2 peti washing powder 1 kilo", CATALOG)[0]
    assert l.sku_code == "SKU-DT-1KG" and l.confidence == "high"
    l = capture("2 peti washing powder 500 gram", CATALOG)[0]
    assert l.sku_code == "SKU-DT-500"


def test_ambiguous_item_is_low_confidence_not_a_silent_guess():
    l = capture("3 peti biscuit", CATALOG)[0]
    assert l.confidence == "low" and "choose" in l.note.lower()
    assert {l.sku_code, *[a.code for a in l.alternatives]} >= {"SKU-BS-075", "SKU-BS-200"}


def test_alias_beats_fuzzy_and_is_high_confidence():
    lines = capture("4 peti biscuit chhota", CATALOG, aliases={"biscuit chhota": "SKU-BS-200"})
    assert lines[0].sku_code == "SKU-BS-200" and lines[0].source == "alias" and lines[0].confidence == "high"


def test_unknown_item_has_no_sku_and_is_low():
    l = capture("2 peti zebra paint", CATALOG)[0]
    assert l.sku_code is None and l.confidence == "low" and l.source == "none"


def test_pieces_are_asked_not_converted():
    l = capture("48 pcs biscuit chhota", CATALOG)[0]
    assert l.unit == "piece" and l.unit_question and l.qty == 48


def test_missing_unit_is_asked():
    l = capture("biscuit chhota 5", CATALOG)[0]
    assert l.qty == 5 and l.unit is None and l.unit_question


def test_missing_quantity_is_asked():
    l = capture("biscuit chhota", CATALOG)[0]
    assert l.qty is None and l.unit_question


def test_hindi_number_words_with_unit():
    assert extract_quantity("do peti tea")[:2] == (2, "case")
    assert extract_quantity("das petti tea")[:2] == (10, "case")


def test_size_numbers_are_not_quantities():
    assert extract_quantity("chai patti 250 wala")[3].startswith("No quantity") or extract_quantity("chai patti 250 wala")[0] is None
    assert extract_quantity("2 peti washing powder 1 kilo")[:2] == (2, "case")


def test_greeting_only_segments_are_ignored():
    assert capture("bhaiya namaste", CATALOG) == []


def test_split_handles_newlines_bullets_and_aur():
    assert split_message("1. 5 peti tea\n2) 2 peti soap aur 3 peti salt") == ["5 peti tea", "2 peti soap", "3 peti salt"]


def test_sizes_and_cleaning_helpers():
    assert sizes_in("washing powder 1 kilo") == {1000.0}
    assert sizes_in("chai patti 250 wala") == {250.0}
    assert clean_phrase("Bhaiya, Biscuit Chhota!") == "biscuit chhota"
    assert strip_quantity("5 peti biscuit chhota") == "biscuit chhota"


def test_llm_tiebreaker_only_chooses_from_candidates():
    calls = []

    def fake(desc, cands):
        calls.append(desc)
        return "SKU-BS-200"

    l = capture("3 peti biscuit", CATALOG, llm=fake)[0]
    assert calls and l.sku_code == "SKU-BS-200" and l.source == "llm" and l.confidence != "high"


def test_llm_not_called_when_rules_are_confident():
    called = []
    capture("2 peti washing powder 1 kilo", CATALOG, llm=lambda d, c: called.append(1))
    assert not called


def test_llm_garbage_or_failure_falls_back_to_rules():
    bad = make_anthropic_picker("k", "m", post=lambda *a, **kw: (_ for _ in ()).throw(RuntimeError("down")))
    l = capture("3 peti biscuit", CATALOG, llm=bad)[0]
    assert l.source == "fuzzy" and l.confidence == "low"

    class R:
        def raise_for_status(self): pass
        def json(self): return {"content": [{"text": '{"code": "SKU-FAKE"}'}]}

    invented = make_anthropic_picker("k", "m", post=lambda *a, **kw: R())
    l = capture("3 peti biscuit", CATALOG, llm=invented)[0]
    assert l.source == "fuzzy"  # invented SKU ignored
