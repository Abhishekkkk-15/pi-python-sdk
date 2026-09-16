"""Image/video attachments for multimodal LLM calls."""

from __future__ import annotations

import base64
import mimetypes
import re
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence
from urllib.parse import urlparse

# Soft limits (bytes) — oversized files return an error instead of crashing.
MAX_IMAGE_BYTES = 20 * 1024 * 1024
MAX_VIDEO_BYTES = 50 * 1024 * 1024

IMAGE_MIMES = {
    "image/png",
    "image/jpeg",
    "image/jpg",
    "image/webp",
    "image/gif",
    "image/bmp",
}
VIDEO_MIMES = {
    "video/mp4",
    "video/mpeg",
    "video/quicktime",
    "video/webm",
    "video/x-msvideo",
    "video/x-matroska",
}

# Model id substrings that typically accept images / video.
_IMAGE_MODEL_HINTS = (
    "gpt-4o",
    "gpt-4.1",
    "gpt-4-turbo",
    "gpt-5",
    "o1",
    "o3",
    "o4",
    "vision",
    "pixtral",
    "llava",
    "gemini",
    "claude-3",
    "claude-4",
    "claude-sonnet",
    "claude-opus",
    "claude-haiku",
    "qwen-vl",
    "qwen2-vl",
    "qwen2.5-vl",
)
_VIDEO_MODEL_HINTS = (
    "gemini",
)


@dataclass
class Attachment:
    """
    One user media attachment (image or video).

    Provide exactly one of ``path``, ``url``, or ``data``.
    """

    path: str | Path | None = None
    url: str | None = None
    data: bytes | None = None
    mime: str | None = None
    filename: str | None = None

    def to_storage_dict(self) -> dict[str, Any]:
        """Metadata for session persistence (no raw bytes)."""
        out: dict[str, Any] = {}
        if self.path is not None:
            out["path"] = str(self.path)
        if self.url is not None:
            out["url"] = self.url
        if self.mime is not None:
            out["mime"] = self.mime
        if self.filename is not None:
            out["filename"] = self.filename
        # Persist inline data only when there is no path/url (small clips / uploads)
        if self.data is not None and self.path is None and not self.url:
            out["data_base64"] = base64.b64encode(self.data).decode("ascii")
        return out

    @classmethod
    def from_storage_dict(cls, data: dict[str, Any]) -> "Attachment":
        raw_b64 = data.get("data_base64")
        raw = base64.b64decode(raw_b64) if raw_b64 else None
        return cls(
            path=data.get("path"),
            url=data.get("url"),
            data=raw,
            mime=data.get("mime"),
            filename=data.get("filename"),
        )


@dataclass
class LoadedMedia:
    """Normalized payload ready for provider encoding."""

    kind: str  # "image" | "video"
    mime: str
    filename: str | None = None
    url: str | None = None
    data: bytes | None = None

    def data_uri(self) -> str | None:
        if self.data is None:
            return None
        b64 = base64.b64encode(self.data).decode("ascii")
        return f"data:{self.mime};base64,{b64}"


def _guess_mime(path_or_url: str | None, explicit: str | None) -> str | None:
    if explicit and explicit.strip():
        mime = explicit.strip().lower()
        if mime == "image/jpg":
            return "image/jpeg"
        return mime
    if not path_or_url:
        return None
    guessed, _ = mimetypes.guess_type(path_or_url)
    if guessed == "image/jpg":
        return "image/jpeg"
    return guessed


def media_kind(mime: str | None) -> str | None:
    if not mime:
        return None
    m = mime.lower()
    if m in IMAGE_MIMES or m.startswith("image/"):
        return "image"
    if m in VIDEO_MIMES or m.startswith("video/"):
        return "video"
    return None


def load_attachment(att: Attachment) -> LoadedMedia:
    """Resolve an Attachment into bytes and/or URL. Raises ValueError on bad input."""
    path = Path(att.path).expanduser() if att.path else None
    url = (att.url or "").strip() or None
    data = att.data

    sources = sum(1 for x in (path, url, data) if x is not None)
    if sources == 0:
        raise ValueError("Attachment requires path, url, or data")
    if sources > 1 and data is not None and (path or url):
        # Prefer explicit data when provided alongside metadata
        path = None
        url = None

    filename = att.filename
    mime = att.mime

    if path is not None:
        if not path.is_file():
            raise ValueError(f"Attachment file not found: {path}")
        data = path.read_bytes()
        filename = filename or path.name
        mime = _guess_mime(str(path), mime)
    elif url is not None:
        filename = filename or Path(urlparse(url).path).name or None
        mime = _guess_mime(url, mime)
        if url.startswith("data:"):
            # data:[<mime>][;base64],<payload>
            try:
                header, payload = url.split(",", 1)
                meta = header[5:]
                mime_part = meta.split(";")[0] or mime
                if mime_part:
                    mime = mime_part
                if ";base64" in meta:
                    data = base64.b64decode(payload)
                    url = None
            except Exception as e:
                raise ValueError(f"Invalid data URL attachment: {e}") from e
    elif data is not None:
        mime = _guess_mime(filename, mime)

    if not mime:
        raise ValueError(
            f"Could not determine MIME type for attachment "
            f"{filename or path or url or 'upload'}; pass mime= explicitly"
        )

    kind = media_kind(mime)
    if kind is None:
        raise ValueError(
            f"Unsupported attachment type {mime!r}. "
            "Only image/* and video/* are supported."
        )

    max_bytes = MAX_IMAGE_BYTES if kind == "image" else MAX_VIDEO_BYTES
    if data is not None and len(data) > max_bytes:
        mb = max_bytes / (1024 * 1024)
        raise ValueError(
            f"Attachment {filename or 'upload'} is too large "
            f"({len(data)} bytes; max {mb:.0f} MB for {kind})"
        )

    if data is None and not url:
        raise ValueError("Attachment has no resolvable data or URL")

    return LoadedMedia(kind=kind, mime=mime, filename=filename, url=url, data=data)


def load_attachments(attachments: Sequence[Attachment] | None) -> list[LoadedMedia]:
    if not attachments:
        return []
    return [load_attachment(a) for a in attachments]


def model_supports_images(provider: str | None, model: str | None) -> bool:
    p = (provider or "").lower()
    m = (model or "").lower()
    if p in ("vertex", "google", "gemini"):
        return True
    if any(h in m for h in _IMAGE_MODEL_HINTS):
        return True
    # OpenRouter / custom often embed the real id after /
    if "/" in m and any(h in m.split("/")[-1] for h in _IMAGE_MODEL_HINTS):
        return True
    return False


def model_supports_video(provider: str | None, model: str | None) -> bool:
    p = (provider or "").lower()
    m = (model or "").lower()
    if p in ("vertex", "google", "gemini"):
        return True
    if any(h in m for h in _VIDEO_MODEL_HINTS):
        return True
    return False


def validate_attachments_for_model(
    attachments: Sequence[Attachment] | None,
    *,
    provider: str | None,
    model: str | None,
) -> Optional[str]:
    """
    Return an error message if attachments cannot be sent, else None.

    Never raises — callers should return a soft RunResult error.
    """
    if not attachments:
        return None
    try:
        loaded = load_attachments(list(attachments))
    except ValueError as e:
        return str(e)

    kinds = {item.kind for item in loaded}
    if "image" in kinds and not model_supports_images(provider, model):
        return (
            f"Model {model!r} (provider {provider!r}) does not support image attachments. "
            "Use a vision-capable model (e.g. gpt-4o, gemini-2.5-flash) or remove images."
        )
    if "video" in kinds and not model_supports_video(provider, model):
        return (
            f"Model {model!r} (provider {provider!r}) does not support video attachments. "
            "Use a video-capable model (e.g. gemini-2.5-flash) or remove videos."
        )
    return None


def attachments_to_content_parts(
    text: str,
    attachments: Sequence[Attachment] | None,
) -> list[dict[str, Any]] | str:
    """
    Build SDK-normalized multimodal content for a user message.

    Returns a plain string when there are no attachments (compat with text-only paths).
    """
    if not attachments:
        return text or ""
    loaded = load_attachments(list(attachments))
    parts: list[dict[str, Any]] = []
    if text and str(text).strip():
        parts.append({"type": "text", "text": str(text)})
    for item in loaded:
        part: dict[str, Any] = {
            "type": "media",
            "media_type": item.kind,
            "mime": item.mime,
        }
        if item.filename:
            part["filename"] = item.filename
        if item.url:
            part["url"] = item.url
        if item.data is not None:
            part["data_base64"] = base64.b64encode(item.data).decode("ascii")
        parts.append(part)
    if not parts:
        return text or ""
    return parts


def is_multimodal_content(content: Any) -> bool:
    return isinstance(content, list)


def content_text_fallback(content: Any) -> str:
    """Flatten multimodal content to plain text (compaction / logs)."""
    if content is None:
        return ""
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        texts: list[str] = []
        for part in content:
            if not isinstance(part, dict):
                continue
            if part.get("type") == "text":
                texts.append(str(part.get("text") or ""))
            elif part.get("type") == "media":
                kind = part.get("media_type") or "media"
                name = part.get("filename") or part.get("mime") or kind
                texts.append(f"[{kind}: {name}]")
        return "\n".join(t for t in texts if t)
    return str(content)


# ---------------------------------------------------------------------------
# Provider adapters
# ---------------------------------------------------------------------------

def to_openai_chat_content(content: Any) -> Any:
    """Chat Completions content: string or list of typed parts."""
    if not is_multimodal_content(content):
        return content if content is not None else ""
    out: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append({"type": "text", "text": str(part.get("text") or "")})
            continue
        if part.get("type") != "media":
            continue
        media_type = part.get("media_type")
        mime = str(part.get("mime") or "")
        url = part.get("url")
        b64 = part.get("data_base64")
        if media_type == "image":
            image_url = url or (f"data:{mime};base64,{b64}" if b64 else None)
            if image_url:
                out.append(
                    {"type": "image_url", "image_url": {"url": image_url}}
                )
        elif media_type == "video":
            # Most OpenAI-compatible Chat Completions hosts reject video parts.
            # Leave a text placeholder; capability check should block earlier.
            name = part.get("filename") or mime or "video"
            out.append(
                {
                    "type": "text",
                    "text": f"[video attachment omitted for chat API: {name}]",
                }
            )
    return out or ""


def to_openai_responses_user_content(content: Any) -> Any:
    """Responses API user content parts."""
    if not is_multimodal_content(content):
        return content if content is not None else ""
    out: list[dict[str, Any]] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            out.append({"type": "input_text", "text": str(part.get("text") or "")})
            continue
        if part.get("type") != "media":
            continue
        media_type = part.get("media_type")
        mime = str(part.get("mime") or "")
        url = part.get("url")
        b64 = part.get("data_base64")
        if media_type == "image":
            image_url = url or (f"data:{mime};base64,{b64}" if b64 else None)
            if image_url:
                out.append({"type": "input_image", "image_url": image_url})
        elif media_type == "video":
            name = part.get("filename") or mime or "video"
            out.append(
                {
                    "type": "input_text",
                    "text": f"[video attachment not supported on this API: {name}]",
                }
            )
    return out or ""


def vertex_parts_from_content(content: Any, types: Any) -> list[Any]:
    """Gemini Content parts from SDK-normalized content."""
    if not is_multimodal_content(content):
        text = str(content or "")
        return [types.Part.from_text(text=text)] if text else []

    parts: list[Any] = []
    for part in content:
        if not isinstance(part, dict):
            continue
        if part.get("type") == "text":
            text = str(part.get("text") or "")
            if text:
                parts.append(types.Part.from_text(text=text))
            continue
        if part.get("type") != "media":
            continue
        mime = str(part.get("mime") or "application/octet-stream")
        b64 = part.get("data_base64")
        url = part.get("url")
        if b64:
            raw = base64.b64decode(b64)
            parts.append(types.Part.from_bytes(data=raw, mime_type=mime))
        elif url:
            # Remote URI — Gemini file_data / from_uri when available
            try:
                parts.append(types.Part.from_uri(file_uri=url, mime_type=mime))
            except Exception:
                try:
                    parts.append(
                        types.Part(
                            file_data=types.FileData(file_uri=url, mime_type=mime)
                        )
                    )
                except Exception:
                    parts.append(
                        types.Part.from_text(
                            text=f"[{part.get('media_type')}: {url}]"
                        )
                    )
    return parts
