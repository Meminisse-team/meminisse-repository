from io import BytesIO
from unittest.mock import AsyncMock, patch
import httpx
import pytest
from fastapi import HTTPException, UploadFile
from starlette.datastructures import Headers
from PIL import Image
from app.media.uploads import read_image_upload
from app.clients import s3
from app.main import app
from fastapi.testclient import TestClient
from openai import APIStatusError

def upload(data, mime="image/png"):
    return UploadFile(BytesIO(data), filename="../../private.png", headers=Headers({"content-type": mime}))

@pytest.mark.asyncio
async def test_image_validation():
    data = BytesIO(); Image.new("RGB", (2, 2)).save(data, format="PNG")
    assert await read_image_upload(upload(data.getvalue())) == (data.getvalue(), "image/png")
    for value, mime, status in [(b"not image", "image/png", 422), (data.getvalue(), "image/jpeg", 422), (b"x", "image/svg+xml", 415)]:
        with pytest.raises(HTTPException) as error:
            await read_image_upload(upload(value, mime))
        assert error.value.status_code == status
    with patch("app.media.uploads.MAX_UPLOAD_BYTES", 4), pytest.raises(HTTPException) as error:
        await read_image_upload(upload(b"12345"))
    assert error.value.status_code == 413

@pytest.mark.asyncio
async def test_storage_returns_key_and_signs_for_five_minutes():
    with patch("app.clients.s3.asyncio.to_thread", new_callable=AsyncMock) as call, patch("app.clients.s3._client"):
        assert await s3.upload_bytes("private/key", b"x", content_type="image/png") == "private/key"
        assert call.call_args.kwargs["CacheControl"] == "private, no-store"
        await s3.generate_presigned_get_url("private/key")
        assert call.call_args.kwargs["ExpiresIn"] == 300

@pytest.mark.asyncio
async def test_provider_error_does_not_leak_body(caplog):
    from app.main import upstage_api_error_handler
    from starlette.requests import Request
    exc = APIStatusError("secret upstream content", response=httpx.Response(429, request=httpx.Request("POST", "https://api.upstage.ai")), body={"secret": "private"})
    response = await upstage_api_error_handler(Request({"type": "http", "method": "POST"}), exc)
    assert response.status_code == 502
    assert b"secret" not in response.body
    assert "secret upstream content" not in caplog.text
