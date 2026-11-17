import copy
import json
from pathlib import Path
from unittest.mock import patch

import pytest
import requests
import responses
from haystack import Document, Pipeline
from haystack.dataclasses import ByteStream
from haystack.utils import Secret
from responses import matchers

from haystack_integrations.components.converters.switchfile import SwitchfileConverter, SwitchfileError

TEST_FILES = Path(__file__).parent / "test_files"
PDF = TEST_FILES / "report.pdf"
DOCUMENT = json.loads((TEST_FILES / "report.json").read_text())
API = "https://api.switchfile.io"
CONVERSION_ID = "0b8a7c1e-5d0f-4a51-9f39-2f7f4c1d9a10"
DOWNLOAD_URL = f"{API}/files/2a6f8e1c/report.json?expires=1&signature=abc"


def conversion(status="finished", **extra):
    body = {
        "id": CONVERSION_ID,
        "object": "conversion",
        "status": status,
        "progress": 100 if status == "finished" else 10,
        "to": "json",
        "links": {"self": f"{API}/v1/conversions/{CONVERSION_ID}"},
    }
    if status == "finished":
        body.update(
            download_url=DOWNLOAD_URL,
            credits=1,
            result={"files": [{"filename": "report.json", "size": 100, "url": DOWNLOAD_URL}], "incomplete": False},
        )
    body.update(extra)
    return body


def problem(status, code, detail):
    return {
        "type": f"https://switchfile.io/docs/api/v1#errors-{code.lower()}",
        "status": status,
        "code": code,
        "detail": detail,
    }


@pytest.fixture
def api_key(monkeypatch):
    monkeypatch.setenv("SWITCHFILE_API_KEY", "sf_test_key")


@pytest.fixture
def no_sleep():
    with patch("haystack_integrations.components.converters.switchfile.converter.sleep") as sleep:
        yield sleep


class TestInit:
    def test_defaults(self):
        converter = SwitchfileConverter()
        assert converter.api_key == Secret.from_env_var("SWITCHFILE_API_KEY")
        assert converter.api_base_url == API
        assert converter.table_documents is False
        assert converter.store is True
        assert converter.timeout == 300.0

    def test_invalid_timeout(self):
        with pytest.raises(ValueError, match="timeout"):
            SwitchfileConverter(timeout=0)

    def test_to_dict_and_from_dict(self):
        converter = SwitchfileConverter(
            api_key=Secret.from_env_var("MY_KEY"),
            api_base_url="https://example.test/",
            table_documents=True,
            pages="1-3",
            ocr_language="deu",
            store=False,
            timeout=60,
            store_full_path=True,
            raise_on_failure=True,
        )
        data = converter.to_dict()
        assert data == {
            "type": "haystack_integrations.components.converters.switchfile.converter.SwitchfileConverter",
            "init_parameters": {
                "api_key": {"type": "env_var", "env_vars": ["MY_KEY"], "strict": True},
                "api_base_url": "https://example.test",
                "table_documents": True,
                "pages": "1-3",
                "ocr_language": "deu",
                "store": False,
                "timeout": 60,
                "store_full_path": True,
                "raise_on_failure": True,
            },
        }
        restored = SwitchfileConverter.from_dict(copy.deepcopy(data))
        assert restored.to_dict() == data

    def test_token_secret_is_not_serialized(self):
        converter = SwitchfileConverter(api_key=Secret.from_token("sf_test_secret"))
        with pytest.raises(ValueError):
            converter.to_dict()

    def test_pipeline_round_trip(self):
        pipeline = Pipeline()
        pipeline.add_component("converter", SwitchfileConverter(table_documents=True))
        restored = Pipeline.loads(pipeline.dumps())
        assert restored.get_component("converter").table_documents is True

    def test_missing_key(self, monkeypatch):
        monkeypatch.delenv("SWITCHFILE_API_KEY", raising=False)
        with pytest.raises(ValueError):
            SwitchfileConverter().run(sources=[PDF])


@pytest.mark.usefixtures("api_key")
class TestRun:
    @responses.activate
    def test_converts_a_pdf(self):
        convert = responses.post(
            f"{API}/v1/convert",
            json=conversion(),
            match=[
                matchers.header_matcher({"Authorization": "Bearer sf_test_key"}),
                matchers.query_param_matcher({"wait": "30"}),
            ],
        )
        download = responses.get(DOWNLOAD_URL, json=DOCUMENT)

        result = SwitchfileConverter().run(sources=[PDF], meta={"collection": "reports"})

        assert convert.call_count == 1
        body = convert.calls[0].request.body
        assert b'name="to"\r\n\r\njson' in body
        assert b'filename="report.pdf"' in body
        assert b"options" not in body
        assert "Authorization" not in download.calls[0].request.headers

        [document] = result["documents"]
        assert isinstance(document, Document)
        pages = document.content.split("\f")
        assert len(pages) == 2
        assert pages[0].startswith("# Quarterly report")
        assert "| North | 1,234.50 € |" in pages[0]
        assert document.meta == {
            "file_path": "report.pdf",
            "collection": "reports",
            "switchfile_conversion_id": CONVERSION_ID,
            "switchfile_credits": 1,
            "page_count": 2,
            "ocr_pages": [2],
        }

    @responses.activate
    def test_sends_options(self):
        convert = responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        SwitchfileConverter(pages="1-2", ocr_language="deu+eng").run(sources=[PDF])

        body = convert.calls[0].request.body
        assert b'{"pages": "1-2", "ocr_language": "deu+eng"}' in body

    @responses.activate
    def test_bytestream_and_full_path(self):
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)
        stream = ByteStream(data=PDF.read_bytes(), meta={"file_path": "/data/in/q3.pdf", "owner": "ops"})

        [document] = SwitchfileConverter(store_full_path=True).run(sources=[stream])["documents"]

        assert document.meta["file_path"] == "/data/in/q3.pdf"
        assert document.meta["owner"] == "ops"
        assert b'filename="q3.pdf"' in responses.calls[0].request.body

    @responses.activate
    def test_bytestream_without_name(self):
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        SwitchfileConverter().run(sources=[ByteStream(data=b"%PDF-1.4", mime_type="application/pdf")])

        assert b'filename="document.pdf"' in responses.calls[0].request.body

    @responses.activate
    def test_meta_list_per_source(self):
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        documents = SwitchfileConverter().run(sources=[PDF, PDF], meta=[{"n": 1}, {"n": 2}])["documents"]

        assert [d.meta["n"] for d in documents] == [1, 2]

    @responses.activate
    def test_table_documents(self):
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        documents = SwitchfileConverter(table_documents=True).run(sources=[PDF])["documents"]

        assert len(documents) == 3
        main, first, second = documents
        assert first.content == "| Region | Revenue |\n|---|---|\n| North | 1,234.50 € |\n| South \\| East |  |"
        assert first.meta == {
            "file_path": "report.pdf",
            "source_document_id": main.id,
            "page_number": 1,
            "table_index": 0,
            "table_rows": 3,
            "table_cols": 2,
            "table_header_rows": 1,
        }
        assert second.meta["page_number"] == 2
        assert "table_header_rows" not in second.meta

    @responses.activate
    def test_polls_until_finished(self):
        responses.post(f"{API}/v1/convert", json=conversion("queued"), status=202)
        poll = responses.get(
            f"{API}/v1/conversions/{CONVERSION_ID}",
            json=conversion("processing"),
            status=202,
            match=[matchers.query_param_matcher({"wait": "30"})],
        )
        responses.get(f"{API}/v1/conversions/{CONVERSION_ID}", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        [document] = SwitchfileConverter().run(sources=[PDF])["documents"]

        assert poll.call_count == 1
        assert len([c for c in responses.calls if "/v1/conversions/" in c.request.url]) == 2
        assert document.meta["page_count"] == 2

    @responses.activate
    def test_incomplete_result(self):
        responses.post(
            f"{API}/v1/convert",
            json=conversion(
                credits=0,
                result={"files": [{"url": DOWNLOAD_URL}], "incomplete": True, "notes": ["Page 7 is missing."]},
            ),
        )
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        [document] = SwitchfileConverter().run(sources=[PDF])["documents"]

        assert document.meta["switchfile_incomplete"] is True
        assert document.meta["switchfile_notes"] == ["Page 7 is missing."]
        assert document.meta["switchfile_credits"] == 0

    @responses.activate
    def test_failed_conversion_is_skipped(self, caplog):
        responses.post(
            f"{API}/v1/convert",
            json=conversion("failed", error={"code": "PASSWORD_PROTECTED", "message": "The file needs a password."}),
        )

        result = SwitchfileConverter().run(sources=[PDF])

        assert result == {"documents": []}
        assert "PASSWORD_PROTECTED" in caplog.text

    @responses.activate
    def test_failed_conversion_raises_on_request(self):
        responses.post(
            f"{API}/v1/convert",
            json=conversion("failed", error={"code": "INVALID_INPUT", "message": "The input file could not be read."}),
        )

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "INVALID_INPUT"

    @responses.activate
    def test_request_error_is_skipped_and_next_file_converts(self):
        responses.post(
            f"{API}/v1/convert",
            json=problem(422, "UNSUPPORTED_PAIR", "Converting zip to json is not supported."),
            status=422,
        )
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        documents = SwitchfileConverter().run(sources=[PDF, PDF])["documents"]

        assert len(documents) == 1

    @pytest.mark.parametrize(("status", "code"), [(401, "UNAUTHENTICATED"), (402, "CREDITS_EXCEEDED")])
    @responses.activate
    def test_account_errors_always_raise(self, status, code):
        responses.post(f"{API}/v1/convert", json=problem(status, code, "No."), status=status)

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter().run(sources=[PDF])
        assert excinfo.value.code == code
        assert excinfo.value.status == status
        assert "sf_test_key" not in str(excinfo.value)

    @responses.activate
    def test_error_without_json_body(self):
        responses.post(f"{API}/v1/convert", body="<html>bad gateway</html>", status=502)

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "HTTP_502"

    @responses.activate
    def test_retries_after_rate_limit(self, no_sleep):
        responses.post(
            f"{API}/v1/convert",
            json=problem(429, "RATE_LIMIT_EXCEEDED", "Too many requests."),
            status=429,
            headers={"Retry-After": "3"},
        )
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)

        documents = SwitchfileConverter().run(sources=[PDF])["documents"]

        assert len(documents) == 1
        no_sleep.assert_called_once_with(3.0)

    @responses.activate
    def test_gives_up_after_retries(self, no_sleep):
        responses.post(f"{API}/v1/convert", json=problem(503, "SERVER_BUSY", "Busy."), status=503)

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "SERVER_BUSY"
        assert no_sleep.call_count == 3

    @responses.activate
    def test_timeout_while_polling(self):
        responses.post(f"{API}/v1/convert", json=conversion("processing"), status=202)
        with patch(
            "haystack_integrations.components.converters.switchfile.converter.monotonic",
            side_effect=[0.0, 0.0, 0.0, 999.0],
        ):
            with pytest.raises(SwitchfileError) as excinfo:
                SwitchfileConverter(timeout=10, raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "TIMEOUT"

    @responses.activate
    def test_network_timeout(self):
        responses.post(f"{API}/v1/convert", body=requests.ReadTimeout("slow"))

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "TIMEOUT"

    @responses.activate
    def test_connection_error(self):
        responses.post(f"{API}/v1/convert", body=requests.ConnectionError("refused"))

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "CONNECTION_ERROR"

    @responses.activate
    def test_result_that_is_not_a_document(self):
        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json={"hello": "world"})

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "INVALID_RESPONSE"

    def test_unreadable_source_is_skipped(self, tmp_path):
        assert SwitchfileConverter().run(sources=[tmp_path / "missing.pdf"]) == {"documents": []}

    @responses.activate
    def test_without_storage(self):
        convert = responses.post(
            f"{API}/v1/convert",
            json=DOCUMENT,
            headers={
                "X-Conversion-Id": CONVERSION_ID,
                "X-Credits": "1",
                "X-Incomplete": "1",
                "X-Notes": json.dumps(["Page 3 was added as an image without text."]),
            },
            match=[matchers.query_param_matcher({"store": "false"})],
        )

        [document] = SwitchfileConverter(store=False).run(sources=[PDF])["documents"]

        assert convert.call_count == 1
        assert len(responses.calls) == 1
        assert document.meta["switchfile_conversion_id"] == CONVERSION_ID
        assert document.meta["switchfile_credits"] == 1
        assert document.meta["switchfile_incomplete"] is True
        assert document.meta["switchfile_notes"] == ["Page 3 was added as an image without text."]

    @responses.activate
    def test_without_storage_error(self):
        responses.post(
            f"{API}/v1/convert", json=problem(413, "FILE_TOO_LARGE", "The file is larger than 50 MB."), status=413
        )

        with pytest.raises(SwitchfileError) as excinfo:
            SwitchfileConverter(store=False, raise_on_failure=True).run(sources=[PDF])
        assert excinfo.value.code == "FILE_TOO_LARGE"
        assert excinfo.value.message == "The file is larger than 50 MB."

    @responses.activate
    def test_in_a_pipeline(self):
        from haystack.components.preprocessors import DocumentSplitter

        responses.post(f"{API}/v1/convert", json=conversion())
        responses.get(DOWNLOAD_URL, json=DOCUMENT)
        pipeline = Pipeline()
        pipeline.add_component("converter", SwitchfileConverter())
        pipeline.add_component("splitter", DocumentSplitter(split_by="page", split_length=1))
        pipeline.connect("converter", "splitter")

        result = pipeline.run({"converter": {"sources": [PDF]}})

        chunks = result["splitter"]["documents"]
        assert [c.meta["page_number"] for c in chunks] == [1, 2]
