"""Post-registration passkey state checks (run after a passkey has been registered
via the Playwright virtual authenticator). Skips when no passkey exists."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
BASE_URL = (os.environ.get("REACT_APP_BACKEND_URL") or frontend_env["REACT_APP_BACKEND_URL"]).rstrip("/")
API = f"{BASE_URL}/api"


@pytest.fixture(scope="module")
def auth():
    r = requests.post(f"{API}/auth/login",
                      json={"email": "testuser@finaura.dev", "password": "testpass123"}, timeout=30)
    assert r.status_code == 200, r.text
    body = r.json()
    tok = body.get("token") or body.get("access_token")
    assert tok, body
    return {"Authorization": f"Bearer {tok}"}


def test_registered_passkey_lifecycle(auth):
    lst = requests.get(f"{API}/auth/passkey/list", headers=auth, timeout=30)
    assert lst.status_code == 200, lst.text
    creds = lst.json()["credentials"]
    if not creds:
        pytest.skip("no passkey registered — run the virtual-authenticator UI flow first")
    cred = creds[0]
    assert cred["id"] and len(cred["id"]) == 12
    assert cred["label"]

    me = requests.get(f"{API}/auth/me", headers=auth, timeout=30).json()
    user = me.get("user", me)
    assert user["has_passkey"] is True

    begin = requests.post(f"{API}/auth/passkey/authenticate/begin", headers=auth, timeout=30)
    assert begin.status_code == 200, begin.text
    opts = begin.json()
    assert opts["rpId"] == "wealth-insights-43.preview.emergentagent.com"
    assert len(opts["challenge"]) >= 43
    assert opts["allowCredentials"], opts

    # authenticate/complete with tampered payload must not 500
    bad = requests.post(f"{API}/auth/passkey/authenticate/complete", headers=auth,
                        json={"id": cred["id"], "response": {}}, timeout=30)
    assert bad.status_code in (400, 401), f"{bad.status_code}: {bad.text[:200]}"

    # cleanup: delete and confirm flag flips back
    d = requests.delete(f"{API}/auth/passkey/{cred['id']}", headers=auth, timeout=30)
    assert d.status_code == 200, d.text
    assert d.json()["remaining"] == len(creds) - 1
    after = requests.get(f"{API}/auth/passkey/list", headers=auth, timeout=30).json()["credentials"]
    assert len(after) == len(creds) - 1
    me2 = requests.get(f"{API}/auth/me", headers=auth, timeout=30).json()
    assert (me2.get("user", me2))["has_passkey"] is (len(after) > 0)
