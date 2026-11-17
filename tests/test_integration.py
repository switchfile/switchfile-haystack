import os

import pytest

from haystack_integrations.components.converters.switchfile import SwitchfileConverter

from .pdf import table_pdf

# Live calls only with a sandbox key: sandbox conversions never use credits.
pytestmark = [
    pytest.mark.integration,
    pytest.mark.skipif(
        not os.environ.get("SWITCHFILE_API_KEY", "").startswith("sf_test_"),
        reason="needs a sandbox key (sf_test_...) in SWITCHFILE_API_KEY",
    ),
]


@pytest.mark.parametrize("store", [True, False])
def test_live_conversion(tmp_path, store):
    path = tmp_path / "invoice.pdf"
    path.write_bytes(table_pdf())

    documents = SwitchfileConverter(store=store, table_documents=True, raise_on_failure=True).run(sources=[path])[
        "documents"
    ]

    main = documents[0]
    assert main.meta["file_path"] == "invoice.pdf"
    assert main.meta["page_count"] == 1
    assert main.meta["switchfile_conversion_id"]
    assert "Invoice" in main.content
    assert "Widget" in main.content
    tables = documents[1:]
    assert tables, main.content
    assert tables[0].content.startswith("| ")
    assert tables[0].meta["page_number"] == 1
