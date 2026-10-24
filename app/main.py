
"""FastAPI endpoints for ingesting CT-200 manual versions."""

from pathlib import Path
from tempfile import NamedTemporaryFile

from fastapi import Depends, FastAPI, File, Form, HTTPException, UploadFile
from sqlalchemy.orm import Session

from app.database import get_db
from app.models import Document, Node, Version
from pydantic import BaseModel
import re
from app.parser import compare_nodes, parse_pdf

app = FastAPI(title="CT-200 Manual API")


class SectionSelectionRequest(BaseModel):
    section_numbers: list[str]

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


@app.post(
    "/documents/{document_name}/versions/{version_label}/selection"
)
def select_sections(
    document_name: str,
    version_label: str,
    request: SectionSelectionRequest,
    db: Session = Depends(get_db),
):
    """Select sections from one exact manual version."""
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

    if not request.section_numbers:
        raise HTTPException(
            status_code=422,
            detail="Select at least one section.",
        )

    selected = []
    missing = []

    for number in request.section_numbers:
        node = (
            db.query(Node)
            .filter(
                Node.version_id == version.id,
                Node.section_number == number,
            )
            .order_by(Node.source_order)
            .first()
        )

        if node is None:
            missing.append(number)
            continue

        selected.append({
            "section_number": node.section_number,
            "title": node.title,
            "body": node.body,
            "logical_id": node.logical_id,
            "content_hash": node.content_hash,
            "source_order": node.source_order,
        })

    if missing:
        raise HTTPException(
            status_code=422,
            detail={
                "message": "Some sections were not found in this version.",
                "missing_sections": missing,
                "version_label": version_label,
            },
        )

    return {
        "document_name": document.name,
        "version_label": version.version_label,
        "selected_count": len(selected),
        "sections": selected,
    }

@app.get(
    "/documents/{document_name}/compare/{old_version}/{new_version}"
)
def compare_manual_versions(
    document_name: str,
    old_version: str,
    new_version: str,
    db: Session = Depends(get_db),
):
    """Compare two versions and flag potentially important changes."""
    document = (
        db.query(Document)
        .filter(Document.name == document_name)
        .first()
    )
    if document is None:
        raise HTTPException(status_code=404, detail="Document not found.")

    versions = {}
    for label in (old_version, new_version):
        version = (
            db.query(Version)
            .filter(
                Version.document_id == document.id,
                Version.version_label == label,
            )
            .first()
        )
        if version is None:
            raise HTTPException(
                status_code=404,
                detail=f"Version '{label}' not found.",
            )
        versions[label] = version

    if old_version == new_version:
        raise HTTPException(
            status_code=422,
            detail="Choose two different versions to compare.",
        )

    old_nodes = (
        db.query(Node)
        .filter(Node.version_id == versions[old_version].id)
        .order_by(Node.source_order)
        .all()
    )
    new_nodes = (
        db.query(Node)
        .filter(Node.version_id == versions[new_version].id)
        .order_by(Node.source_order)
        .all()
    )

    statuses = compare_nodes(old_nodes, new_nodes)
    old_by_id = {node.logical_id: node for node in old_nodes}
    new_by_id = {node.logical_id: node for node in new_nodes}

    # Extract numbers with optional units, such as 15%, 300 mmHg, or 1.5 seconds.
    number_pattern = re.compile(
        r"\b\d+(?:\.\d+)?\s*(?:%|mmHg|mm\s*Hg|seconds?|sec|minutes?|min|hours?|h|cycles?)?",
        re.IGNORECASE,
    )

    changes = []
    for logical_id, status in statuses.items():
        if status == "unchanged":
            continue

        old_node = old_by_id.get(logical_id)
        new_node = new_by_id.get(logical_id)

        old_text = (
            f"{old_node.title} {old_node.body}" if old_node else ""
        )
        new_text = (
            f"{new_node.title} {new_node.body}" if new_node else ""
        )

        severity = "medium"
        reason = "Section added or removed."

        if status == "changed":
            old_numbers = number_pattern.findall(old_text)
            new_numbers = number_pattern.findall(new_text)

            if old_numbers != new_numbers:
                severity = "high"
                reason = "Numeric value or unit may have changed."
            elif any(
                term in old_text.lower() or term in new_text.lower()
                for term in (
                    "warning", "caution", "overpressure",
                    "deflate", "must not", "do not",
                )
            ):
                severity = "high"
                reason = "Potential safety-related text change."
            else:
                reason = "Section content changed."

        changes.append({
            "logical_id": logical_id,
            "section_number": (
                new_node.section_number if new_node else old_node.section_number
            ),
            "title": new_node.title if new_node else old_node.title,
            "status": status,
            "severity": severity,
            "reason": reason,
            "old_content_hash": old_node.content_hash if old_node else None,
            "new_content_hash": new_node.content_hash if new_node else None,
            "old_text": old_text.strip() or None,
            "new_text": new_text.strip() or None,
        })

    return {
        "document_name": document.name,
        "old_version": old_version,
        "new_version": new_version,
        "change_count": len(changes),
        "changes": changes,
    }
