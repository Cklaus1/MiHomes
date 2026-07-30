"""W.10 · M20 — AI upload endpoints must cap file count / per-file size / total.

``_read_attachments`` fed every uploaded file straight to ``process_upload``
with no count cap and no total-payload cap (only downstream per-file image
caps). A caller could attach hundreds of files or a huge aggregate payload.
This mirrors ``read_image_uploads``'s count/size validation.

The stream endpoint additionally called ``_read_attachments`` *outside* its
try/except, so a cap breach there would surface as an unhandled 500 instead of
a friendly error — that path is covered too.
"""

import io

import pytest

from mihomes.web.routes.ai import (
    _MAX_ATTACH_FILES,
    _MAX_ATTACH_TOTAL_BYTES,
    _read_attachments,
)


class _FakeUpload:
    def __init__(self, filename, data, content_type="text/plain"):
        self.filename = filename
        self._data = data
        self.content_type = content_type

    async def read(self):
        return self._data


@pytest.mark.asyncio
async def test_too_many_files_rejected():
    files = [_FakeUpload(f"f{i}.txt", b"hello") for i in range(_MAX_ATTACH_FILES + 1)]
    with pytest.raises(ValueError, match="at most"):
        await _read_attachments(files)


@pytest.mark.asyncio
async def test_total_payload_cap_rejected():
    # Two files whose combined size exceeds the total cap.
    half = _MAX_ATTACH_TOTAL_BYTES // 2 + 1
    files = [
        _FakeUpload("a.txt", b"x" * half),
        _FakeUpload("b.txt", b"y" * half),
    ]
    with pytest.raises(ValueError, match="[Tt]otal"):
        await _read_attachments(files)


@pytest.mark.asyncio
async def test_within_caps_ok():
    files = [_FakeUpload("a.txt", b"hello world")]
    result = await _read_attachments(files)
    assert isinstance(result, list)


def test_stream_endpoint_surfaces_cap_error(client):
    """Oversized upload on the streaming endpoint must not 500."""
    big = b"x" * (_MAX_ATTACH_TOTAL_BYTES + 1)
    resp = client.post(
        "/ai/ask-stream",
        data={"query": "hi"},
        files=[("files", ("big.txt", io.BytesIO(big), "text/plain"))],
    )
    assert resp.status_code == 200
    # The error should be delivered in-band as an SSE error event, not a 500.
    assert "data:" in resp.text
