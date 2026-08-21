"""Phase 3 backend tests — WebAuthn passkeys + statement upload/parse/import."""
import io
import json
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")
API = f"{BASE_URL}/api"

EMAIL = "testuser@finaura.dev"
PASSWORD = "testpass123"

CSV_CONTENT = """Date,Description,Debit,Credit
01/08/2026,SWIGGY DINNER,450.00,
02/08/2026,AMAZON ORDER,1299.00,
03/08/2026,METRO CARD RECHARGE,200.00,
04/08/2026,NETFLIX SUBSCRIPTION,649.00,
05/08/2026,RENT PAYMENT,18000.00,
01/08/2026,SALARY AUG,,80000.00
"""

MAPPING = {"date": "Date", "description": "Description", "debit": "Debit", "credit": "Credit"}


@pytest.fixture(scope="session")
def token():
    r = requests.post(f"{API}/auth/login", json={"email": EMAIL, "password": PASSWORD}, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("token") or r.json().get("access_token")
    assert tok
    return tok


@pytest.fixture(scope="session")
def auth(token):
    return {"Authorization": f"Bearer {token}"}


# ---------------- Passkey module ----------------

class TestPasskey:
    def test_list_requires_jwt(self):
        r = requests.get(f"{API}/auth/passkey/list", timeout=30)
        assert r.status_code == 401, r.text

    def test_register_begin_requires_jwt(self):
        r = requests.post(f"{API}/auth/passkey/register/begin", timeout=30)
        assert r.status_code == 401, r.text

    def test_all_endpoints_require_jwt(self):
        calls = [
            ("get", f"{API}/auth/passkey/list", None),
            ("post", f"{API}/auth/passkey/register/begin", None),
            ("post", f"{API}/auth/passkey/register/complete", {}),
            ("post", f"{API}/auth/passkey/authenticate/begin", None),
            ("post", f"{API}/auth/passkey/authenticate/complete", {}),
            ("delete", f"{API}/auth/passkey/abc123", None),
        ]
        for method, url, body in calls:
            r = requests.request(method, url, json=body, timeout=30)
            assert r.status_code == 401, f"{method} {url} -> {r.status_code}"

    def test_list_empty(self, auth):
        r = requests.get(f"{API}/auth/passkey/list", headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        data = r.json()
        assert "credentials" in data
        assert isinstance(data["credentials"], list)
        assert data["credentials"] == []

    def test_register_begin_contract(self, auth):
        r = requests.post(f"{API}/auth/passkey/register/begin", headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        opts = r.json()
        assert opts["rp"]["id"] == "wealth-insights-43.preview.emergentagent.com"
        assert len(opts["challenge"]) >= 43
        assert opts["user"]["name"] == EMAIL
        assert opts["user"]["id"]
        assert isinstance(opts.get("pubKeyCredParams"), list) and opts["pubKeyCredParams"]

    def test_register_complete_invalid_payload_is_400(self, auth):
        # ensure a pending challenge exists so we hit the verification path
        requests.post(f"{API}/auth/passkey/register/begin", headers=auth, timeout=30)
        bogus = {
            "id": "AAAA",
            "rawId": "AAAA",
            "type": "public-key",
            "response": {"clientDataJSON": "eyJmb28iOiJiYXIifQ", "attestationObject": "AAAA"},
        }
        r = requests.post(f"{API}/auth/passkey/register/complete", headers=auth, json=bogus, timeout=30)
        assert r.status_code == 400, f"expected 400, got {r.status_code}: {r.text[:300]}"

    def test_authenticate_begin_404_without_credentials(self, auth):
        r = requests.post(f"{API}/auth/passkey/authenticate/begin", headers=auth, timeout=30)
        assert r.status_code == 404, r.text

    def test_delete_unknown_prefix_404(self, auth):
        r = requests.delete(f"{API}/auth/passkey/deadbeef1234", headers=auth, timeout=30)
        assert r.status_code == 404, r.text


def test_webauthn_challenge_ttl_index():
    from pymongo import MongoClient
    env = dotenv_values("/app/backend/.env")
    client = MongoClient(env["MONGO_URL"])
    db = client[env["DB_NAME"]]
    idx = db.webauthn_challenges.index_information()
    ttl = [v for v in idx.values() if v.get("expireAfterSeconds") == 0]
    assert ttl, f"no TTL index found: {idx}"
    assert any(k[0] == "expires_at" for v in ttl for k in v["key"]), idx
    client.close()


# ---------------- Statements module ----------------

class TestStatements:
    def test_endpoints_require_jwt(self):
        files = {"file": ("s.csv", CSV_CONTENT, "text/csv")}
        assert requests.post(f"{API}/statements/preview", files=files, timeout=30).status_code == 401
        assert requests.post(f"{API}/statements/parse", files={"file": ("s.csv", CSV_CONTENT, "text/csv")},
                             data={"mapping": json.dumps(MAPPING)}, timeout=30).status_code == 401
        assert requests.post(f"{API}/statements/confirm-import", json={"transactions": []},
                             timeout=30).status_code == 401

    def test_csv_preview(self, auth):
        r = requests.post(f"{API}/statements/preview", headers=auth,
                          files={"file": ("stmt.csv", CSV_CONTENT, "text/csv")}, timeout=60)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["kind"] == "csv"
        assert d["total_rows"] == 6, d
        assert d["columns"] == ["Date", "Description", "Debit", "Credit"]
        assert d["guess"]["date"] == "Date"
        assert d["guess"]["debit"] == "Debit"
        assert d["guess"]["credit"] == "Credit"
        assert len(d["rows"]) == 5

    def test_csv_parse(self, auth):
        r = requests.post(f"{API}/statements/parse", headers=auth,
                          files={"file": ("stmt.csv", CSV_CONTENT, "text/csv")},
                          data={"mapping": json.dumps(MAPPING)}, timeout=60)
        assert r.status_code == 200, r.text
        txns = r.json()["transactions"]
        assert len(txns) == 6, txns
        income = [t for t in txns if t["type"] == "Income"]
        expense = [t for t in txns if t["type"] == "Expense"]
        assert len(income) == 1 and len(expense) == 5
        assert income[0]["amount"] == 80000.0
        assert "SALARY" in income[0]["description"]
        cats = {t["description"]: t["category"] for t in txns}
        assert cats["SWIGGY DINNER"] == "Food"
        assert cats["AMAZON ORDER"] == "Shopping"
        assert cats["METRO CARD RECHARGE"] == "Transport"
        assert cats["NETFLIX SUBSCRIPTION"] == "Entertainment"
        assert cats["RENT PAYMENT"] == "Rent"
        assert all(t["date"].endswith("2026") for t in txns), [t["date"] for t in txns]

    def test_confirm_import_persists(self, auth):
        payload = {"transactions": [
            {"date": "01 Aug 2026", "description": "TEST_IMPORT_CHECK", "amount": 123.45,
             "type": "Expense", "category": "Food"},
        ]}
        r = requests.post(f"{API}/statements/confirm-import", headers=auth, json=payload, timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["imported"] == 1
        ov = requests.get(f"{API}/financial/overview", headers=auth, timeout=60)
        assert ov.status_code == 200, ov.text
        body = ov.json()
        blob = json.dumps(body)
        assert "TEST_IMPORT_CHECK" in blob, list(body.keys())
        assert "_id" not in blob

    def test_unsupported_file_415(self, auth):
        r = requests.post(f"{API}/statements/preview", headers=auth,
                          files={"file": ("notes.txt", "hello", "text/plain")}, timeout=30)
        assert r.status_code == 415, f"{r.status_code}: {r.text[:200]}"

    def test_oversize_file_413(self, auth):
        big = b"a,b\n" + b"1,2\n" * (11 * 1024 * 1024 // 4)
        r = requests.post(f"{API}/statements/preview", headers=auth,
                          files={"file": ("big.csv", big, "text/csv")}, timeout=180)
        assert r.status_code == 413, f"{r.status_code}: {r.text[:200]}"

    def test_excel_preview_and_parse(self, auth):
        import pandas as pd
        df = pd.read_csv(io.StringIO(CSV_CONTENT))
        buf = io.BytesIO()
        df.to_excel(buf, index=False)
        content = buf.getvalue()
        r = requests.post(f"{API}/statements/preview", headers=auth,
                          files={"file": ("stmt.xlsx", content,
                                          "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
                          timeout=60)
        assert r.status_code == 200, r.text
        assert r.json()["kind"] == "excel"
        assert r.json()["total_rows"] == 6
        r2 = requests.post(f"{API}/statements/parse", headers=auth,
                           files={"file": ("stmt.xlsx", content, "application/octet-stream")},
                           data={"mapping": json.dumps(MAPPING)}, timeout=60)
        assert r2.status_code == 200, r2.text
        assert len(r2.json()["transactions"]) == 6

    def test_pdf_preview(self, auth):
        try:
            from reportlab.pdfgen import canvas
        except ImportError:
            pytest.skip("reportlab not installed")
        buf = io.BytesIO()
        c = canvas.Canvas(buf)
        y = 800
        for line in ["Finaura Bank Statement",
                     "01/08/2026 SWIGGY DINNER 450.00 DR",
                     "02/08/2026 AMAZON ORDER 1299.00 DR",
                     "01/08/2026 SALARY AUG 80000.00 CR"]:
            c.drawString(50, y, line)
            y -= 20
        c.save()
        content = buf.getvalue()
        r = requests.post(f"{API}/statements/preview", headers=auth,
                          files={"file": ("stmt.pdf", content, "application/pdf")}, timeout=90)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["kind"] == "pdf"
        txns = d["transactions"]
        assert len(txns) >= 1, d
        first = txns[0]
        assert first["date"]
        assert first["description"]
        assert first["amount"] > 0
        assert first["type"] == "Expense", first


# ---------------- Regression ----------------

class TestRegression:
    def test_auth_config(self):
        r = requests.get(f"{API}/auth/config", timeout=30)
        assert r.status_code == 200, r.text
        d = r.json()
        assert d["google_enabled"] is False
        assert d["apple_enabled"] is False
        assert d["resend_enabled"] is False

    def test_me_includes_has_passkey(self, auth):
        r = requests.get(f"{API}/auth/me", headers=auth, timeout=30)
        assert r.status_code == 200, r.text
        user = r.json().get("user", r.json())
        assert "has_passkey" in user, user
        assert user["has_passkey"] in (True, False)

    def test_demo_overview_public(self):
        r = requests.get(f"{API}/demo/overview", timeout=60)
        assert r.status_code == 200, r.text
        assert "transactions" in r.json() or "summary" in r.json()

    def test_goal_crud(self, auth):
        r = requests.post(f"{API}/goals", headers=auth,
                          json={"name": "TEST_Phase3 Goal", "target_amount": 50000,
                                "current_amount": 1000, "deadline": "2027-01-01"}, timeout=60)
        assert r.status_code in (200, 201), r.text
        gid = r.json().get("id") or r.json().get("goal", {}).get("id")
        assert gid
        d = requests.delete(f"{API}/goals/{gid}", headers=auth, timeout=60)
        assert d.status_code in (200, 204), d.text

    def test_chat(self, auth):
        # /api/chat streams plain text/SSE chunks
        r = requests.post(f"{API}/chat", headers=auth,
                          json={"message": "How much did I spend on food?"},
                          timeout=180, stream=True)
        assert r.status_code == 200, r.text
        text = r.text
        assert len(text.strip()) > 5, repr(text[:200])
