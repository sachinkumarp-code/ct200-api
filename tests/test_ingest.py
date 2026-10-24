
from fastapi.testclient import TestClient
from pathlib import Path

from app.main import app

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.database import get_db
from app.models import Base
@pytest.fixture
def test_db():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )

    TestingSessionLocal = sessionmaker(
        bind=engine,
        autoflush=False,
        autocommit=False,
    )

    Base.metadata.create_all(bind=engine)

    def override_get_db():
        db = TestingSessionLocal()
        try:
            yield db
        finally:
            db.close()

    app.dependency_overrides[get_db] = override_get_db

    yield

    app.dependency_overrides.clear()
    Base.metadata.drop_all(bind=engine)
    engine.dispose()

@pytest.fixture
def client(test_db):
    return TestClient(app)


def test_health_endpoint(client):
    response = client.get("/health")

    assert response.status_code == 200


def test_duplicate_version_returns_conflict(client):
    pdf_path = Path(__file__).parent.parent / "data" / "ct200_manual.pdf"

    with open(pdf_path, "rb") as pdf_file:
        first_response = client.post(
            "/ingest",
            data={
                "document_name": "CT-200",
                "version_label": "v1",
            },
            files={
                "file": (
                    "ct200_manual.pdf",
                    pdf_file,
                    "application/pdf",
                )
            },
        )

    assert first_response.status_code == 200

    with open(pdf_path, "rb") as pdf_file:
        duplicate_response = client.post(
            "/ingest",
            data={
                "document_name": "CT-200",
                "version_label": "v1",
            },
            files={
                "file": (
                    "ct200_manual.pdf",
                    pdf_file,
                    "application/pdf",
                )
            },
        )

    assert duplicate_response.status_code == 409
    assert duplicate_response.json() == {
        "detail": "Version 'v1' already exists."
    }


def test_ingest_new_version_successfully(client):
    pdf_path = Path(__file__).parent.parent / "data" / "ct200_manual.pdf"

    with open(pdf_path, "rb") as pdf_file:
        response = client.post(
            "/ingest",
            data={
                "document_name": "CT-200-Test",
                "version_label": "v1",
            },
            files={
                "file": (
                    "ct200_manual.pdf",
                    pdf_file,
                    "application/pdf",
                )
            },
        )

    assert response.status_code == 200

    result = response.json()
    assert result["document_name"] == "CT-200-Test"
    assert result["version_label"] == "v1"
    assert result["sections_stored"] == 27
    assert result["tables_extracted"] == 2
