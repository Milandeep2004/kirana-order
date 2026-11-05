import pytest

from app.pricing import (
    CartLine, SchemeInfo, SkuInfo, adjust_price, check_credit, free_cases_for, max_orderable, price_cart,
)

BISCUIT = SkuInfo("SKU-BS-075", "Glucose biscuit 75 g, case of 96", 77760, 340)
DETERGENT = SkuInfo("SKU-DT-1KG", "Detergent powder 1 kg, case of 20", 203000, 85)
TEA = SkuInfo("SKU-TE-250", "Tea leaf 250 g, case of 40", 472000, 0)
SKUS = {s.code: s for s in (BISCUIT, DETERGENT, TEA)}
SCHEMES = {
    "SKU-BS-075": [SchemeInfo("free_goods", buy_qty=10, free_qty=1)],
    "SKU-DT-1KG": [SchemeInfo("slab", min_cases=5, discount_bp=300)],
}


def price(code, cases, skus=SKUS):
    return price_cart([CartLine(code, cases)], skus, SCHEMES).lines[0]


@pytest.mark.parametrize("cases,free", [(9, 0), (10, 1), (11, 1), (19, 1), (20, 2), (25, 2), (30, 3)])
def test_free_goods_per_full_set(cases, free):
    assert price("SKU-BS-075", cases).free_cases == free


def test_free_goods_are_not_charged():
    l = price("SKU-BS-075", 10)
    assert l.gross_paise == 10 * 77760
    assert l.net_paise == l.gross_paise
    assert l.free_cases == 1


@pytest.mark.parametrize("cases,discount", [(4, 0), (5, 30450), (6, 36540)])
def test_slab_boundary(cases, discount):
    # 5 cases * 2030.00 = 10150.00 -> 3% = 304.50 = 30450 paise
    assert price("SKU-DT-1KG", cases).discount_paise == discount


def test_slab_net_amount():
    l = price("SKU-DT-1KG", 5)
    assert l.net_paise == 5 * 203000 - 30450


def test_slab_rounds_half_up_to_paise():
    sku = SkuInfo("X", "x", 1050, 100)  # 3 * 1050 = 3150 -> 3% = 94.5 -> 95
    r = price_cart([CartLine("X", 3)], {"X": sku}, {"X": [SchemeInfo("slab", min_cases=1, discount_bp=300)]})
    assert r.lines[0].discount_paise == 95


def test_out_of_stock_blocks():
    l = price("SKU-TE-250", 1)
    assert l.blocking and l.issues[0].code == "out_of_stock"
    assert not price_cart([CartLine("SKU-TE-250", 1)], SKUS, SCHEMES).placeable


def test_free_goods_count_against_stock():
    tight = {"SKU-BS-075": SkuInfo("SKU-BS-075", "b", 77760, 21)}
    l = price("SKU-BS-075", 20, tight)  # 20 + 2 free = 22 > 21
    assert l.issues and l.issues[0].code == "insufficient_stock"
    assert l.issues[0].max_orderable == 19  # 19 + 1 free = 20 <= 21


def test_exactly_at_stock_is_ok():
    tight = {"SKU-BS-075": SkuInfo("SKU-BS-075", "b", 77760, 22)}
    assert not price("SKU-BS-075", 20, tight).blocking


def test_max_orderable_without_scheme():
    assert max_orderable(7, []) == 7
    assert max_orderable(0, []) == 0


def test_max_orderable_with_free_goods():
    assert max_orderable(11, [SchemeInfo("free_goods", buy_qty=10, free_qty=1)]) == 10


def test_duplicate_lines_merge_before_schemes():
    r = price_cart([CartLine("SKU-BS-075", 6), CartLine("SKU-BS-075", 4)], SKUS, SCHEMES)
    assert len(r.lines) == 1 and r.lines[0].cases == 10 and r.lines[0].free_cases == 1


def test_invalid_and_unknown():
    r = price_cart([CartLine("SKU-BS-075", 0), CartLine("NOPE", 2)], SKUS, SCHEMES)
    codes = {i.code for l in r.lines for i in l.issues}
    assert codes == {"invalid_qty", "unknown_sku"} and not r.placeable


def test_cart_total_and_empty_cart():
    r = price_cart([CartLine("SKU-BS-075", 5), CartLine("SKU-DT-1KG", 5)], SKUS, SCHEMES)
    assert r.total_paise == 5 * 77760 + (5 * 203000 - 30450)
    assert not price_cart([], SKUS, SCHEMES).placeable


def test_stacked_schemes_on_one_sku():
    both = {"S": [SchemeInfo("free_goods", buy_qty=10, free_qty=1), SchemeInfo("slab", min_cases=10, discount_bp=100)]}
    l = price_cart([CartLine("S", 10)], {"S": SkuInfo("S", "s", 10000, 50)}, both).lines[0]
    assert l.free_cases == 1 and l.discount_paise == 1000


def test_free_cases_helper_ignores_slab():
    assert free_cases_for(50, SchemeInfo("slab", min_cases=5, discount_bp=300)) == 0


def test_credit_check():
    assert check_credit(100000, 40000, 60000) is None  # exactly at limit is fine
    assert "exceeded by ₹0.01" in check_credit(100000, 40000, 60001)


def test_price_tier():
    assert adjust_price(77760, 0) == 77760
    assert adjust_price(77760, 150) == 77760 - 1166  # 1.5% = 1166.4 -> 1166
