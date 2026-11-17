# switchfile-haystack

[Haystack](https://haystack.deepset.ai) converter for [switchfile](https://switchfile.io): turn PDFs into Markdown
documents for your pipelines and agents. Tables arrive as Markdown tables, in reading order with the text around them,
and can also be returned as documents of their own.

- **`SwitchfileConverter`** takes file paths or `ByteStream` objects and returns one `Document` per file.
- Scanned pages go through text recognition.
- Word, Excel and PowerPoint files work the same way.

In the open [pdf-table-bench](https://github.com/switchfile/pdf-table-bench) benchmark, 97.2 % of the number cells
in switchfile's output are exact, summed over all test PDFs. The documents, every output and the scoring code are public, so you can check the
result on your own files.

## Installation

```bash
pip install switchfile-haystack
```

## API key

Create a key in the [switchfile dashboard](https://switchfile.io/dashboard/keys) and put it in an environment
variable:

```bash
export SWITCHFILE_API_KEY="sf_live_..."
```

Keys that start with `sf_test_` are sandbox keys. They convert files the same way and never use credits, which is
useful while you build a pipeline. Free accounts convert a number of files per day at no cost; see
[pricing](https://switchfile.io/pricing).

To read the key from another variable, pass `api_key=Secret.from_env_var("MY_VARIABLE")`.

## Usage

### On its own

```python
from haystack_integrations.components.converters.switchfile import SwitchfileConverter

converter = SwitchfileConverter()
result = converter.run(sources=["report.pdf"], meta={"collection": "annual-reports"})

document = result["documents"][0]
print(document.content)
print(document.meta)
# {'file_path': 'report.pdf', 'collection': 'annual-reports',
#  'switchfile_conversion_id': '0b8a7c1e-...', 'switchfile_credits': 1, 'page_count': 12}
```

The content is the Markdown that the API returns for `md`, the same output the benchmark scores: headings,
paragraphs, lists and tables. Running headers and footers are left out. Each page starts with a
`<!-- page N -->` line.

### Split by page

Set `split_pages=True` to separate pages by a form feed (`\f`), the same as the PDF converters that ship with
Haystack, so `DocumentSplitter(split_by="page")` keeps page numbers. The component then requests the structured
`json` result and renders the Markdown from it. That Markdown can differ in detail from the `md` result. Each file is
still converted once.

### In an indexing pipeline

```python
from haystack import Pipeline
from haystack.components.preprocessors import DocumentSplitter
from haystack.components.writers import DocumentWriter
from haystack.document_stores.in_memory import InMemoryDocumentStore
from haystack_integrations.components.converters.switchfile import SwitchfileConverter

document_store = InMemoryDocumentStore()

pipeline = Pipeline()
pipeline.add_component("converter", SwitchfileConverter(split_pages=True))
pipeline.add_component("splitter", DocumentSplitter(split_by="page", split_length=1))
pipeline.add_component("writer", DocumentWriter(document_store=document_store))
pipeline.connect("converter", "splitter")
pipeline.connect("splitter", "writer")

pipeline.run({"converter": {"sources": ["report.pdf", "invoice.pdf"]}})
```

### Tables as separate documents

With `table_documents=True`, every table is returned once more as a document of its own, after the document of the
whole file. A table that runs over several pages is returned once, with the page where it starts. Like
`split_pages`, this uses the `json` result, so the document of the whole file has its pages separated by a form feed:

```python
converter = SwitchfileConverter(table_documents=True)
documents = converter.run(sources=["report.pdf"])["documents"]

for table in documents[1:]:
    print(table.meta["page_number"], table.meta["table_rows"], table.meta["table_cols"])
    print(table.content)
```

Table documents carry `source_document_id` (the ID of the document of the whole file), `page_number`, `table_index`,
`table_rows`, `table_cols` and, where it is known, `table_header_rows`. Keep in mind that the tables are then in your
index twice: inside the full document and on their own.

## Parameters

| Parameter | Default | |
|---|---|---|
| `api_key` | `Secret.from_env_var("SWITCHFILE_API_KEY")` | The API key |
| `split_pages` | `False` | Separate pages by a form feed; the Markdown is then rendered from the `json` result |
| `table_documents` | `False` | Also return every table as its own document; also renders from the `json` result |
| `pages` | all | Pages to convert, for example `"1-3,5"` |
| `ocr_language` | detected | Language of scanned pages, for example `"eng"`, `"deu"` or `"deu+eng"` |
| `store` | `True` | `False` converts without storing the file or the result, see below |
| `timeout` | `300` | Seconds to wait for one file, including upload, conversion and download |
| `store_full_path` | `False` | Keep the full path in `file_path` instead of the file name |
| `raise_on_failure` | `False` | Raise on a file that cannot be converted instead of skipping it with a warning |
| `api_base_url` | `https://api.switchfile.io` | Base URL of the API |

The component supports `to_dict` and `from_dict`, so pipelines with it can be saved as YAML. The API key itself is
never serialized, only the name of its environment variable.

### Without storage

By default, switchfile keeps the file and the result for 24 hours and then deletes both. With `store=False`, the file
goes in the request and the result comes back in the response; neither is written to storage. This mode takes files up
to 50 MB and runs at most two requests per account at the same time. See
[Converting without storage](https://switchfile.io/docs/no-store).

## Metadata

| Key | |
|---|---|
| `file_path` | File name, or the full path with `store_full_path=True` |
| `page_count` | Number of pages |
| `ocr_pages` | Page numbers whose text comes from text recognition, only with `split_pages` or `table_documents` and when there are any |
| `switchfile_conversion_id` | ID of the conversion in your dashboard |
| `switchfile_credits` | Credits the conversion used |
| `switchfile_incomplete`, `switchfile_notes` | Set when some pages could not be converted; the notes name them |

Metadata from `meta` and from the `meta` of a `ByteStream` is kept.

## Errors

Failures raise `SwitchfileError` with the error `code` from the [API documentation](https://switchfile.io/docs/errors),
for example `UNSUPPORTED_PAIR`, `PASSWORD_PROTECTED` or `TIMEOUT`, and the HTTP `status` where there was one.

- A file that cannot be converted is skipped with a warning, so one broken file does not stop a large indexing run.
  Set `raise_on_failure=True` to raise instead.
- Errors of the account always raise, because every following file would fail the same way: `UNAUTHENTICATED`
  (missing or invalid key) and `CREDITS_EXCEEDED`.
- Rate limits (`429`) and a busy server (`503`) are retried after the time the API asks for, within `timeout`.

## Development

```bash
pip install hatch
hatch run test:unit          # mocked HTTP, no key needed
hatch run test:types
SWITCHFILE_API_KEY=sf_test_... hatch run test:integration   # live calls, sandbox keys only
```

## License

MIT, see [LICENSE](LICENSE).
