from .conftest import login

SHARMA, GUPTA, VERMA = "9000000001", "9000000002", "9000000003"


def place(client, lines):
    return client.post("/api/orders", json={"lines": lines})


def test_place_order_prices_on_server_and_is_placed(client):
    login(client, SHARMA)
    r = place(client, [{"sku_code": "SKU-BS-075", "cases": 10}])
    assert r.status_code == 201
    o = r.json()
    assert o["status"] == "placed" and o["lines"][0]["free_cases"] == 1
    assert o["total_paise"] == 10 * 77760


def test_out_of_stock_order_is_rejected_with_reason(client):
    login(client, SHARMA)
    r = place(client, [{"sku_code": "SKU-TE-250", "cases": 1}])
    assert r.status_code == 422
    assert r.json()["detail"]["cart"]["lines"][0]["issues"][0]["code"] == "out_of_stock"


def test_over_credit_order_goes_on_hold(client):
    login(client, GUPTA)  # limit 50,000, outstanding 42,000
    o = place(client, [{"sku_code": "SKU-BS-075", "cases": 20}]).json()  # ~15,500
    assert o["status"] == "on_hold" and "Credit limit exceeded" in o["credit_hold_reason"]


def test_empty_cart_rejected(client):
    login(client, SHARMA)
    assert place(client, []).status_code == 422


def test_history_is_private_to_the_retailer(client):
    login(client, SHARMA)
    mine = client.get("/api/orders").json()
    assert mine and all(o["id"] for o in mine)
    other_id = None
    client.post("/api/auth/logout")
    login(client, VERMA)
    other_id = client.get("/api/orders").json()[0]["id"]
    client.post("/api/auth/logout")
    login(client, SHARMA)
    assert client.get(f"/api/orders/{other_id}/repeat").status_code == 404


def test_repeat_order_reprices_current_cart(client):
    login(client, SHARMA)
    oid = client.get("/api/orders").json()[0]["id"]
    cart = client.get(f"/api/orders/{oid}/repeat").json()
    assert cart["lines"] and "total_paise" in cart
