"""Image normalization shared by Gemini and OpenAI-compatible Qwen requests."""

from __future__ import annotations

import base64
import os
import re
from collections.abc import Mapping, Sequence
from io import BytesIO
from typing import Any, Dict

from PIL import Image

from .errors import LLMInputError

NormalizedImage = Dict[str, str]

_DATA_URL_RE = re.compile(
    r"^data:(?P<mime>[\w/+.-]+);base64,(?P<data>.+)$", re.IGNORECASE | re.DOTALL
)
_MIME_BY_EXTENSION = {
    ".bmp": "image/bmp",
    ".gif": "image/gif",
    ".jpeg": "image/jpeg",
    ".jpg": "image/jpeg",
    ".png": "image/png",
    ".webp": "image/webp",
}


def _data_url_parts(value: str) -> tuple[str, str] | None:
    match = _DATA_URL_RE.match(value.strip())
    if not match:
        return None
    return match.group("mime").lower(), match.group("data").replace("\n", "").strip()


def _mime_for_path(path: str) -> str:
    return _MIME_BY_EXTENSION.get(os.path.splitext(path)[1].lower(), "image/jpeg")


def _validate_base64(value: str) -> str:
    compact = value.replace("\n", "").strip()
    if not compact:
        raise LLMInputError("Image base64 payload is empty")
    try:
        base64.b64decode(compact, validate=False)
    except Exception as exc:
        raise LLMInputError("Image base64 payload is invalid") from exc
    return compact


def pillow_image_to_data_url(image: Image.Image) -> str:
    """Encode a Pillow image as the JPEG data URL used by the original pipeline."""

    prepared = image
    if prepared.mode == "RGBA":
        rgb_image = Image.new("RGB", prepared.size, "white")
        rgb_image.paste(prepared, mask=prepared.getchannel("A"))
        prepared = rgb_image
    elif prepared.mode not in {"RGB", "L"}:
        prepared = prepared.convert("RGB")

    output = BytesIO()
    prepared.save(output, format="JPEG")
    encoded = base64.b64encode(output.getvalue()).decode("ascii")
    return f"data:image/jpeg;base64,{encoded}"


def normalize_images(images: Sequence[Any] | None) -> list[NormalizedImage]:
    """Convert supported local image representations into `{mime_type, data}` dictionaries.

    Each item may be a Pillow image, raw bytes, a file path, a base64 data URL,
    raw base64, or a mapping with `path`, `base64`, `data_url`, or `bytes` plus
    optional `mime_type`.
    Remote URLs are intentionally unsupported: reproducible examples should package
    inputs locally rather than silently depend on an external image host.
    """

    normalized: list[NormalizedImage] = []
    for image in images or []:
        mime_type = "image/jpeg"
        payload = ""

        if isinstance(image, Image.Image):
            parsed = _data_url_parts(pillow_image_to_data_url(image))
            assert parsed is not None
            mime_type, payload = parsed
        elif isinstance(image, bytes):
            payload = base64.b64encode(image).decode("ascii")
        elif isinstance(image, str):
            value = image.strip()
            if not value:
                continue
            parsed = _data_url_parts(value)
            if parsed:
                mime_type, payload = parsed
            elif os.path.isfile(value):
                with open(value, "rb") as handle:
                    payload = base64.b64encode(handle.read()).decode("ascii")
                mime_type = _mime_for_path(value)
            elif value.startswith(("http://", "https://")):
                raise LLMInputError("Remote image URLs are unsupported; download and pass a local file instead")
            else:
                payload = value
        elif isinstance(image, Mapping):
            item = dict(image)
            mime_type = str(item.get("mime_type") or mime_type).strip().lower()
            if isinstance(item.get("bytes"), bytes):
                payload = base64.b64encode(item["bytes"]).decode("ascii")
            elif item.get("path"):
                path = str(item["path"])
                if not os.path.isfile(path):
                    raise LLMInputError(f"Image path does not exist: {path}")
                with open(path, "rb") as handle:
                    payload = base64.b64encode(handle.read()).decode("ascii")
                if not item.get("mime_type"):
                    mime_type = _mime_for_path(path)
            elif item.get("data_url"):
                parsed = _data_url_parts(str(item["data_url"]))
                if not parsed:
                    raise LLMInputError("Invalid image data_url")
                mime_type, payload = parsed
            elif item.get("base64"):
                payload = str(item["base64"])
            else:
                raise LLMInputError("Image mapping requires bytes, path, data_url, or base64")
        else:
            raise LLMInputError(f"Unsupported image input type: {type(image)!r}")

        if not mime_type.startswith("image/"):
            raise LLMInputError(f"Unsupported image MIME type: {mime_type!r}")
        normalized.append({"mime_type": mime_type, "data": _validate_base64(payload)})
    return normalized
