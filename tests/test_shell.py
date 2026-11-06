def test_app_shell_served_with_security_headers(client):
    r = client.get("/")
    assert r.status_code == 200 and "Kirana Order" in r.text
    assert "default-src 'self'" in r.headers["content-security-policy"]
    assert r.headers["x-content-type-options"] == "nosniff"


def test_service_worker_and_manifest_are_served(client):
    assert client.get("/sw.js").headers["content-type"].startswith("text/javascript")
    assert client.get("/static/manifest.webmanifest").status_code == 200


def test_api_responses_are_not_cacheable(client):
    assert client.get("/api/config").headers["cache-control"] == "no-store"


def test_config_exposes_demo_code_only_in_demo_mode(client):
    cfg = client.get("/api/config").json()
    assert cfg["demo_mode"] is True and cfg["demo_otp"]  # tests run with DEMO_MODE=1


def test_head_requests_are_accepted_for_uptime_monitors(client):
    assert client.head("/health").status_code == 200
    assert client.head("/").status_code == 200
