import os
import requests

import pytest
from dotenv import dotenv_values

_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or _env["REACT_APP_BACKEND_URL"]).splitlines()[0].rstrip("/")

# Phase 1 suite: these hit the pre-auth (now Bearer-protected) finance endpoints.
# Superseded by tests/test_auth_phase2.py which covers the same ground with auth.
pytestmark = pytest.mark.skip(reason="Obsolete after Phase 2 auth: endpoints now require Bearer JWT; see test_auth_phase2.py")


def test_overview_has_demo_profile_and_no_mongo_id():
    response = requests.get(f"{BASE_URL}/api/financial/overview", timeout=20)
    assert response.status_code == 200
    data = response.json()
    assert data["user"]["name"] == "Aarav Sharma"
    assert all("_id" not in item for item in data["transactions"] + data["goals"])


def test_create_goal_returns_serializable_goal():
    payload = {"name": "TEST_regression_goal", "target_amount": 500000, "deadline": "2030"}
    response = requests.post(f"{BASE_URL}/api/goals", json=payload, timeout=20)
    assert response.status_code == 200
    created = response.json()
    assert created["name"] == payload["name"]
    assert isinstance(created["id"], str)
    assert "_id" not in created


def test_transaction_category_persists_in_overview():
    overview = requests.get(f"{BASE_URL}/api/financial/overview", timeout=20).json()
    txn = next(t for t in overview["transactions"] if t["id"] == "txn-1")
    response = requests.patch(f"{BASE_URL}/api/transactions/{txn['id']}", json={"category": "Other"}, timeout=20)
    assert response.status_code == 200
    refreshed = requests.get(f"{BASE_URL}/api/financial/overview", timeout=20).json()
    assert next(t for t in refreshed["transactions"] if t["id"] == txn["id"])["category"] == "Other"


def test_delete_financial_data_confirms_deletion():
    response = requests.delete(f"{BASE_URL}/api/financial/data", timeout=20)
    assert response.status_code == 200
    assert response.json() == {"deleted": True}


def test_chat_returns_configured_or_clear_unavailable_response():
    response = requests.post(f"{BASE_URL}/api/chat", json={"message": "Why did my savings change?"}, timeout=30)
    assert response.status_code in (200, 503)
    if response.status_code == 503:
        assert "not configured" in response.text.lower()