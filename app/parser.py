
"""Basic PDF extraction and section matching for the CT-200 manual."""

from dataclasses import dataclass, field
from hashlib import sha256
import re
import unicodedata
from typing import Optional

import pymupdf


# Accept headings such as "2 General", "2. General", and "2.1 Specifications".
HEADING_RE = re.compile(
    r"^\s*(\d+(?:\.\d+)*)(?:\.)?\s+(.+?)\s*$"
)


@dataclass
class TextLine:
    """One line of PDF text and its formatting clues."""

    text: str
    font_size: float
    bold: bool
    page_number: int
    source_order: int


@dataclass
class ParsedNode:
    """A section found in the PDF."""

    section_number: str
    title: str
    level: int
    logical_id: str
    body: str = ""
    parent_section: Optional[str] = None
    source_order: int = 0
    page_number: int = 1
    content_hash: str = ""


@dataclass
class ParseResult:
    """Extracted sections, tables, and warnings."""

    nodes: list[ParsedNode] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    tables: list[list[list[str]]] = field(default_factory=list)


def normalize_text(text: str) -> str:
    """Normalize ligatures and whitespace without deleting punctuation."""

    # Converts PDF ligatures such as "ﬁ" to ordinary "fi".
    text = unicodedata.normalize("NFKC", text)
    return " ".join(text.split()).strip()


def content_hash(text: str) -> str:
    """Create a stable SHA-256 hash from normalized text."""

    normalized = normalize_text(text)
    return sha256(normalized.encode("utf-8")).hexdigest()


def parse_table_rows(rows: list[list[object]]) -> list[list[str]]:
    """Clean table cells while keeping row and column order."""

    return [
        [
            normalize_text("" if cell is None else str(cell))
            for cell in row
        ]
        for row in rows
    ]


def classify_heading(line: TextLine) -> Optional[tuple[str, str]]:
    """Return (section number, title) when a line looks like a heading."""

    text = normalize_text(line.text)
    match = HEADING_RE.match(text)

    if not match:
        return None

    # Numbering alone is insufficient: numbered body lists can look similar.
    if not line.bold and line.font_size < 12:
        return None

    number = match.group(1)
    title = normalize_text(match.group(2))
    return number, title


def build_nodes(lines: list[TextLine]) -> ParseResult:
    """Build sections in source order and find their nearest parents."""

    result = ParseResult()
    current_node = None
    nodes_by_number = {}

    for line in lines:
        text = normalize_text(line.text)
        if not text:
            continue

        heading = classify_heading(line)

        if not heading:
            # Ordinary text belongs to the most recently detected section.
            if current_node is not None:
                current_node.body = (
                    f"{current_node.body}\n{text}".strip()
                )
            continue

        number, title = heading
        level = number.count(".") + 1
        parts = number.split(".")
        parent_section = None

        # Search for the closest earlier numbered ancestor.
        for length in range(len(parts) - 1, 0, -1):
            candidate = ".".join(parts[:length])
            if candidate in nodes_by_number:
                parent_section = candidate
                break

        if level > 1 and parent_section is None:
            result.warnings.append(
                f"Section {number} has no numbered ancestor in the document."
            )
        elif parent_section is not None:
            parent_level = parent_section.count(".") + 1

            if parent_level < level - 1:
                result.warnings.append(
                    f"Section {number} skips a heading level; "
                    f"attached to {parent_section}."
                )

        node = ParsedNode(
            section_number=number,
            title=title,
            level=level,
            logical_id=number,
            parent_section=parent_section,
            source_order=line.source_order,
            page_number=line.page_number,
        )
        result.nodes.append(node)
        current_node = node

        if number in nodes_by_number:
            result.warnings.append(
                f"Repeated section number {number}; review the source PDF."
            )
        else:
            nodes_by_number[number] = node

    # Hash extracted body text for later version comparisons.
    for node in result.nodes:
        node.content_hash = content_hash(node.body)

    return result


def extract_pdf_lines(
    pdf_path: str,
) -> tuple[list[TextLine], list[list[list[str]]]]:
    """Extract text lines and tables directly from a PDF."""

    lines = []
    tables = []
    source_order = 0

    with pymupdf.open(pdf_path) as pdf:
        for page_index, page in enumerate(pdf):
            page_number = page_index + 1
            page_dict = page.get_text("dict")

            # Rebuild each line from its text spans.
            for block in page_dict["blocks"]:
                if "lines" not in block:
                    continue

                for pdf_line in block["lines"]:
                    spans = pdf_line.get("spans", [])
                    raw_text = "".join(
                        span.get("text", "") for span in spans
                    )
                    text = normalize_text(raw_text)

                    if not text:
                        continue

                    font_size = max(
                        (span.get("size", 0) for span in spans),
                        default=0,
                    )
                    bold = any(
                        "bold" in span.get("font", "").lower()
                        for span in spans
                    )

                    lines.append(
                        TextLine(
                            text=text,
                            font_size=font_size,
                            bold=bold,
                            page_number=page_number,
                            source_order=source_order,
                        )
                    )
                    source_order += 1

            # Extract tables when the installed PyMuPDF supports detection.
            try:
                for table in page.find_tables().tables:
                    tables.append(parse_table_rows(table.extract()))
            except (AttributeError, RuntimeError, ValueError) as error:
                result_warning = (
                    f"Table extraction issue on page {page_number}: {error}"
                )
                # Preserve the issue so it can be reviewed.
                tables.append([[result_warning]])

    return lines, tables


def parse_pdf(pdf_path: str) -> ParseResult:
    """Parse a PDF into sections, tables, and warnings."""

    lines, tables = extract_pdf_lines(pdf_path)
    result = build_nodes(lines)
    result.tables = tables

    if not tables:
        result.warnings.append(
            "No tables were extracted; check the PDF table layout manually."
        )

    if not result.nodes:
        result.warnings.append(
            "No numbered headings were detected; review PDF formatting rules."
        )

    return result


def compare_nodes(
    old_nodes: list[ParsedNode],
    new_nodes: list[ParsedNode],
) -> dict[str, str]:
    """Compare versions by logical ID and normalized body hash."""

    old_by_id = {node.logical_id: node for node in old_nodes}
    new_by_id = {node.logical_id: node for node in new_nodes}
    statuses = {}

    for logical_id, new_node in new_by_id.items():
        if logical_id not in old_by_id:
            statuses[logical_id] = "added"
        elif old_by_id[logical_id].content_hash != new_node.content_hash:
            statuses[logical_id] = "changed"
        else:
            statuses[logical_id] = "unchanged"

    for logical_id in old_by_id.keys() - new_by_id.keys():
        statuses[logical_id] = "removed"

    return statuses
