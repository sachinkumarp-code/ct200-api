
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

def test_browse_documents_versions_and_sections(client):
    pdf_path = Path(__file__).parent.parent / "data" / "ct200_manual.pdf"

    with open(pdf_path, "rb") as pdf_file:
        response = client.post(
            "/ingest",
            data={"document_name": "CT-200-Browse", "version_label": "v1"},
            files={"file": ("ct200_manual.pdf", pdf_file, "application/pdf")},
        )

    assert response.status_code == 200

    # Browse all documents.
    response = client.get("/documents")
    assert response.status_code == 200
    assert any(
        document["name"] == "CT-200-Browse"
        for document in response.json()["documents"]
    )

    # Browse versions of this manual.
    response = client.get("/documents/CT-200-Browse/versions")
    assert response.status_code == 200
    assert response.json()["versions"][0]["version_label"] == "v1"

    # Browse all sections in the version.
    response = client.get(
        "/documents/CT-200-Browse/versions/v1/sections"
    )
    assert response.status_code == 200
    sections = response.json()["sections"]
    assert len(sections) == 27
    assert any(section["section_number"] == "2.1" for section in sections)

    # Retrieve one section with its hierarchy.
    response = client.get(
        "/documents/CT-200-Browse/versions/v1/sections/2.1"
    )
    assert response.status_code == 200
    section = response.json()["section"]
    assert section["section_number"] == "2.1"
    assert "children" in section
    assert "parent" in section


def test_browse_unknown_document_returns_404(client):
    response = client.get("/documents/Unknown-Manual/versions")
    assert response.status_code == 404
