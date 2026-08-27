"""MinerU Cloud upload/poll normalization contract tests."""

import asyncio
import io
import zipfile
from pathlib import Path

import pytest

from app.core.config import get_settings
from app.services.mineru import MineruCloudError, MineruCloudParser, _zip_result


class FakeMineruClient:
    def __init__(self):
        self.calls: list[tuple[str, str]] = []

    async def request(self, method, url, **_kwargs):
        self.calls.append((method, url))
        if method == "POST":
            return FakeResponse(
                json_data={"data": {"batch_id": "batch-1", "file_urls": [{"url": "https://upload"}]}},
            )
        return FakeResponse(
            json_data={
                "data": {
                    "extract_result_list": [
                        {"state": "done", "markdown_url": "https://markdown", "page_count": 2}
                    ]
                }
            },
        )

    async def put(self, url, **_kwargs):
        self.calls.append(("PUT", url))
        return FakeResponse()

    async def get(self, url, **_kwargs):
        self.calls.append(("GET", url))
        return FakeResponse(text="# Heading\n\nFull text from MinerU")


class FakeResponse:
    def __init__(self, *, json_data=None, text="", content=None):
        self._json_data = json_data
        self.text = text
        self.content = content if content is not None else text.encode("utf-8")

    def raise_for_status(self):
        return None

    def json(self):
        return self._json_data


@pytest.mark.asyncio
async def test_mineru_cloud_upload_poll_and_normalize(tmp_path: Path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_api_tokens", "key-a,key-b", raising=False)
    monkeypatch.setattr(settings, "mineru_poll_interval_seconds", 0.01, raising=False)
    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-test")
    client = FakeMineruClient()
    statuses: list[str] = []

    async def on_status(value: str) -> None:
        statuses.append(value)

    parser = MineruCloudParser(client=client)
    result = await parser.parse(pdf, on_status=on_status)

    assert result["parser"] == "mineru"
    assert result["pages"] == 2
    assert result["chunks"]
    assert statuses == ["mineru_uploading", "mineru_parsing"]
    assert [method for method, _url in client.calls] == ["POST", "PUT", "GET", "GET"]


def test_mineru_zip_keeps_markdown_images_and_tables():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("result/paper.md", "# Result\n\n![Figure](images/figure.png)")
        bundle.writestr("result/images/figure.png", b"png-bytes")
        bundle.writestr("result/tables/table.csv", "a,b\n1,2")
        bundle.writestr("../escape.txt", "blocked")

    result = _zip_result(stream.getvalue(), pages=3)

    assert result["markdown_path"] == "result/paper.md"
    assert result["manifest"] == {
        "pages": 3,
        "images": ["result/images/figure.png"],
        "tables": ["result/tables/table.csv"],
    }
    assert {item["path"] for item in result["artifacts"]} == {
        "result/images/figure.png",
        "result/tables/table.csv",
    }


def test_mineru_zip_rejects_invalid_markdown_encoding():
    stream = io.BytesIO()
    with zipfile.ZipFile(stream, "w") as bundle:
        bundle.writestr("paper.md", b"\xff\xfe\xfa")
    with pytest.raises(MineruCloudError, match="MINERU_MARKDOWN_ENCODING_INVALID"):
        _zip_result(stream.getvalue())


@pytest.mark.asyncio
async def test_mineru_scheduler_rotates_keys_across_parser_instances(monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_api_tokens", "rotation-a,rotation-b", raising=False)
    first = MineruCloudParser(client=FakeMineruClient())
    second = MineruCloudParser(client=FakeMineruClient())
    assert {first._next_token(), second._next_token()} == {"rotation-a", "rotation-b"}


@pytest.mark.asyncio
async def test_mineru_scheduler_enforces_process_wide_concurrency(tmp_path: Path, monkeypatch):
    settings = get_settings()
    monkeypatch.setattr(settings, "mineru_api_tokens", "concurrency-key", raising=False)
    monkeypatch.setattr(settings, "mineru_concurrency", 1, raising=False)
    monkeypatch.setattr(settings, "mineru_poll_interval_seconds", 0.01, raising=False)
    tracker = {"active": 0, "maximum": 0}

    class TrackingClient(FakeMineruClient):
        async def request(self, method, url, **kwargs):
            if method == "POST":
                tracker["active"] += 1
                tracker["maximum"] = max(tracker["maximum"], tracker["active"])
                await asyncio.sleep(0.02)
            return await super().request(method, url, **kwargs)

        async def get(self, url, **kwargs):
            response = await super().get(url, **kwargs)
            tracker["active"] -= 1
            return response

    pdf = tmp_path / "paper.pdf"
    pdf.write_bytes(b"%PDF-test")
    parsers = [MineruCloudParser(client=TrackingClient()) for _ in range(2)]
    await asyncio.gather(*(parser.parse(pdf) for parser in parsers))
    assert tracker["maximum"] == 1
