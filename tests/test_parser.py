"""Unit tests for the CT-200 PDF parser helpers."""

from app.parser import (
    ParsedNode,
    TextLine,
    build_nodes,
    compare_nodes,
    content_hash,
    parse_table_rows,
)


def make_line(text, order, size=13, bold=True, page=1):
    """Make a test line without needing a real PDF."""
    return TextLine(
        text=text,
        font_size=size,
        bold=bold,
        page_number=page,
        source_order=order,
    )


def test_skipped_heading_level_attaches_to_nearest_existing_ancestor():
    """2.1.1.1 should attach to 2.1 if 2.1.1 is missing."""

    result = build_nodes([
        make_line("2 General", 0),
        make_line("2.1 General Specifications", 1),
        make_line("2.1.1.1 Battery Life Under Typical Use", 2, size=11),
        make_line("The device supports 250 cycles.", 3, bold=False, size=11),
    ])

    battery = next(
        node for node in result.nodes
        if node.section_number == "2.1.1.1"
    )

    assert battery.parent_section == "2.1"
    assert any("skips a heading level" in warning for warning in result.warnings)
    assert "250 cycles" in battery.body


def test_duplicate_heading_titles_have_distinct_logical_ids():
    """Two 'Error Codes' headings under different sections must stay distinct."""

    result = build_nodes([
        make_line("4 Troubleshooting", 0),
        make_line("4.2 Error Codes", 1),
        make_line("5 Maintenance", 2),
        make_line("7 Reference", 3),
        make_line("7.1 Error Codes", 4),
    ])

    error_sections = [
        node for node in result.nodes
        if node.title == "Error Codes"
    ]

    assert len(error_sections) == 2
    assert {node.logical_id for node in error_sections} == {"4.2", "7.1"}
    assert {node.parent_section for node in error_sections} == {"4", "7"}


def test_out_of_order_subsections_keep_source_order_and_parent():
    """Section 3.4 may appear before 3.3; don't reorder the source."""

    result = build_nodes([
        make_line("3 Measurement", 0),
        make_line("3.4 Auto Shutoff", 1),
        make_line("3.3 Result Display and Classification", 2),
    ])

    subsection_numbers = [
        node.section_number for node in result.nodes
        if node.section_number in {"3.3", "3.4"}
    ]

    assert subsection_numbers == ["3.4", "3.3"]

    auto_shutoff = next(
        node for node in result.nodes
        if node.section_number == "3.4"
    )
    assert auto_shutoff.parent_section == "3"


def test_table_rows_preserve_e3_columns():
    """Table cleanup should preserve the E3 code, meaning, and behaviour."""

    rows = parse_table_rows([
        ["Code", "Meaning", "Device Behavior"],
        ["E3", "Pressure sensor fault", "Deflates within 1.5 seconds"],
    ])

    assert rows[1] == [
        "E3",
        "Pressure sensor fault",
        "Deflates within 1.5 seconds",
    ]
    assert rows[0][0] == "Code"
    assert rows[0][1] == "Meaning"
    assert rows[0][2] == "Device Behavior"


def test_version_comparison_detects_change_without_duplicate_logical_nodes():
    """Changed text should be flagged while the section keeps its logical ID."""

    old_nodes = [
        ParsedNode(
            section_number="2.1.1.1",
            title="Battery Life Under Typical Use",
            level=4,
            logical_id="2.1.1.1",
            body="The device supports 300 cycles.",
            content_hash=content_hash("The device supports 300 cycles."),
        )
    ]

    new_nodes = [
        ParsedNode(
            section_number="2.1.1.1",
            title="Battery Life Under Typical Use",
            level=4,
            logical_id="2.1.1.1",
            body="The device supports 250 cycles.",
            content_hash=content_hash("The device supports 250 cycles."),
        )
    ]

    statuses = compare_nodes(old_nodes, new_nodes)

    assert statuses == {"2.1.1.1": "changed"}
    assert len({node.logical_id for node in new_nodes}) == len(new_nodes)
