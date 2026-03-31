"""Tests for JWT authentication and patient identification endpoints.

Uses FastAPI TestClient for endpoint-level testing with a real in-memory
SQLite database (not mocked).
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from src.db.models import Base, Patient
from src.db.database import get_db
from src.main import app
from src.api.auth import create_access_token, _decode_token, ALGORITHM


# ---------------------------------------------------------------------------
# Test database fixture
# ---------------------------------------------------------------------------

@pytest.fixture
def test_db():
    """Create an in-memory SQLite DB with tables and seed data."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    TestSession = sessionmaker(bind=engine)
    db = TestSession()

    # Seed a patient
    patient = Patient(
        id="test_patient_1",
        full_name="Sarah Johnson",
        phone="5550101234",
        date_of_birth=None,
        insurance_name=None,
    )
    db.add(patient)
    db.commit()

    yield db

    db.close()
    engine.dispose()


@pytest.fixture
def client(test_db):
    """FastAPI test client with overridden DB dependency."""
    def _override_db():
        yield test_db

    app.dependency_overrides[get_db] = _override_db
    with TestClient(app) as c:
        yield c
    app.dependency_overrides.clear()


def _auth_header(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ---------------------------------------------------------------------------
# Token creation tests
# ---------------------------------------------------------------------------

class TestTokenCreation:
    def test_create_token_generates_session_id(self):
        token, session_id = create_access_token()
        assert len(session_id) == 16
        assert token

    def test_create_token_with_existing_session_id(self):
        token, session_id = create_access_token(session_id="my_session")
        assert session_id == "my_session"

    def test_token_roundtrip(self):
        token, session_id = create_access_token(session_id="test123")
        data = _decode_token(token)
        assert data.session_id == "test123"


# ---------------------------------------------------------------------------
# Auth endpoint tests
# ---------------------------------------------------------------------------

class TestAuthEndpoints:
    def test_issue_token(self, client):
        resp = client.post("/api/auth/token")
        assert resp.status_code == 200
        body = resp.json()
        assert "access_token" in body
        assert "session_id" in body
        assert body["token_type"] == "bearer"

    def test_refresh_token(self, client):
        # Get initial token
        resp = client.post("/api/auth/token")
        token = resp.json()["access_token"]
        session_id = resp.json()["session_id"]

        # Refresh it
        resp2 = client.post("/api/auth/refresh", headers=_auth_header(token))
        assert resp2.status_code == 200
        body = resp2.json()
        # Same session_id preserved
        assert body["session_id"] == session_id
        assert body["access_token"]  # non-empty token returned

    def test_refresh_without_token_returns_401(self, client):
        resp = client.post("/api/auth/refresh")
        assert resp.status_code == 401

    def test_refresh_with_invalid_token_returns_401(self, client):
        resp = client.post("/api/auth/refresh", headers=_auth_header("garbage"))
        assert resp.status_code == 401

    def test_expired_token_returns_401(self, client):
        from datetime import timedelta
        token, _ = create_access_token(expires_delta=timedelta(seconds=-1))
        resp = client.post("/api/auth/refresh", headers=_auth_header(token))
        assert resp.status_code == 401


# ---------------------------------------------------------------------------
# Patient identification tests
# ---------------------------------------------------------------------------

class TestIdentifyEndpoint:
    def _get_token(self, client) -> tuple[str, str]:
        resp = client.post("/api/auth/token")
        body = resp.json()
        return body["access_token"], body["session_id"]

    def test_identify_requires_auth(self, client):
        resp = client.post("/api/identify", json={"mode": "question"})
        assert resp.status_code == 401

    def test_question_mode(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "question"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        assert resp.json()["status"] == "ok"

    def test_returning_patient_found(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "returning", "name": "Sarah Johnson", "phone": "555-010-1234"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["patient_id"] == "test_patient_1"
        assert body["patient_name"] == "Sarah Johnson"
        assert "dob" in body["needs_info"]

    def test_returning_patient_not_found(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "returning", "name": "Nobody", "phone": "555-999-9999"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "not_found"

    def test_new_patient_created(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "new", "name": "Alex Test", "phone": "555-888-7777"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "ok"
        assert body["patient_id"] is not None
        assert body["patient_name"] == "Alex Test"
        assert "dob" in body["needs_info"]
        assert "insurance" in body["needs_info"]

    def test_new_patient_duplicate_phone(self, client):
        """Registering with an existing phone returns 'existing' status."""
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "new", "name": "Duplicate", "phone": "555-010-1234"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "existing"
        assert body["patient_name"] == "Sarah Johnson"

    def test_returning_missing_name_phone(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "returning"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"

    def test_invalid_phone_format(self, client):
        token, _ = self._get_token(client)
        resp = client.post(
            "/api/identify",
            json={"mode": "new", "name": "Test", "phone": "123"},
            headers=_auth_header(token),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "error"
