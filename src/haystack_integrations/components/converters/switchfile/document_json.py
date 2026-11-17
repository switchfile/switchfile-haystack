# Renders the switchfile Document JSON (schema "switchfile.document", version 1.x) as Markdown.
# Field reference: https://switchfile.io/docs/document-json
from typing import Any

# Running headers and footers repeat on every page and add noise to retrieval, the same reason the
# Markdown output of the API leaves them out.
_SKIPPED_BLOCKS = {"header", "footer", "image"}


def _escape_cell(text: str) -> str:
    return " ".join(text.replace("|", "\\|").split())


def table_to_markdown(table: dict[str, Any]) -> str:
    """
    Render a table block as a Markdown pipe table.

    The first row becomes the header row, because a pipe table always needs one. A merged cell keeps its text in its
    top-left position; the positions it spans stay empty.

    :param table: A block with `"type": "table"`.
    :returns: The table as Markdown, or an empty string for a table without rows or columns.
    """
    rows = int(table.get("rows") or 0)
    cols = int(table.get("cols") or 0)
    if rows <= 0 or cols <= 0:
        return ""
    grid = [["" for _ in range(cols)] for _ in range(rows)]
    for cell in table.get("cells") or []:
        row, col = cell.get("row"), cell.get("col")
        if isinstance(row, int) and isinstance(col, int) and 0 <= row < rows and 0 <= col < cols:
            grid[row][col] = _escape_cell(str(cell.get("text") or ""))
    lines = ["| " + " | ".join(grid[0]) + " |", "|" + "|".join(["---"] * cols) + "|"]
    lines.extend("| " + " | ".join(row) + " |" for row in grid[1:])
    return "\n".join(lines)


def block_to_markdown(block: dict[str, Any]) -> str:
    """
    Render one block of a page as Markdown.

    :param block: A block from `pages[].blocks`.
    :returns: The block as Markdown, or an empty string for blocks without text.
    """
    kind = block.get("type")
    if kind in _SKIPPED_BLOCKS:
        return ""
    if kind == "table":
        return table_to_markdown(block)
    if kind == "heading":
        level = min(max(int(block.get("level") or 1), 1), 6)
        return "#" * level + " " + " ".join(str(block.get("text") or "").split())
    if kind == "list":
        lines = []
        for item in block.get("items") or []:
            indent = "  " * max(int(item.get("level") or 0), 0)
            marker = item.get("marker") or "-"
            lines.append(f"{indent}{marker} {item.get('text') or ''}".rstrip())
        return "\n".join(lines)
    # paragraph, footnote and any block type added in a later 1.x version
    return str(block.get("text") or "").strip()


def page_to_markdown(page: dict[str, Any]) -> str:
    """
    Render one page of the Document JSON as Markdown.

    :param page: An entry of `pages`.
    :returns: The blocks of the page in reading order, separated by blank lines.
    """
    parts = (block_to_markdown(block) for block in page.get("blocks") or [])
    return "\n\n".join(part for part in parts if part)


def iter_tables(document: dict[str, Any]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """
    List every table of the document together with the page it stands on.

    :param document: The parsed Document JSON.
    :returns: Pairs of (page, table block) in reading order.
    """
    return [
        (page, block)
        for page in document.get("pages") or []
        for block in page.get("blocks") or []
        if block.get("type") == "table"
    ]
