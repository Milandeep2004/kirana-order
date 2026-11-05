from .conftest import login

SHARMA, GUPTA = "9000000001", "9000000002"
MSG = "bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo, chai patti 250 wala 1 peti"


def draft(client):
    r = client.post("/api/capture", json={"text": MSG})
    assert r.status_code == 201, r.text
    return r.json()


def test_capture_sample_message_maps_lines_and_flags_out_of_stock_tea(client):
    login(client, SHARMA)
    d = draft(client)
    assert d["status"] == "draft_ai" and len(d["lines"]) == 3
    by = {l["sku_code"]: l for l in d["lines"]}
    assert by["SKU-BS-075"]["qty"] == 5 and by["SKU-BS-075"]["confidence"] == "high"
    assert by["SKU-DT-1KG"]["qty"] == 2
    assert by["SKU-TE-250"]["out_of_stock"] is True
    assert all(len(l["alternatives"]) <= 2 and l["original_text"] for l in d["lines"])


def test_draft_is_not_an_order_until_confirmed(client):
    login(client, SHARMA)
    draft(client)
    assert all(o["status"] != "draft_ai" for o in client.get("/api/orders").json())


def test_confirm_requires_all_lines_and_blocks_out_of_stock(client):
    login(client, SHARMA)
    d = draft(client)
    ids = {l["sku_code"]: l["line_id"] for l in d["lines"]}
    partial = [{"line_id": ids["SKU-BS-075"], "sku_code": "SKU-BS-075", "qty": 5, "unit": "case"}]
    assert client.post(f"/api/capture/{d['id']}/confirm", json={"lines": partial}).status_code == 422
    allx = partial + [
        {"line_id": ids["SKU-DT-1KG"], "sku_code": "SKU-DT-1KG", "qty": 2, "unit": "case"},
        {"line_id": ids["SKU-TE-250"], "sku_code": "SKU-TE-250", "qty": 1, "unit": "case"}]
    r = client.post(f"/api/capture/{d['id']}/confirm", json={"lines": allx})
    assert r.status_code == 422  # tea is out of stock: still a draft
    assert client.get(f"/api/capture/{d['id']}").status_code == 200


def test_confirm_success_after_swapping_out_of_stock_item(client):
    login(client, SHARMA)
    d = draft(client)
    ids = {l["sku_code"]: l["line_id"] for l in d["lines"]}
    lines = [
        {"line_id": ids["SKU-BS-075"], "sku_code": "SKU-BS-075", "qty": 5, "unit": "case"},
        {"line_id": ids["SKU-DT-1KG"], "sku_code": "SKU-DT-1KG", "qty": 2, "unit": "case"},
        {"line_id": ids["SKU-TE-250"], "sku_code": "SKU-TE-100", "qty": 1, "unit": "case"}]
    r = client.post(f"/api/capture/{d['id']}/confirm", json={"lines": lines})
    assert r.status_code == 200 and r.json()["status"] == "placed" and r.json()["source"] == "ai_text"
    assert client.get(f"/api/capture/{d['id']}").status_code == 404  # no longer a draft


def test_pieces_must_be_whole_cases(client):
    login(client, SHARMA)
    d = client.post("/api/capture", json={"text": "50 biscuit chhota"}).json()
    lid = d["lines"][0]["line_id"]
    r = client.post(f"/api/capture/{d['id']}/confirm", json={"lines": [
        {"line_id": lid, "sku_code": "SKU-BS-075", "qty": 50, "unit": "piece"}]})
    assert r.status_code == 422 and "whole number of cases" in r.text
    r = client.post(f"/api/capture/{d['id']}/confirm", json={"lines": [
        {"line_id": lid, "sku_code": "SKU-BS-075", "qty": 96, "unit": "piece"}]})
    assert r.status_code == 200 and r.json()["lines"][0]["cases"] == 1


def test_drafts_are_private(client):
    login(client, SHARMA)
    d = draft(client)
    client.post("/api/auth/logout")
    login(client, GUPTA)
    assert client.get(f"/api/capture/{d['id']}").status_code == 404


def test_gibberish_message_rejected(client):
    login(client, SHARMA)
    assert client.post("/api/capture", json={"text": "hello bhaiya kaise ho"}).status_code == 422
    assert client.post("/api/capture", json={"text": "ab"}).status_code == 422
