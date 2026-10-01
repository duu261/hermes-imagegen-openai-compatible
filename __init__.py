"""OpenAI Images-compatible image provider for Hermes Agent, tuned for CLIProxyAPI and New API.

Routes ``image_generate`` to a gateway's ``/v1/images/generations`` and ``/v1/images/edits``.
Settings live under ``plugins.entries.openai-compatible.settings`` in config.yaml; the API key is
read through Hermes' profile-scoped secret lookup from the variable named by ``key_env``.
"""

from __future__ import annotations

import base64
import io
import ipaddress
import logging
import os
import re
import socket
import struct
import urllib.error
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple
from urllib.parse import urlparse

from agent.image_gen_provider import (
    DEFAULT_ASPECT_RATIO,
    ImageGenProvider,
    error_response,
    normalize_reference_images,
    resolve_aspect_ratio,
    save_b64_image,
    save_url_image,
    success_response,
)

try:  # Profile-scoped secrets (multi-profile gateways); plain env on older Hermes.
    from agent.secret_scope import get_secret as _get_secret
except ImportError:  # pragma: no cover - older Hermes
    def _get_secret(name: str, default: Optional[str] = None) -> Optional[str]:
        return os.environ.get(name, default)

logger = logging.getLogger(__name__)

PLUGIN_NAME = "openai-compatible"
PLUGIN_VERSION = "2.0.0"
USER_AGENT = f"hermes-imagegen-openai-compatible/{PLUGIN_VERSION}"

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_KEY_ENV = "OPENAI_COMPAT_IMAGE_API_KEY"
LEGACY_BASE_URL_ENV = "OPENAI_COMPAT_IMAGE_BASE_URL"
LEGACY_MODEL_ENV = "OPENAI_COMPAT_IMAGE_MODEL"

# Models that take OpenAI ``size`` + ``quality``. Codex-backed gateways render these at
# ``low`` when quality is omitted, so a quality is always sent for them.
SIZED_MODELS = {
    "gpt-image-1",
    "gpt-image-1.5",
    "gpt-image-2",
    "gpt-image-2.5",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
}
QUALITIES = ("auto", "low", "medium", "high", "xhigh", "max")
DEFAULT_QUALITY = "medium"
SIZES = {"landscape": "1536x1024", "square": "1024x1024", "portrait": "1024x1536"}

MAX_SOURCE_IMAGE_BYTES = 50 * 1024 * 1024
MAX_ERROR_CHARS = 300
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


class SourceImageError(Exception):
    """A source/reference image could not be loaded; the message is safe to show the model."""


# --------------------------------------------------------------------------- settings


def _resolve_model(selected: str, quality_setting: str) -> Tuple[str, Optional[str]]:
    """``(api_model, quality)``. ``gpt-image-2-high`` -> (``gpt-image-2``, ``high``).

    Known GPT image models always get a quality (setting, else medium). Any other id is a
    gateway-defined model (Gemini "nano banana", Flux, ...) and is sent verbatim with no
    size/quality, because gateways reject enum values they do not know.
    """
    base, _, suffix = selected.rpartition("-")
    if base in SIZED_MODELS and suffix in QUALITIES:
        return base, suffix
    if selected in SIZED_MODELS:
        return selected, quality_setting if quality_setting in QUALITIES else DEFAULT_QUALITY
    return selected, None


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or not parsed.netloc or any(c in base_url for c in "\r\n\t "):
        raise ValueError("base_url must be an absolute URL with no credentials or whitespace")
    if parsed.scheme == "https" or (parsed.scheme == "http" and hostname in LOCAL_HTTP_HOSTS):
        return base_url.rstrip("/")
    raise ValueError("base_url must use HTTPS; plain HTTP is allowed only for localhost")


# --------------------------------------------------------------------------- source images


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reject_private_host(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise SourceImageError(f"could not resolve source image host {hostname!r}") from None
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise SourceImageError("source image host resolves to a private or local address")


def _load_image_bytes(ref: str) -> Tuple[bytes, str]:
    """Load a source image from an HTTPS URL, ``data:image/...`` URI, or local path."""
    ref = ref.strip()
    lower = ref.lower()
    if lower.startswith(("http://", "https://")):
        parsed = urlparse(ref)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise SourceImageError("source image URLs must use HTTPS without embedded credentials")
        _reject_private_host(parsed.hostname)
        request = urllib.request.Request(
            ref, headers={"Accept": "image/*", "User-Agent": USER_AGENT}, method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=60) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/"):
                    raise SourceImageError("source URL did not return an image")
                data = response.read(MAX_SOURCE_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise SourceImageError(f"source image URL returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise SourceImageError("could not reach source image URL") from None
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise SourceImageError("source image exceeds the 50MB limit")
        return data, Path(parsed.path).name or "image.png"
    if lower.startswith("data:"):
        header, separator, b64 = ref.partition(",")
        if not separator or not header.lower().startswith("data:image/"):
            raise SourceImageError("source data URL must contain an image MIME type")
        ext = header[5:].split(";", 1)[0].lower().split("/", 1)[1] or "png"
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError:
            raise SourceImageError("source data URL contains invalid base64") from None
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise SourceImageError("source image exceeds the 50MB limit")
        return data, f"image.{ext}"

    from agent.file_safety import raise_if_read_blocked

    try:
        raise_if_read_blocked(ref)
        with open(ref, "rb") as fh:
            data = fh.read(MAX_SOURCE_IMAGE_BYTES + 1)
    except FileNotFoundError:
        raise SourceImageError(
            f"source image not found: {ref} (cached images can be pruned; regenerate it "
            "or pass an existing file)") from None
    except IsADirectoryError:
        raise SourceImageError(f"source image path is a directory: {ref}") from None
    except PermissionError:
        raise SourceImageError(f"source image is not readable: {ref}") from None
    except OSError as exc:
        raise SourceImageError(
            f"could not read source image {ref}: {exc.strerror or type(exc).__name__}") from None
    except Exception as exc:  # noqa: BLE001 - raise_if_read_blocked refuses credential-like paths
        raise SourceImageError(f"source image read refused: {ref} ({type(exc).__name__})") from None
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise SourceImageError("source image exceeds the 50MB limit")
    return data, os.path.basename(ref) or "image.png"


def _named_file(ref: str) -> io.BytesIO:
    data, name = _load_image_bytes(ref)
    bio = io.BytesIO(data)
    bio.name = name
    return bio


# --------------------------------------------------------------------------- results


def _image_dims(head: bytes) -> Optional[str]:
    """``WxH`` from a PNG/JPEG header, else None. Gateways often ignore the requested size."""
    if head[:8] == b"\x89PNG\r\n\x1a\n" and len(head) >= 24:
        width, height = struct.unpack(">II", head[16:24])
        return f"{width}x{height}"
    if head[:2] == b"\xff\xd8":
        i = 2
        while i + 9 < len(head):
            if head[i] != 0xFF:
                i += 1
                continue
            marker = head[i + 1]
            if marker in (0xC0, 0xC1, 0xC2):
                height, width = struct.unpack(">HH", head[i + 5:i + 9])
                return f"{width}x{height}"
            i += 2 + struct.unpack(">H", head[i + 2:i + 4])[0]
    return None


def _file_dims(path: str) -> Optional[str]:
    try:
        with open(path, "rb") as fh:
            return _image_dims(fh.read(65536))
    except OSError:
        return None


def _redact(text: str, secrets: List[str]) -> str:
    for secret in secrets:
        if secret and len(secret) >= 6:
            text = text.replace(secret, "[redacted]")
    text = re.sub(r"(?i)bearer\s+[a-z0-9._\-]+", "Bearer [redacted]", text)
    text = re.sub(r"\bsk-[A-Za-z0-9_\-]{8,}", "sk-[redacted]", text)
    text = " ".join(text.split())
    return text[:MAX_ERROR_CHARS] + ("..." if len(text) > MAX_ERROR_CHARS else "")


def _status_error(exc: Any, secrets: List[str]) -> Tuple[str, str]:
    """``(error, error_type)`` for an ``openai.APIStatusError``."""
    status = getattr(exc, "status_code", 0) or 0
    body = getattr(exc, "body", None)
    message = ""
    if isinstance(body, dict):
        inner = body["error"] if isinstance(body.get("error"), dict) else body
        message = str(inner.get("message") or inner.get("error_name") or inner.get("title") or "")
    if not message:
        response = getattr(exc, "response", None)
        message = getattr(response, "text", "") or getattr(exc, "message", "") or ""
    message = _redact(str(message), secrets)
    hint = ""
    if status == 403 and ("1010" in message or "browser_signature_banned" in message):
        hint = " (Cloudflare blocked the client signature; check the gateway's WAF rules)"
    elif status in (401, 403):
        hint = " (check the key in the variable named by key_env and its gateway permissions)"
    elif status == 404:
        hint = " (check base_url ends at the API root, usually /v1, and the model is enabled)"
    elif status == 524:
        hint = " (Cloudflare cut a slow render at ~125s; use a route without the CDN for long images)"
    error_type = {401: "auth_error", 403: "auth_error", 429: "rate_limited"}.get(status, "api_error")
    return f"HTTP {status}: {message or 'no error message'}{hint}", error_type


# --------------------------------------------------------------------------- provider


class OpenAICompatibleImageGenProvider(ImageGenProvider):
    """``images.generate`` / ``images.edit`` against a trusted OpenAI-compatible gateway."""

    def __init__(self, ctx: Any = None) -> None:
        self._ctx = ctx

    @property
    def name(self) -> str:
        return PLUGIN_NAME

    @property
    def display_name(self) -> str:
        return "OpenAI-Compatible (CLIProxyAPI / New API)"

    # -- settings --------------------------------------------------------------------

    def _setting(self, key: str) -> str:
        getter = getattr(self._ctx, "get_config", None)
        if getter is None:
            return ""
        try:
            value = getter(key, "")
        except Exception:  # noqa: BLE001 - a malformed config must not break dispatch
            logger.debug("Could not read %s setting %s", PLUGIN_NAME, key, exc_info=True)
            return ""
        return value.strip() if isinstance(value, str) else ""

    def _base_url(self) -> str:
        return self._setting("base_url") or (_get_secret(LEGACY_BASE_URL_ENV, "") or "").strip()

    def _key_env(self) -> str:
        return self._setting("key_env") or DEFAULT_KEY_ENV

    @staticmethod
    def _selected_model(requested: Any) -> str:
        """``image_gen.model`` (passed by Hermes) -> legacy env -> default."""
        for candidate in (requested if isinstance(requested, str) else "",
                          _get_secret(LEGACY_MODEL_ENV, "") or ""):
            if candidate.strip():
                return candidate.strip()
        return DEFAULT_MODEL

    # -- provider surface --------------------------------------------------------------

    def is_available(self) -> bool:
        if not self._base_url():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        return [
            {"id": "gpt-image-2", "display": "GPT Image 2", "speed": "varies",
             "strengths": "Default; CLIProxyAPI Codex and New API image channels"},
            {"id": "gpt-image-2.5-flare", "display": "GPT Image 2.5 Flare", "speed": "faster",
             "strengths": "Everyday generation and editing"},
            {"id": "gpt-image-2.5-sunburst", "display": "GPT Image 2.5 Sunburst", "speed": "slower",
             "strengths": "Precision generation and editing"},
        ]

    def default_model(self) -> Optional[str]:
        return self._selected_model(None)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI-Compatible (CLIProxyAPI / New API)",
            "badge": "gateway",
            "tag": "GPT Image via CLIProxyAPI, New API, or another OpenAI Images gateway",
            "env_vars": [
                {"key": DEFAULT_KEY_ENV, "prompt": "Gateway API key (empty for keyless local gateways)",
                 "secret": True, "optional": True},
            ],
        }

    def capabilities(self) -> Dict[str, Any]:
        return {"modalities": ["text", "image"], "max_reference_images": 16}

    def generate(
        self,
        prompt: str,
        aspect_ratio: str = DEFAULT_ASPECT_RATIO,
        *,
        image_url: Optional[str] = None,
        reference_image_urls: Optional[List[str]] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)
        label = self._selected_model(kwargs.get("model"))
        api_model, quality = _resolve_model(label, self._setting("quality"))

        def fail(error: str, error_type: str) -> Dict[str, Any]:
            return error_response(error=error, error_type=error_type, provider=PLUGIN_NAME,
                                  model=label, prompt=prompt, aspect_ratio=aspect)

        if not prompt:
            return fail("Prompt is required and cannot be empty", "invalid_argument")
        try:
            import openai
        except ImportError:
            return fail("openai Python package not installed (pip install 'openai>=2,<3')", "missing_dependency")

        raw_base_url = self._base_url()
        if not raw_base_url:
            return fail(
                f"No gateway configured. Run: hermes config set plugins.entries.{PLUGIN_NAME}.settings.base_url "
                "https://<your-gateway>/v1", "configuration_error")
        try:
            base_url = _validate_base_url(raw_base_url)
        except ValueError as exc:
            return fail(str(exc), "configuration_error")
        key_env = self._key_env()
        api_key = (_get_secret(key_env, "") or "").strip()
        if any(c in api_key for c in "\r\n\t "):
            return fail(f"The key in {key_env} contains whitespace or line breaks", "configuration_error")

        sources: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            sources.append(image_url.strip())
        sources.extend(normalize_reference_images(reference_image_urls) or [])
        sources = sources[:16]
        is_edit = bool(sources)

        # Load every source before calling the gateway so a missing file is an io_error,
        # not an opaque API failure.
        try:
            files = [_named_file(ref) for ref in sources]
        except SourceImageError as exc:
            return fail(str(exc), "io_error")

        request: Dict[str, Any] = {"model": api_model, "prompt": prompt, "n": 1}
        size = SIZES[aspect]
        if quality is not None:
            request["size"] = size
            request["quality"] = quality
        if is_edit:
            request["image"] = files if len(files) > 1 else files[0]

        secrets = [api_key]
        try:
            client = openai.OpenAI(
                base_url=base_url, api_key=api_key or "not-needed",
                default_headers={"User-Agent": USER_AGENT},
            )
            call = client.images.edit if is_edit else client.images.generate
            response = call(**request)
        except openai.APIStatusError as exc:
            error, error_type = _status_error(exc, secrets)
            return fail(error, error_type)
        except openai.APITimeoutError:
            return fail("Gateway request timed out", "timeout")
        except openai.APIConnectionError:
            return fail(f"Could not connect to the gateway at {base_url}", "connection_error")
        except Exception as exc:  # noqa: BLE001 - surface the category, never raw config
            # No exc_info: header/URL errors can carry the key in their traceback.
            logger.debug("%s image request failed: %s", PLUGIN_NAME, type(exc).__name__)
            return fail(f"Image request failed ({type(exc).__name__}): {_redact(str(exc), secrets)}", "api_error")

        data = getattr(response, "data", None) or []
        if not data or data[0] is None:
            return fail("Gateway returned no image data", "empty_response")
        item = data[0]
        b64_data = getattr(item, "b64_json", None)
        url_data = getattr(item, "url", None)
        try:
            if b64_data:
                image_ref = str(save_b64_image(b64_data, prefix="openai_compat"))
            elif url_data:
                image_ref = str(save_url_image(url_data, prefix="openai_compat"))
            else:
                return fail("Gateway response contained neither b64_json nor url", "invalid_response")
        except Exception as exc:  # noqa: BLE001
            return fail(f"Could not save the returned image ({type(exc).__name__})", "io_error")

        extra: Dict[str, Any] = {"api_model": api_model}
        if quality is not None:
            extra.update(requested_size=size, requested_quality=quality)
        output_size = _file_dims(image_ref)
        if output_size:
            extra["output_size"] = output_size
        if getattr(item, "revised_prompt", None):
            extra["revised_prompt"] = item.revised_prompt
        return success_response(
            image=image_ref, model=label, prompt=prompt, aspect_ratio=aspect, provider=PLUGIN_NAME,
            modality="image" if is_edit else "text", extra=extra)


def register(ctx: Any) -> None:
    """Register the provider with Hermes Agent."""
    ctx.register_image_gen_provider(OpenAICompatibleImageGenProvider(ctx))
