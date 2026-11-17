import json
import os
import re
from importlib.metadata import PackageNotFoundError, version
from pathlib import Path
from time import monotonic, sleep
from typing import Any

import requests
from haystack import Document, component, default_from_dict, default_to_dict, logging
from haystack.components.converters.utils import get_bytestream_from_source, normalize_metadata
from haystack.dataclasses import ByteStream
from haystack.utils import Secret, deserialize_secrets_inplace

from haystack_integrations.components.converters.switchfile.document_json import (
    iter_tables,
    page_to_markdown,
    table_to_markdown,
)
from haystack_integrations.components.converters.switchfile.errors import SwitchfileError

logger = logging.getLogger(__name__)

DEFAULT_API_BASE_URL = "https://api.switchfile.io"

# Errors that hit every following file in the same way, so skipping the file would only hide them.
_ACCOUNT_ERRORS = {"UNAUTHENTICATED", "CREDITS_EXCEEDED", "END_USER_NOT_ALLOWED", "STORAGE_DISABLED"}
_RETRY_STATUSES = {429, 503}
_MAX_RETRIES = 3
_MAX_WAIT = 30  # upper limit of ?wait in the simple API
_CONNECT_TIMEOUT = 10.0
_PAGE_MARK = re.compile(r"^<!-- page \d+ -->$", re.MULTILINE)

try:
    _USER_AGENT = f"switchfile-haystack/{version('switchfile-haystack')}"
except PackageNotFoundError:  # running from a source checkout
    _USER_AGENT = "switchfile-haystack"


@component
class SwitchfileConverter:
    """
    Converts PDF files to Markdown documents with the switchfile API.

    Each source becomes one `Document` whose content is the Markdown that the API returns for `to=md`: headings,
    paragraphs, lists and tables as pipe tables, in reading order, with a `<!-- page N -->` line where each page starts.

    With `split_pages=True` or `table_documents=True`, the component requests the structured `json` result instead and
    renders the Markdown from it. Pages are then separated by a form feed (`\\f`) for
    `DocumentSplitter(split_by="page")`, and `table_documents=True` also returns every table as a document of its own.
    Either way, each file is converted once.

    Word, Excel and PowerPoint files work the same way.

    ### Usage example

    ```python
    from haystack_integrations.components.converters.switchfile import SwitchfileConverter

    converter = SwitchfileConverter()  # reads SWITCHFILE_API_KEY
    documents = converter.run(sources=["report.pdf"])["documents"]
    print(documents[0].content)
    ```
    """

    def __init__(
        self,
        *,
        api_key: Secret = Secret.from_env_var("SWITCHFILE_API_KEY"),
        api_base_url: str = DEFAULT_API_BASE_URL,
        split_pages: bool = False,
        table_documents: bool = False,
        pages: str | None = None,
        ocr_language: str | None = None,
        store: bool = True,
        timeout: float = 300.0,
        store_full_path: bool = False,
        raise_on_failure: bool = False,
    ) -> None:
        """
        Create a SwitchfileConverter.

        :param api_key:
            switchfile API key. By default it is read from the `SWITCHFILE_API_KEY` environment variable.
            Sandbox keys (`sf_test_...`) never use credits.
        :param api_base_url:
            Base URL of the API.
        :param split_pages:
            If `True`, pages are separated by a form feed (`\\f`) so that `DocumentSplitter(split_by="page")` keeps
            page numbers. The component then requests the `json` result and renders the Markdown from it, which can
            differ in detail from the `md` result.
        :param table_documents:
            If `True`, every table is also returned as a separate `Document` with its page number in the metadata,
            in addition to the document of the whole file. Like `split_pages`, this uses the `json` result and
            renders the Markdown from it, with pages separated by a form feed.
        :param pages:
            Pages to convert, for example `"1-3,5"`. All pages by default.
        :param ocr_language:
            Language of scanned pages, for example `"eng"`, `"deu"` or `"deu+eng"`. Detected by default.
        :param store:
            If `False`, the file and the result are not stored by switchfile. The result comes back in the
            response of a single request. This mode takes files up to 50 MB and runs at most two requests per
            account at the same time; set `timeout` to at least 390 seconds for large files.
            See https://switchfile.io/docs/no-store.
        :param timeout:
            Seconds to wait for one file, including upload, conversion and download.
        :param store_full_path:
            If `True`, the full path of the file is stored in the `file_path` metadata. If `False`, only the file
            name is stored.
        :param raise_on_failure:
            If `True`, a file that cannot be converted raises a `SwitchfileError`. If `False`, the file is skipped
            with a warning. Errors of the account, such as an invalid API key or missing credits, always raise.
        """
        if timeout <= 0:
            msg = "timeout must be greater than 0"
            raise ValueError(msg)
        self.api_key = api_key
        self.api_base_url = api_base_url.rstrip("/")
        self.split_pages = split_pages
        self.table_documents = table_documents
        self.pages = pages
        self.ocr_language = ocr_language
        self.store = store
        self.timeout = timeout
        self.store_full_path = store_full_path
        self.raise_on_failure = raise_on_failure

    def to_dict(self) -> dict[str, Any]:
        """
        Serializes the component to a dictionary.

        :returns:
            Dictionary with serialized data.
        """
        return default_to_dict(
            self,
            api_key=self.api_key.to_dict(),
            api_base_url=self.api_base_url,
            split_pages=self.split_pages,
            table_documents=self.table_documents,
            pages=self.pages,
            ocr_language=self.ocr_language,
            store=self.store,
            timeout=self.timeout,
            store_full_path=self.store_full_path,
            raise_on_failure=self.raise_on_failure,
        )

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "SwitchfileConverter":
        """
        Deserializes the component from a dictionary.

        :param data:
            Dictionary to deserialize from.
        :returns:
            Deserialized component.
        """
        deserialize_secrets_inplace(data["init_parameters"], keys=["api_key"])
        return default_from_dict(cls, data)

    @component.output_types(documents=list[Document])
    def run(
        self,
        sources: list[str | Path | ByteStream],
        meta: dict[str, Any] | list[dict[str, Any]] | None = None,
    ) -> dict[str, list[Document]]:
        """
        Converts files to Markdown documents.

        :param sources:
            File paths or `ByteStream` objects.
        :param meta:
            Metadata to attach to the documents. A single dictionary is added to every document; a list must have
            one dictionary per source. The `meta` of a `ByteStream` is added as well.
        :returns:
            A dictionary with the following key:
            - `documents`: One document per converted file, followed by one document per table if
              `table_documents` is `True`.
        :raises SwitchfileError:
            For errors of the account, and for any failed file if `raise_on_failure` is `True`.
        """
        api_key = self.api_key.resolve_value()
        if not api_key:
            msg = "No switchfile API key. Set SWITCHFILE_API_KEY or pass api_key."
            raise ValueError(msg)

        documents: list[Document] = []
        meta_list = normalize_metadata(meta, sources_count=len(sources))
        with requests.Session() as session:
            for source, metadata in zip(sources, meta_list, strict=True):
                try:
                    bytestream = get_bytestream_from_source(source)
                except Exception as e:
                    if self.raise_on_failure:
                        raise
                    logger.warning("Could not read {source}. Skipping it. Error: {error}", source=source, error=e)
                    continue

                try:
                    result, info = self._convert(session, api_key, bytestream)
                except SwitchfileError as e:
                    if self.raise_on_failure or e.code in _ACCOUNT_ERRORS:
                        raise
                    logger.warning("Could not convert {source}. Skipping it. {error}", source=source, error=e)
                    continue

                documents.extend(self._to_documents(result, info, {**bytestream.meta, **metadata}))
        return {"documents": documents}

    @property
    def _use_json(self) -> bool:
        return self.split_pages or self.table_documents

    def _to_documents(
        self, result: dict[str, Any] | str, info: dict[str, Any], merged_meta: dict[str, Any]
    ) -> list[Document]:
        if not self.store_full_path and (file_path := merged_meta.get("file_path")):
            merged_meta["file_path"] = os.path.basename(file_path)

        if isinstance(result, str):
            meta = {**merged_meta, **info, "page_count": len(_PAGE_MARK.findall(result))}
            if not _PAGE_MARK.sub("", result).strip():
                logger.warning("No text found in {file}. Returning an empty document.", file=meta.get("file_path"))
            return [Document(content=result, meta=meta)]

        document_json = result
        pages = document_json.get("pages") or []
        meta = {**merged_meta, **info, "page_count": len(pages)}
        ocr_pages = [page.get("number") for page in pages if page.get("ocr")]
        if ocr_pages:
            meta["ocr_pages"] = ocr_pages
        content = "\f".join(page_to_markdown(page) for page in pages)
        if not content.strip():
            logger.warning("No text found in {file}. Returning an empty document.", file=meta.get("file_path"))
        document = Document(content=content, meta=meta)
        documents = [document]

        if self.table_documents:
            for index, (page, table) in enumerate(iter_tables(document_json)):
                table_meta = {
                    **merged_meta,
                    "source_document_id": document.id,
                    "page_number": page.get("number"),
                    "table_index": index,
                    "table_rows": table.get("rows"),
                    "table_cols": table.get("cols"),
                }
                if table.get("header_rows") is not None:
                    table_meta["table_header_rows"] = table["header_rows"]
                documents.append(Document(content=table_to_markdown(table), meta=table_meta))
        return documents

    def _convert(
        self, session: requests.Session, api_key: str, bytestream: ByteStream
    ) -> tuple[dict[str, Any] | str, dict[str, Any]]:
        deadline = monotonic() + self.timeout
        filename = os.path.basename(str(bytestream.meta.get("file_path") or "")) or "document.pdf"
        mime_type = bytestream.mime_type or (
            "application/pdf" if filename.lower().endswith(".pdf") else "application/octet-stream"
        )
        parse = _parse_document if self._use_json else _parse_markdown
        form: dict[str, str] = {"to": "json" if self._use_json else "md"}
        options = {k: v for k, v in (("pages", self.pages), ("ocr_language", self.ocr_language)) if v}
        if options:
            form["options"] = json.dumps(options)
        files = {"file": (filename, bytestream.data, mime_type)}
        auth = {"Authorization": f"Bearer {api_key}"}

        if not self.store:
            response = self._request(
                session,
                "POST",
                "/v1/convert",
                deadline,
                headers=auth,
                params={"store": "false"},
                data=form,
                files=files,
            )
            notes = response.headers.get("X-Notes")
            info = _info(
                conversion_id=response.headers.get("X-Conversion-Id"),
                credits_used=response.headers.get("X-Credits"),
                incomplete=response.headers.get("X-Incomplete") == "1",
                notes=_parse_notes(notes),
            )
            return parse(response), info

        response = self._request(
            session,
            "POST",
            "/v1/convert",
            deadline,
            headers=auth,
            params={"wait": _wait(deadline)},
            data=form,
            files=files,
        )
        conversion = response.json()
        while conversion.get("status") in ("queued", "processing"):
            if monotonic() >= deadline:
                msg = f"Conversion {conversion.get('id')} did not finish within {self.timeout:g} seconds"
                raise SwitchfileError("TIMEOUT", msg)
            response = self._request(
                session,
                "GET",
                f"/v1/conversions/{conversion['id']}",
                deadline,
                headers=auth,
                params={"wait": _wait(deadline)},
            )
            conversion = response.json()

        if conversion.get("status") != "finished":
            error = conversion.get("error") or {}
            raise SwitchfileError(
                error.get("code") or "CONVERSION_FAILED",
                error.get("message") or "The file could not be converted",
                details=conversion,
            )

        result = conversion.get("result") or {}
        files_out = result.get("files") or []
        url = conversion.get("download_url") or (files_out[0].get("url") if files_out else None)
        if not url:
            raise SwitchfileError("INVALID_RESPONSE", "The finished conversion has no download URL", details=conversion)
        # The download URL is signed, so the API key is not sent along.
        download = self._request(session, "GET", url, deadline)
        info = _info(
            conversion_id=conversion.get("id"),
            credits_used=conversion.get("credits"),
            incomplete=bool(result.get("incomplete")),
            notes=result.get("notes") or [],
        )
        return parse(download), info

    def _request(
        self, session: requests.Session, method: str, path_or_url: str, deadline: float, **kwargs: Any
    ) -> requests.Response:
        url = path_or_url if path_or_url.startswith("http") else self.api_base_url + path_or_url
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json", **kwargs.pop("headers", {})}
        for attempt in range(_MAX_RETRIES + 1):
            remaining = deadline - monotonic()
            # Leave room for the server to answer after its own ?wait has passed.
            read_timeout = max(remaining, 0) + _MAX_WAIT
            try:
                response = session.request(
                    method, url, headers=headers, timeout=(_CONNECT_TIMEOUT, read_timeout), **kwargs
                )
            except requests.Timeout as e:
                msg = f"No answer from the switchfile API within {self.timeout:g} seconds"
                raise SwitchfileError("TIMEOUT", msg) from e
            except requests.RequestException as e:
                msg = f"Could not reach the switchfile API: {e}"
                raise SwitchfileError("CONNECTION_ERROR", msg) from e

            if response.status_code in _RETRY_STATUSES and attempt < _MAX_RETRIES:
                delay = _retry_after(response)
                if monotonic() + delay < deadline:
                    logger.debug(
                        "switchfile answered {status}, retrying in {delay} s", status=response.status_code, delay=delay
                    )
                    sleep(delay)
                    continue
            if response.status_code >= 400:
                raise _error_from_response(response)
            return response
        raise _error_from_response(response)  # pragma: no cover


def _wait(deadline: float) -> int:
    return int(min(_MAX_WAIT, max(1, deadline - monotonic())))


def _retry_after(response: requests.Response) -> float:
    try:
        return max(float(response.headers.get("Retry-After", "2")), 0.0)
    except ValueError:
        return 2.0


def _error_from_response(response: requests.Response) -> SwitchfileError:
    try:
        body = response.json()
    except ValueError:
        body = None
    if isinstance(body, dict):
        code = body.get("code") or f"HTTP_{response.status_code}"
        message = body.get("detail") or body.get("message") or body.get("title") or response.reason
        return SwitchfileError(str(code), str(message), status=response.status_code, details=body)
    return SwitchfileError(
        f"HTTP_{response.status_code}", response.reason or "Request failed", status=response.status_code
    )


def _parse_document(response: requests.Response) -> dict[str, Any]:
    try:
        document = response.json()
    except ValueError as e:
        raise SwitchfileError("INVALID_RESPONSE", "The result is not valid JSON") from e
    if not isinstance(document, dict) or not isinstance(document.get("pages"), list):
        raise SwitchfileError("INVALID_RESPONSE", "The result is not a switchfile document", details=document)
    return document


def _parse_markdown(response: requests.Response) -> str:
    try:
        return response.content.decode("utf-8")
    except UnicodeDecodeError as e:
        raise SwitchfileError("INVALID_RESPONSE", "The result is not UTF-8 Markdown") from e


def _parse_notes(header: str | None) -> list[str]:
    if not header:
        return []
    try:
        notes = json.loads(header)
    except ValueError:
        return [header]
    return [str(note) for note in notes] if isinstance(notes, list) else [str(notes)]


def _info(conversion_id: Any, credits_used: Any, incomplete: bool, notes: list[str]) -> dict[str, Any]:
    info: dict[str, Any] = {"switchfile_conversion_id": conversion_id}
    if credits_used is not None:
        try:
            info["switchfile_credits"] = int(credits_used)
        except (TypeError, ValueError):
            pass
    if incomplete:
        info["switchfile_incomplete"] = True
    if notes:
        info["switchfile_notes"] = notes
    return info
