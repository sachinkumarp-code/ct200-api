
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
