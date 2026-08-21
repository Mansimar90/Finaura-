"""Tests for the multi-model Ask Finaura chat (OpenAI + Claude Sonnet 5)."""
import os

import pytest
import requests
from dotenv import dotenv_values

frontend_env = dotenv_values("/app/frontend/.env")
base_url = os.environ.get("REACT_APP_BACKEND_URL") or frontend_env.get("REACT_APP_BACKEND_URL")
if not base_url:
    raise RuntimeError("REACT_APP_BACKEND_URL missing")
BASE_URL = base_url.rstrip("/")

CREDS = {"email": "testuser@finaura.dev", "password": "testpass123"}


@pytest.fixture(scope="module")
def session():
    s = requests.Session()
    s.headers.update({"Content-Type": "application/json"})
    return s


@pytest.fixture(scope="module")
def token(session):
    r = session.post(f"{BASE_URL}/api/auth/login", json=CREDS, timeout=30)
    if r.status_code != 200:
        pytest.fail(f"login failed {r.status_code}: {r.text[:300]}")
    tok = r.json().get("access_token") or r.json().get("token")
    assert tok
    return tok


def _stream(payload, headers=None, timeout=120):
    r = requests.post(
        f"{BASE_URL}/api/chat", json=payload,
        headers={"Content-Type": "application/json", **(headers or {})},
        stream=True, timeout=timeout,
    )
    body = ""
    if r.status_code == 200:
        for chunk in r.iter_content(chunk_size=None, decode_unicode=True):
            if chunk:
                body += chunk
    return r, body


# --- /api/chat/models ---
class TestChatModels:
    def test_models_public(self, session):
        r = session.get(f"{BASE_URL}/api/chat/models", timeout=30)
        assert r.status_code == 200, r.text[:300]
        data = r.json()
        assert data["default"] == "openai"
        ids = [m["id"] for m in data["models"]]
        assert ids == ["openai", "claude"], ids
        claude = next(m for m in data["models"] if m["id"] == "claude")
        assert claude["label"] == "Claude Sonnet 5"
        assert claude["provider"] == "anthropic"
        openai = next(m for m in data["models"] if m["id"] == "openai")
        assert openai["provider"] == "openai"
        assert isinstance(openai["label"], str) and openai["label"]


# --- /api/chat streaming, anonymous demo mode ---
class TestAnonymousChat:
    def test_openai_stream(self):
        r, body = _stream({"message": "Say hello in 3 words", "model": "openai"})
        assert r.status_code == 200, body[:300]
        assert r.headers.get("content-type", "").startswith("text/plain")
        assert r.headers.get("X-Model") == "OpenAI GPT-5.4"
        assert len(body.strip()) > 0, "empty stream"

    def test_claude_stream(self):
        r, body = _stream({"message": "Say hello in 3 words", "model": "claude"})
        assert r.status_code == 200, body[:300]
        assert r.headers.get("X-Model") == "Claude Sonnet 5"
        assert len(body.strip()) > 0, "empty stream"

    def test_unknown_model_falls_back(self):
        r, body = _stream({"message": "Say hi in 2 words", "model": "llama"})
        assert r.status_code == 200, body[:300]
        assert r.headers.get("X-Model") == "OpenAI GPT-5.4"
        assert len(body.strip()) > 0

    def test_no_model_field_defaults_openai(self):
        r, body = _stream({"message": "Say hi in 2 words"})
        assert r.status_code == 200, body[:300]
        assert r.headers.get("X-Model") == "OpenAI GPT-5.4"
        assert len(body.strip()) > 0


# --- /api/chat authenticated (personalised system prompt) ---
class TestAuthenticatedChat:
    def test_claude_authenticated(self, token):
        r, body = _stream(
            {"message": "What is my monthly income according to my profile? Answer briefly.",
             "model": "claude"},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, body[:300]
        assert r.headers.get("X-Model") == "Claude Sonnet 5"
        assert len(body.strip()) > 0

    def test_openai_authenticated_no_model(self, token):
        r, body = _stream(
            {"message": "Name one of my goals briefly."},
            headers={"Authorization": f"Bearer {token}"},
        )
        assert r.status_code == 200, body[:300]
        assert r.headers.get("X-Model") == "OpenAI GPT-5.4"
        assert len(body.strip()) > 0

    def test_invalid_token_still_works_anonymously(self):
        r, body = _stream(
            {"message": "Say hi", "model": "claude"},
            headers={"Authorization": "Bearer garbage.token.value"},
        )
        assert r.status_code == 200, body[:300]
        assert len(body.strip()) > 0
