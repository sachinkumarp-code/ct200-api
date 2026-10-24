# CT-200 Manual API

A FastAPI application for ingesting CT-200 medical-device manual PDFs, storing versioned sections, retrieving specific sections, selecting source content from a pinned version, and comparing manual versions.

## Features

- PDF ingestion with section parsing and extraction warnings.
- SQLite storage for documents, versions, and section nodes.
- Manual and version browsing.
- Section retrieval with parent/child hierarchy.
- Version-pinned section selection with content hashes.
- Version comparison identifying added, removed, changed, and unchanged sections.
- Heuristic severity flags for potentially important changes.
- Automated parser and API tests.

## Technology stack

- Python
- FastAPI
- SQLAlchemy
- SQLite
- PyMuPDF
- Pydantic
- Pytest

## Project structure

```text
ct200-api/
├── app/
│   ├── main.py
│   ├── models.py
│   ├── parser.py
│   ├── database.py
│   └── init_db.py
├── data/
│   ├── ct200_manual.pdf
│   └── ct200_manual_v2.pdf
├── docs/
│   └── irregularities.md
├── tests/
│   ├── test_ingest.py
│   └── test_parser.py
├── requirements.txt
└── README.md
```

## Setup

Python 3.13 was used during development.

Create and activate a virtual environment:

```bash
python3 -m venv venv
source venv/bin/activate
```

Install dependencies:

```bash
python -m pip install -r requirements.txt
```

Initialize the SQLite database:

```bash
python -m app.init_db
```

Start the API:

```bash
uvicorn app.main:app --reload
```

Interactive API documentation is available at:

- http://127.0.0.1:8000/docs

Health check:

```bash
curl http://127.0.0.1:8000/health
```

Expected response:

```json
{"status":"ok"}
```

## API endpoints

| Method | Endpoint | Purpose |
|---|---|---|
| GET | `/health` | Check API availability |
| POST | `/ingest` | Upload and ingest a PDF version |
| GET | `/documents` | List manuals and versions |
| GET | `/documents/{document_name}/versions` | List versions of a manual |
| GET | `/documents/{document_name}/versions/{version_label}/sections` | List sections in a version |
| GET | `/documents/{document_name}/versions/{version_label}/sections/{section_number}` | Retrieve one section and its hierarchy |
| POST | `/documents/{document_name}/versions/{version_label}/selection` | Select sections from a specific version |
| GET | `/documents/{document_name}/compare/{old_version}/{new_version}` | Compare two stored versions |

### Ingest a manual

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -F "document_name=CT-200" \
  -F "version_label=v1" \
  -F "file=@data/ct200_manual.pdf"
```

Ingest the second version using a different version label:

```bash
curl -X POST http://127.0.0.1:8000/ingest \
  -F "document_name=CT-200" \
  -F "version_label=v2" \
  -F "file=@data/ct200_manual_v2.pdf"
```

A duplicate version label for the same document returns HTTP `409`. An upload with no detected numbered sections returns HTTP `422`.

### Browse sections

```bash
curl http://127.0.0.1:8000/documents/CT-200/versions/v1/sections
```

Retrieve a specific section:

```bash
curl http://127.0.0.1:8000/documents/CT-200/versions/v1/sections/2.1
```

### Select sections from a pinned version

```bash
curl -X POST \
  http://127.0.0.1:8000/documents/CT-200/versions/v1/selection \
  -H "Content-Type: application/json" \
  -d '{"section_numbers":["2.1","3.2"]}'
```

The response includes the selected section content and hashes. A requested section that is not found in the selected version results in HTTP `422`.

### Compare versions

```bash
curl http://127.0.0.1:8000/documents/CT-200/compare/v1/v2
```

The comparison response includes the old and new version labels, section status, content hashes, and old/new text where available.

## Parsing and comparison design

- PDFs are parsed using PyMuPDF.
- Section hierarchy, source order, page number, and content hashes are stored for retrieved sections.
- Section comparisons use logical identifiers and normalized content hashes.
- Parser warnings preserve known structural irregularities instead of silently assuming the source document is consistent.
- The parser irregularities and decisions are documented in `docs/irregularities.md`.

## Limitations

- Extracted tables are currently reported in the ingestion response; they are not persisted as dedicated structured database records.
- The comparison severity labels are heuristic review aids, not validated medical-device risk assessments.
- Section-number lookups return the first matching section when a number is duplicated.
- The application does not currently generate LLM-authored outputs or validate generated text against a medical-device regulatory standard.
- SQLite is configured for local development and demonstration.

## Run tests

```bash
python -m pytest -v
```

The current parser and API test suite covers ingestion, duplicate-version handling, browsing, section selection, parser irregularities, and version comparison.
