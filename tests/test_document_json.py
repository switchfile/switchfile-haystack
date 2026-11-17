import json
from pathlib import Path

from haystack_integrations.components.converters.switchfile.document_json import (
    block_to_markdown,
    iter_tables,
    page_to_markdown,
    table_to_markdown,
)

DOCUMENT = json.loads((Path(__file__).parent / "test_files" / "report.json").read_text())


def test_page_renders_blocks_in_reading_order_without_header_and_footer():
    markdown = page_to_markdown(DOCUMENT["pages"][0])
    assert markdown == (
        "# Quarterly report\n\n"
        "Revenue grew in every region.\n\n"
        "| Region | Revenue |\n"
        "|---|---|\n"
        "| North | 1,234.50 € |\n"
        "| South \\| East |  |"
    )
    assert "confidential" not in markdown
    assert "Page 1" not in markdown


def test_list_heading_and_merged_cells():
    markdown = page_to_markdown(DOCUMENT["pages"][1])
    assert markdown.startswith("## Next steps\n\n- Hire\n  1. Sales\n\n")
    assert "| Q |  | Total |\n|---|---|---|\n| Q1 | Q2 | 42 |" in markdown


def test_table_without_size_is_empty():
    assert table_to_markdown({"type": "table", "rows": 0, "cols": 0, "cells": []}) == ""


def test_cells_outside_the_table_are_ignored():
    table = {"rows": 1, "cols": 1, "cells": [{"row": 0, "col": 0, "text": "a\nb"}, {"row": 5, "col": 0, "text": "x"}]}
    assert table_to_markdown(table) == "| a b |\n|---|"


def test_unknown_block_type_falls_back_to_text():
    assert block_to_markdown({"type": "caption", "text": " Figure 1 "}) == "Figure 1"


def test_heading_level_is_clamped():
    assert block_to_markdown({"type": "heading", "level": 9, "text": "Deep"}) == "###### Deep"


def test_iter_tables_keeps_the_page():
    tables = iter_tables(DOCUMENT)
    assert [(page["number"], table["cols"]) for page, table in tables] == [(1, 2), (2, 3)]
