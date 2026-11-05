import csv
import io

from .conftest import login

SHARMA, GUPTA, DESK = "9000000001", "9000000002", "9000000100"


def retailer_places(client, phone, lines):
    login(client, phone)
    r = client.post("/api/orders", json={"lines": lines})
    assert r.status_code == 201, r.text
    client.post("/api/auth/logout")
    return r.json()


def test_desk_endpoints_blocked_for_retailers(client):
    login(client, SHARMA)
    assert client.get("/api/desk/orders").status_code == 403
    assert client.post("/api/desk/export/tally").status_code == 403


def test_queue_filters_by_status_and_hides_drafts(client):
    login(client, SHARMA)
    client.post("/api/capture", json={"text": "5 peti biscuit chhota"})
    client.post("/api/auth/logout")
    login(client, DESK)
    rows = client.get("/api/desk/orders").json()
    assert rows and all(r["status"] != "draft_ai" for r in rows)
    assert all(r["status"] == "placed" for r in client.get("/api/desk/orders?status=placed").json())


def test_hold_must_be_released_before_confirm(client):
    o = retailer_places(client, GUPTA, [{"sku_code": "SKU-BS-075", "cases": 20}])
    assert o["status"] == "on_hold"
    login(client, DESK)
    assert client.post(f"/api/desk/orders/{o['id']}/confirm").status_code == 409
    assert client.post(f"/api/desk/orders/{o['id']}/release", json={"note": "Paid by cheque"}).status_code == 200
    r = client.post(f"/api/desk/orders/{o['id']}/confirm")
    assert r.status_code == 200 and r.json()["status"] == "confirmed"


def test_confirm_deducts_stock_including_free_cases_and_raises_outstanding(client):
    o = retailer_places(client, SHARMA, [{"sku_code": "SKU-BS-075", "cases": 10}])  # 10 + 1 free
    login(client, DESK)
    before = client.get(f"/api/desk/orders/{o['id']}").json()["retailer"]["outstanding_paise"]
    d = client.post(f"/api/desk/orders/{o['id']}/confirm").json()
    assert d["lines"][0]["stock_cases"] == 340 - 11
    assert d["retailer"]["outstanding_paise"] == before + o["total_paise"]
    assert client.post(f"/api/desk/orders/{o['id']}/confirm").status_code == 409  # not twice


def test_confirm_fails_if_stock_vanished(client):
    a = retailer_places(client, SHARMA, [{"sku_code": "SKU-DW-500", "cases": 25}])
    b = retailer_places(client, SHARMA, [{"sku_code": "SKU-DW-500", "cases": 25}])  # stock is 30: each fits alone, not both
    login(client, DESK)
    assert client.post(f"/api/desk/orders/{a['id']}/confirm").status_code == 200
    r = client.post(f"/api/desk/orders/{b['id']}/confirm")
    assert r.status_code == 409 and "Stock has changed" in r.text


def test_desk_correction_teaches_alias_used_next_time(client):
    login(client, SHARMA)
    d = client.post("/api/capture", json={"text": "3 peti kapda sabun"}).json()
    line = d["lines"][0]
    client.post(f"/api/capture/{d['id']}/confirm", json={"lines": [
        {"line_id": line["line_id"], "sku_code": "SKU-DW-500", "qty": 3, "unit": "case"}]})
    client.post("/api/auth/logout")
    login(client, DESK)
    oid = next(r["id"] for r in client.get("/api/desk/orders").json() if r["source"] == "ai_text")
    det = client.get(f"/api/desk/orders/{oid}").json()
    ln = det["lines"][0]
    r = client.put(f"/api/desk/orders/{oid}/lines", json={"lines": [
        {"line_id": ln["line_id"], "sku_code": "SKU-DT-500", "cases": 3}]})
    assert r.status_code == 200
    client.post("/api/auth/logout")
    login(client, SHARMA)
    again = client.post("/api/capture", json={"text": "2 peti kapda sabun"}).json()["lines"][0]
    assert again["sku_code"] == "SKU-DT-500" and again["confidence"] == "high"


def test_edit_blocked_after_confirm_and_validates(client):
    o = retailer_places(client, SHARMA, [{"sku_code": "SKU-BS-200", "cases": 2}])
    login(client, DESK)
    assert client.put(f"/api/desk/orders/{o['id']}/lines", json={"lines": [
        {"sku_code": "SKU-TE-250", "cases": 1}]}).status_code == 422  # out of stock
    client.post(f"/api/desk/orders/{o['id']}/confirm")
    assert client.put(f"/api/desk/orders/{o['id']}/lines", json={"lines": [
        {"sku_code": "SKU-BS-200", "cases": 1}]}).status_code == 409


def test_tally_export_csv_and_marks_exported(client):
    o = retailer_places(client, SHARMA, [{"sku_code": "SKU-DT-1KG", "cases": 5}])
    login(client, DESK)
    client.post(f"/api/desk/orders/{o['id']}/confirm")
    r = client.post("/api/desk/export/tally")
    assert r.status_code == 200 and "text/csv" in r.headers["content-type"]
    rows = list(csv.DictReader(io.StringIO(r.text)))
    mine = [x for x in rows if x["Voucher No"] == f"ORD-{o['id']:06d}"]
    assert mine and mine[0]["Item Code"] == "SKU-DT-1KG" and mine[0]["Qty (cases)"] == "5"
    assert float(mine[0]["Discount"]) > 0  # 3% slab at 5 cases
    assert client.get(f"/api/desk/orders/{o['id']}").json()["status"] == "exported"
