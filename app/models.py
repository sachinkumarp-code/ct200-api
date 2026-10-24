"""Simple SQLAlchemy models for the CT-200 manual."""

from sqlalchemy import (
    Column,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.orm import declarative_base, relationship

Base = declarative_base()


class Document(Base):
    """One manual, which can have multiple versions."""

    __tablename__ = "documents"

    id = Column(Integer, primary_key=True)
    name = Column(String(200), nullable=False, unique=True)

    versions = relationship(
        "Version",
        back_populates="document",
        cascade="all, delete-orphan",
    )


class Version(Base):
    """One uploaded version of a manual."""

    __tablename__ = "versions"

    id = Column(Integer, primary_key=True)
    document_id = Column(
        Integer,
        ForeignKey("documents.id"),
        nullable=False,
    )
    version_label = Column(String(100), nullable=False)
    source_filename = Column(String(255), nullable=False)
    created_at = Column(DateTime, server_default=func.now())

    document = relationship("Document", back_populates="versions")
    nodes = relationship(
        "Node",
        back_populates="version",
        cascade="all, delete-orphan",
    )

    __table_args__ = (
        UniqueConstraint(
            "document_id",
            "version_label",
            name="uq_document_version_label",
        ),
    )


class Node(Base):
    """A section or content node extracted from one manual version."""

    __tablename__ = "nodes"

    id = Column(Integer, primary_key=True)
    version_id = Column(
        Integer,
        ForeignKey("versions.id"),
        nullable=False,
    )

    # Original heading number, e.g. "2.1.1.1"; body nodes may have none.
    section_number = Column(String(100), nullable=True)

    # Heading text, such as "General Specifications".
    title = Column(String(500), nullable=False)

    # Heading depth: 1 for "2", 2 for "2.1", and so on.
    level = Column(Integer, nullable=False, default=0)

    # Extracted text for this node.
    body = Column(Text, nullable=False, default="")

    # Parent section ID; null means this is a top-level node.
    parent_id = Column(Integer, ForeignKey("nodes.id"), nullable=True)

    # Stable section path used to match corresponding nodes across versions.
    logical_id = Column(String(1000), nullable=False)

    # SHA-256 of normalized body text, used to detect content changes.
    content_hash = Column(String(64), nullable=False)

    # Order in which the node appeared in the source PDF.
    source_order = Column(Integer, nullable=False)

    # Page number is useful for debugging extraction problems.
    page_number = Column(Integer, nullable=True)

    version = relationship("Version", back_populates="nodes")
    parent = relationship("Node", remote_side=[id], backref="children")
