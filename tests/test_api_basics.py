from .conftest import login

SHARMA, GUPTA, DESK = "9000000001", "9000000002", "9000000100"


def test_health(client):
    assert client.get("/health").json() == {"status": "ok"}


def test_login_rejects_bad_phone_and_wrong_otp(client):
    assert client.post("/api/auth/login", json={"phone": "123", "otp": "123456"}).status_code == 422
    assert client.post("/api/auth/login", json={"phone": SHARMA, "otp": "000000"}).status_code == 401
    assert client.post("/api/auth/login", json={"phone": "9999999999", "otp": "123456"}).status_code == 401


def test_catalogue_requires_login_and_retailer_role(client):
    assert client.get("/api/catalogue").status_code == 401
    login(client, DESK)
    assert client.get("/api/catalogue").status_code == 403


def test_catalogue_shows_prices_ex_gst_and_stock_status(client):
    login(client, SHARMA)
    data = client.get("/api/catalogue").json()
    assert data["gst_note"] == "Prices exclude GST"
    by = {i["code"]: i for i in data["items"]}
    assert by["SKU-BS-075"]["case_price_paise"] == 77760  # 96 x 8.10
    assert by["SKU-TE-250"]["stock_status"] == "out"
    assert "Buy 10 cases, get 1 free" in by["SKU-BS-075"]["schemes"]


def test_prices_are_per_retailer(client):
    login(client, SHARMA)
    a = {i["code"]: i for i in client.get("/api/catalogue").json()["items"]}["SKU-DT-1KG"]["case_price_paise"]
    client.post("/api/auth/logout")
    login(client, GUPTA)  # 1% price tier
    b = {i["code"]: i for i in client.get("/api/catalogue").json()["items"]}["SKU-DT-1KG"]["case_price_paise"]
    assert b < a


def test_cart_price_applies_schemes_and_blocks_out_of_stock(client):
    login(client, SHARMA)
    r = client.post("/api/cart/price", json={"lines": [
        {"sku_code": "SKU-BS-075", "cases": 25}, {"sku_code": "SKU-TE-250", "cases": 1}]}).json()
    biscuit = next(l for l in r["lines"] if l["sku_code"] == "SKU-BS-075")
    assert biscuit["free_cases"] == 2
    assert r["placeable"] is False
    assert r["lines"][1]["issues"][0]["code"] == "out_of_stock"


def test_cart_price_validates_input(client):
    login(client, SHARMA)
    assert client.post("/api/cart/price", json={"lines": [{"sku_code": "SKU-BS-075", "cases": 0}]}).status_code == 422


def test_session_cookie_is_secure_when_configured(client, monkeypatch):
    monkeypatch.setattr("app.main.COOKIE_SECURE", True)
    r = client.post("/api/auth/login", json={"phone": SHARMA, "otp": "123456"})
    cookie = r.headers["set-cookie"].lower()
    assert "httponly" in cookie and "secure" in cookie and "samesite=lax" in cookie


def test_unregistered_number_gets_helpful_message_in_demo_mode_only(client, monkeypatch):
    r = client.post("/api/auth/login", json={"phone": "9876543210", "otp": "123456"})
    assert r.status_code == 401 and "quick-login" in r.json()["detail"]
    # a wrong code for an unknown number stays generic
    r = client.post("/api/auth/login", json={"phone": "9876543210", "otp": "000000"})
    assert r.status_code == 401 and "quick-login" not in r.json()["detail"]
    monkeypatch.setattr("app.main.DEMO_MODE", False)
    r = client.post("/api/auth/login", json={"phone": "9876543210", "otp": "123456"})
    assert "quick-login" not in r.json()["detail"]


def test_catalogue_has_hindi_names_and_structured_scheme_rules(client):
    login(client, SHARMA)
    items = {i["code"]: i for i in client.get("/api/catalogue").json()["items"]}
    assert all(i["name_hi"] for i in items.values())
    rule = items["SKU-BS-075"]["scheme_rules"][0]
    assert rule == {"type": "free_goods", "buy_qty": 10, "free_qty": 1, "min_cases": 0, "discount_pct": 0.0}
    assert items["SKU-DT-1KG"]["scheme_rules"][0]["discount_pct"] == 3.0
