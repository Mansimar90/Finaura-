"""Iteration 8 targeted backend tests: memory upsert preservation + learn article detail."""
import os
from datetime import datetime

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

EMAIL = "testuser@finaura.dev"
PASSWORD = "testpass123"

ARTICLE_IDS = [
    "emergency-funds",
    "mutual-funds-101",
    "compound-interest",
    "inflation-basics",
    "tax-regimes-fy-2025-26",
    "sip-vs-lump-sum",
]


@pytest.fixture(scope="module")
def client():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    r = s.post(f"{BASE_URL}/api/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    token = r.json().get("access_token")
    if not token:
        pytest.fail(f"no access_token in login response: {r.text[:300]}")
    s.headers.update({"Authorization": f"Bearer {token}"})
    return s


def _parse(ts):
    return datetime.fromisoformat(str(ts).replace("Z", "+00:00"))


# --- memories upsert preservation ---
class TestMemoryUpsertPreservation:
    KEY = "preserve_test"

    def test_upsert_preserves_id_and_created_at(self, client):
        # cleanup any prior row
        rows = client.get(f"{BASE_URL}/api/memories", timeout=30).json()["memories"]
        for m in rows:
            if m["key"] == self.KEY:
                client.delete(f"{BASE_URL}/api/memories/{m['id']}", timeout=30)

        first = client.post(f"{BASE_URL}/api/memories", json={
            "category": "income", "key": self.KEY, "value": "first", "numeric_value": 100,
        }, timeout=30)
        assert first.status_code == 200, first.text
        d1 = first.json()
        assert "_id" not in d1
        assert d1["value"] == "first"
        assert d1["numeric_value"] == 100
        first_id, first_created = d1["id"], d1["created_at"]

        second = client.post(f"{BASE_URL}/api/memories", json={
            "category": "income", "key": self.KEY, "value": "second", "numeric_value": 200,
        }, timeout=30)
        assert second.status_code == 200, second.text
        d2 = second.json()
        assert d2["id"] == first_id, "id changed on upsert"
        assert d2["created_at"] == first_created, "created_at changed on upsert"
        assert d2["value"] == "second"
        assert d2["numeric_value"] == 200
        assert _parse(d2["updated_at"]) > _parse(d2["created_at"]), "updated_at not newer than created_at"

        rows = client.get(f"{BASE_URL}/api/memories", timeout=30).json()["memories"]
        matched = [m for m in rows if m["key"] == self.KEY]
        assert len(matched) == 1, f"expected 1 row, got {len(matched)}"
        assert matched[0]["id"] == first_id
        assert matched[0]["value"] == "second"

        # teardown
        assert client.delete(f"{BASE_URL}/api/memories/{first_id}", timeout=30).status_code in (200, 204)


# --- learn article detail endpoint ---
class TestLearnArticles:
    def test_list_articles(self, client):
        r = client.get(f"{BASE_URL}/api/learn/articles", timeout=30)
        assert r.status_code == 200
        ids = [a["id"] for a in r.json()["articles"]]
        for aid in ARTICLE_IDS:
            assert aid in ids

    @pytest.mark.parametrize("aid", ARTICLE_IDS)
    def test_article_detail(self, client, aid):
        r = client.get(f"{BASE_URL}/api/learn/articles/{aid}", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d.get("id") == aid
        assert d.get("title")
        assert d.get("sections") or d.get("body")

    def test_article_detail_unknown_returns_404(self, client):
        r = client.get(f"{BASE_URL}/api/learn/articles/undefined", timeout=30)
        assert r.status_code == 404
