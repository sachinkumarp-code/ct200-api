
"""FastAPI endpoints for ingesting CT-200 manual versions."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, Node, Version
from app.parser import parse_pdf

app = FastAPI(title="CT-200 Manual API")


@app.get("/health")
def health_check():
    """Check that the API is running."""
    return {"status": "ok"}


@app.post("/ingest")
def ingest_manual(
    document_name: str = Form(...),
    version_label: str = Form(...),
    file: UploadFile = File(...),
    db: Session = Depends(get_db),
):
    """Parse a PDF and save its document version and sections."""

    if not file.filename or Path(file.filename).suffix.lower() != ".pdf":
        raise HTTPException(status_code=400, detail="Please upload a PDF file.")

    # Read the uploaded PDF into a temporary file for PyMuPDF.
    temporary_path = None

    try:
        with NamedTemporaryFile(suffix=".pdf", delete=False) as temp_file:
            temporary_path = Path(temp_file.name)
            temp_file.write(file.file.read())

        # Parse before writing anything to the database.
        parsed = parse_pdf(str(temporary_path))

        if not parsed.nodes:
            raise HTTPException(
                status_code=422,
                detail="No numbered sections were detected in the PDF.",
            )

        # Find or create the manual record.
        document = (
            db.query(Document)
            .filter(Document.name == document_name)
            .first()
        )

        if document is None:
            document = Document(name=document_name)
            db.add(document)
            db.flush()

        # Each document must have a unique version label.
        existing_version = (
            db.query(Version)
            .filter(
                Version.document_id == document.id,
                Version.version_label == version_label,
            )
            .first()
        )

        if existing_version is not None:
            raise HTTPException(
                status_code=409,
                detail=f"Version '{version_label}' already exists.",
            )

        version = Version(
            document_id=document.id,
            version_label=version_label,
            source_filename=file.filename,
        )
        db.add(version)
        db.flush()

        # First pass: create each node and map section numbers to database IDs.
        section_to_id = {}

        for parsed_node in parsed.nodes:
            node = Node(
                version_id=version.id,
                section_number=parsed_node.section_number,
                title=parsed_node.title,
                level=parsed_node.level,
                body=parsed_node.body,
                logical_id=parsed_node.logical_id,
                content_hash=parsed_node.content_hash,
                source_order=parsed_node.source_order,
                page_number=parsed_node.page_number,
            )
            db.add(node)
            db.flush()
            if parsed_node.section_number not in section_to_id:
                section_to_id[parsed_node.section_number] = node.id

        # Second pass: resolve parent section numbers to database node IDs.
        for parsed_node in parsed.nodes:
            if parsed_node.parent_section is None:
                continue

            node_id = section_to_id.get(parsed_node.section_number)
            parent_id = section_to_id.get(parsed_node.parent_section)

            if node_id is not None and parent_id is not None:
                db.query(Node).filter(Node.id == node_id).update(
                    {"parent_id": parent_id}
                )

        db.commit()

        return {
            "message": "Manual version ingested successfully.",
            "document_name": document_name,
            "version_label": version_label,
            "sections_stored": len(parsed.nodes),
            "tables_extracted": len(parsed.tables),
            "warnings": parsed.warnings,
        }

    except HTTPException:
        db.rollback()
        raise
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="An internal error occurred while ingesting the PDF.",
        )
    finally:
        if temporary_path is not None:
            temporary_path.unlink(missing_ok=True)
        file.file.close()


@app.get("/documents")
def list_documents(db: Session = Depends(get_db)):
    """List all manuals and their available versions."""
    documents = db.query(Document).order_by(Document.name).all()

    result = []
    for document in documents:
        versions = (
            db.query(Version)
            .filter(Version.document_id == document.id)
            .order_by(Version.id)
            .all()
        )

        result.append({
            "id": document.id,
            "name": document.name,
            "versions": [
                {
                    "id": version.id,
                    "version_label": version.version_label,
                    "source_filename": version.source_filename,
                    "section_count": (
                        db.query(Node)
                        .filter(Node.version_id == version.id)
                        .count()
                    ),
                }
                for version in versions
            ],
        })

    return {"documents": result}


@app.get("/documents/{document_name}/versions")
def list_versions(document_name: str, db: Session = Depends(get_db)):
    """List all versions of a named manual."""
    document = (
        db.query(Document)
        .filter(Document.name == document_name)
        .first()
    )

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    versions = (
        db.query(Version)
        .filter(Version.document_id == document.id)
        .order_by(Version.id)
        .all()
    )

    return {
        "document_name": document.name,
        "versions": [
            {
                "id": version.id,
                "version_label": version.version_label,
                "source_filename": version.source_filename,
                "section_count": (
                    db.query(Node)
                    .filter(Node.version_id == version.id)
                    .count()
                ),
            }
            for version in versions
        ],
    }


@app.get(
    "/documents/{document_name}/versions/{version_label}/sections"
)
def list_sections(
    document_name: str,
    version_label: str,
    db: Session = Depends(get_db),
):
    """List sections in source order for a specific manual version."""
    document = (
        db.query(Document)
        .filter(Document.name == document_name)
        .first()
    )

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    version = (
        db.query(Version)
        .filter(
            Version.document_id == document.id,
            Version.version_label == version_label,
        )
        .first()
    )

    if version is None:
        raise HTTPException(status_code=404, detail="Version not found.")

    nodes = (
        db.query(Node)
        .filter(Node.version_id == version.id)
        .order_by(Node.source_order)
        .all()
    )

    return {
        "document_name": document.name,
        "version_label": version.version_label,
        "sections": [
            {
                "section_number": node.section_number,
                "title": node.title,
                "level": node.level,
                "parent_id": node.parent_id,
                "page_number": node.page_number,
                "source_order": node.source_order,
            }
            for node in nodes
        ],
    }


@app.get(
    "/documents/{document_name}/versions/{version_label}/sections/{section_number}"
)
def get_section(
    document_name: str,
    version_label: str,
    section_number: str,
    db: Session = Depends(get_db),
):
    """Retrieve a section, its parent, and its direct children."""
    document = (
        db.query(Document)
        .filter(Document.name == document_name)
        .first()
    )

    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    version = (
        db.query(Version)
        .filter(
            Version.document_id == document.id,
            Version.version_label == version_label,
        )
        .first()
    )

    if version is None:
        raise HTTPException(status_code=404, detail="Version not found.")

    node = (
        db.query(Node)
        .filter(
            Node.version_id == version.id,
            Node.section_number == section_number,
        )
        .order_by(Node.source_order)
        .first()
    )

    if node is None:
        raise HTTPException(status_code=404, detail="Section not found.")

    parent = db.get(Node, node.parent_id) if node.parent_id else None
    children = (
        db.query(Node)
        .filter(Node.parent_id == node.id)
        .order_by(Node.source_order)
        .all()
    )

    return {
        "document_name": document.name,
        "version_label": version.version_label,
        "section": {
            "section_number": node.section_number,
            "title": node.title,
            "level": node.level,
            "body": node.body,
            "logical_id": node.logical_id,
            "content_hash": node.content_hash,
            "page_number": node.page_number,
            "source_order": node.source_order,
            "parent": (
                {
                    "section_number": parent.section_number,
                    "title": parent.title,
                }
                if parent
                else None
            ),
            "children": [
                {
                    "section_number": child.section_number,
                    "title": child.title,
                }
                for child in children
            ],
        },
    }

