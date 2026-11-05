import os

os.environ["DEMO_MODE"] = "1"
os.environ["DEMO_OTP"] = "123456"
os.environ["DATABASE_URL"] = "sqlite:///./test_kirana.db"

import pytest
from fastapi.testclient import TestClient

from app.db import Base, engine
from app.main import app


@pytest.fixture()
def client():
    Base.metadata.drop_all(engine)
    with TestClient(app) as c:  # lifespan creates tables + seeds
        yield c


def login(client, phone):
    r = client.post("/api/auth/login", json={"phone": phone, "otp": "123456"})
    assert r.status_code == 200, r.text
    return r.json()
