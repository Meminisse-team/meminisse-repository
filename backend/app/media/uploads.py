"""Bounded image ingestion. Names and client MIME declarations are never trusted."""
from io import BytesIO
import warnings
from fastapi import HTTPException, UploadFile
from PIL import Image, UnidentifiedImageError

MAX_UPLOAD_BYTES = 10 * 1024 * 1024
MAX_IMAGE_PIXELS = 25_000_000
MIME_FORMATS = {"image/jpeg": "JPEG", "image/png": "PNG", "image/webp": "WEBP"}

async def read_image_upload(file: UploadFile) -> tuple[bytes, str]:
    mime = (file.content_type or "").lower()
    if mime not in MIME_FORMATS:
        raise HTTPException(415, "JPEG, PNG, WebP 이미지만 업로드할 수 있습니다.")
    chunks = bytearray()
    while chunk := await file.read(64 * 1024):
        chunks.extend(chunk)
        if len(chunks) > MAX_UPLOAD_BYTES:
            raise HTTPException(413, "이미지는 10 MiB 이하여야 합니다.")
    try:
        with warnings.catch_warnings():
            warnings.simplefilter("error", Image.DecompressionBombWarning)
            with Image.open(BytesIO(chunks)) as image:
                if image.format != MIME_FORMATS[mime] or image.width * image.height > MAX_IMAGE_PIXELS:
                    raise ValueError("invalid image format or dimensions")
                image.verify()
            with Image.open(BytesIO(chunks)) as image:
                image.load()
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError, Image.DecompressionBombWarning):
        raise HTTPException(422, "유효한 이미지 파일이 아닙니다.") from None
    return bytes(chunks), mime
