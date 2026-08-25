"""Iteration 17 — /financial/overview flag semantics for a demo-ONLY user."""
import os
import uuid

import requests
from dotenv import dotenv_values

base = (os.environ.get("REACT_APP_BACKEND_URL")
        or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
API = f"{base}/api"


def test_demo_only_user_flags():
    s = requests.Session()
    email = f"it17flag_{uuid.uuid4().hex[:8]}@qa.finaura.dev"
    r = s.post(f"{API}/auth/register", json={"email": email, "password": "testpass123"}, timeout=40)
    assert r.status_code == 200, r.text[:300]
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok, r.text[:300]
    s.headers.update({"Authorization": f"Bearer {tok}"})
    try:
        assert s.post(f"{API}/statements/import-demo", timeout=40).status_code == 200
        ov = s.get(f"{API}/financial/overview", timeout=40).json()
        sources = {t.get("source") for t in ov["transactions"]}
        print("sources:", sources, "has_demo_data:", ov["has_demo_data"],
              "has_real_data:", ov["has_real_data"], "summary:", ov["summary"])
        assert sources == {"demo"}
        # A demo-only user must be flagged as demo, not real
        assert ov["has_demo_data"] is True, "demo-only user reported has_demo_data=False"
        assert ov["has_real_data"] is False, "demo rows counted as real data"
    finally:
        s.delete(f"{API}/financial/data", timeout=30)
        s.delete(f"{API}/auth/account", timeout=30)
