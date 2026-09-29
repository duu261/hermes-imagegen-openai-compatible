"""OpenAI Images-compatible GPT-Image2 provider plugin for Hermes Agent.

Provides a basic backend for a trusted OpenAI-compatible
`/v1/images/generations` and `/v1/images/edits` API.
"""

from __future__ import annotations

import io
import base64
import ipaddress
import logging
import os
import socket
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

logger = logging.getLogger(__name__)

DEFAULT_MODEL = "gpt-image-2"
DEFAULT_BASE_URL = ""
GPT_IMAGE_2_MODEL = "gpt-image-2"
# Models that take GPT-Image2 size + quality. Without an explicit quality the
# Codex-backed proxies render 2.5 models at "low".
GPT_IMAGE_SIZED_MODELS = {
    GPT_IMAGE_2_MODEL,
    "gpt-image-2.5",
    "gpt-image-2.5-flare",
    "gpt-image-2.5-sunburst",
}
GPT_IMAGE_2_SIZES = {
    "landscape": "1536x1024",
    "square": "1024x1024",
    "portrait": "1024x1536",
}
GPT_IMAGE_2_QUALITIES = {"low", "medium", "high", "auto"}
MAX_SOURCE_IMAGE_BYTES = 50 * 1024 * 1024
LOCAL_HTTP_HOSTS = {"localhost", "127.0.0.1", "::1"}


def _load_image_bytes(ref: str) -> Tuple[bytes, str]:
    """Load image bytes from a URL, base64 data URI, or local file path."""
    ref = ref.strip()
    lower = ref.lower()
    if lower.startswith(("http://", "https://")):
        parsed = urlparse(ref)
        if parsed.scheme != "https" or parsed.username or parsed.password or not parsed.hostname:
            raise ValueError("source image URLs must use HTTPS without embedded credentials")
        _reject_private_host(parsed.hostname)
        request = urllib.request.Request(ref, headers={"Accept": "image/*"}, method="GET")
        opener = urllib.request.build_opener(_NoRedirectHandler())
        try:
            with opener.open(request, timeout=60) as response:
                content_type = (response.headers.get("Content-Type") or "").split(";", 1)[0].strip().lower()
                if not content_type.startswith("image/"):
                    raise ValueError("source URL did not return an image")
                data = response.read(MAX_SOURCE_IMAGE_BYTES + 1)
        except urllib.error.HTTPError as exc:
            raise ValueError(f"source image request returned HTTP {exc.code}") from None
        except (urllib.error.URLError, TimeoutError, socket.timeout):
            raise ValueError("could not reach source image URL") from None
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError("source image exceeds the 50MB limit")
        name = Path(parsed.path).name or "image.png"
        return data, name
    if lower.startswith("data:"):
        header, separator, b64 = ref.partition(",")
        if not separator or not header.lower().startswith("data:image/"):
            raise ValueError("source data URL must contain an image MIME type")
        mime_type = header[5:].split(";", 1)[0].lower()
        ext = mime_type.split("/", 1)[1] or "png"
        try:
            data = base64.b64decode(b64, validate=True)
        except ValueError:
            raise ValueError("source data URL contains invalid base64") from None
        if len(data) > MAX_SOURCE_IMAGE_BYTES:
            raise ValueError("source image exceeds the 50MB limit")
        return data, f"image.{ext}"

    from agent.file_safety import raise_if_read_blocked

    raise_if_read_blocked(ref)
    with open(ref, "rb") as fh:
        data = fh.read()
    name = os.path.basename(ref) or "image.png"
    if len(data) > MAX_SOURCE_IMAGE_BYTES:
        raise ValueError("source image exceeds the 50MB limit")
    return data, name


class _NoRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


def _reject_private_host(hostname: str) -> None:
    try:
        addresses = socket.getaddrinfo(hostname, 443, type=socket.SOCK_STREAM)
    except socket.gaierror:
        raise ValueError("could not resolve source image host") from None
    for address in addresses:
        try:
            ip = ipaddress.ip_address(address[4][0])
        except ValueError:
            continue
        if ip.is_private or ip.is_loopback or ip.is_link_local or ip.is_reserved or ip.is_multicast:
            raise ValueError("source image host resolves to a private or local address")


def _validate_base_url(base_url: str) -> str:
    parsed = urlparse(base_url)
    hostname = (parsed.hostname or "").lower()
    if parsed.username or parsed.password or not parsed.netloc:
        raise ValueError("image base URL must be absolute and contain no embedded credentials")
    if parsed.scheme == "https":
        return base_url.rstrip("/")
    if parsed.scheme == "http" and hostname in LOCAL_HTTP_HOSTS:
        return base_url.rstrip("/")
    raise ValueError("remote image base URLs must use HTTPS; HTTP is allowed only for localhost")


def _resolve_model(raw_model: Any) -> Tuple[str, str, str]:
    configured = os.environ.get("OPENAI_COMPAT_IMAGE_MODEL", "").strip()
    selected = configured or (raw_model.strip() if isinstance(raw_model, str) else "") or DEFAULT_MODEL
    if selected in {"gpt-image-2-low", "gpt-image-2-medium", "gpt-image-2-high"}:
        quality = selected.rsplit("-", 1)[1]
        return selected, GPT_IMAGE_2_MODEL, quality
    if selected in GPT_IMAGE_SIZED_MODELS:
        return selected, selected, "medium"
    return selected, selected, ""


class OpenAICompatibleImageGenProvider(ImageGenProvider):
    """OpenAI Images-compatible generation and editing backend."""

    @property
    def name(self) -> str:
        return "openai-compatible"

    @property
    def display_name(self) -> str:
        return "OpenAI-Compatible"

    def is_available(self) -> bool:
        """Check whether the SDK and an explicit endpoint are configured."""
        if not os.environ.get("OPENAI_COMPAT_IMAGE_BASE_URL", "").strip():
            return False
        try:
            import openai  # noqa: F401
        except ImportError:
            return False
        return True

    def list_models(self) -> List[Dict[str, Any]]:
        configured_model = os.environ.get("OPENAI_COMPAT_IMAGE_MODEL", DEFAULT_MODEL)
        return [
            {
                "id": configured_model,
                "display": f"OpenAI-Compatible ({configured_model})",
                "speed": "varies",
                "strengths": "Routes text-to-image and image-editing through OpenAI-compatible endpoint",
            }
        ]

    def default_model(self) -> Optional[str]:
        return os.environ.get("OPENAI_COMPAT_IMAGE_MODEL", DEFAULT_MODEL)

    def get_setup_schema(self) -> Dict[str, Any]:
        return {
            "name": "OpenAI-Compatible",
            "badge": "custom",
            "tag": "Generic OpenAI-compatible image generation endpoint",
            "env_vars": [
                {
                    "key": "OPENAI_COMPAT_IMAGE_BASE_URL",
                    "prompt": "HTTPS base URL for the OpenAI-compatible image endpoint",
                },
                {
                    "key": "OPENAI_COMPAT_IMAGE_MODEL",
                    "prompt": "Model ID (default: image-model-v1)",
                    "optional": True,
                },
                {
                    "key": "OPENAI_COMPAT_IMAGE_API_KEY",
                    "prompt": "API key (optional)",
                    "secret": True,
                    "optional": True,
                },
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
        try:
            import openai
        except ImportError:
            return error_response(
                error="openai Python package not installed (pip install openai)",
                error_type="missing_dependency",
                provider=self.name,
                aspect_ratio=aspect_ratio,
            )

        prompt = (prompt or "").strip()
        aspect = resolve_aspect_ratio(aspect_ratio)

        if not prompt:
            return error_response(
                error="Prompt is required and cannot be empty",
                error_type="invalid_argument",
                provider=self.name,
                aspect_ratio=aspect,
            )

        model, api_model, quality = _resolve_model(kwargs.get("model"))
        raw_base_url = os.environ.get("OPENAI_COMPAT_IMAGE_BASE_URL", DEFAULT_BASE_URL).strip()
        if not raw_base_url:
            return error_response(
                error="OPENAI_COMPAT_IMAGE_BASE_URL is not configured",
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        try:
            base_url = _validate_base_url(raw_base_url)
        except ValueError as exc:
            return error_response(
                error=str(exc),
                error_type="configuration_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )
        api_key = os.environ.get("OPENAI_COMPAT_IMAGE_API_KEY", "dummy")

        # Collect source images for image-to-image / edit
        sources: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            sources.append(image_url.strip())
        for ref in (normalize_reference_images(reference_image_urls) or []):
            sources.append(ref)
        sources = sources[:16]
        is_edit = bool(sources)
        modality = "image" if is_edit else "text"

        try:
            client = openai.OpenAI(base_url=base_url, api_key=api_key)

            image_options: Dict[str, Any] = {}
            if api_model in GPT_IMAGE_SIZED_MODELS:
                image_options["size"] = GPT_IMAGE_2_SIZES[aspect]
                requested_quality = kwargs.get("quality")
                image_options["quality"] = (
                    requested_quality
                    if isinstance(requested_quality, str) and requested_quality in GPT_IMAGE_2_QUALITIES
                    else quality
                )

            if is_edit:
                files = []
                for ref in sources:
                    data, fname = _load_image_bytes(ref)
                    bio = io.BytesIO(data)
                    bio.name = fname
                    files.append(bio)

                response = client.images.edit(
                    model=api_model,
                    image=files if len(files) > 1 else files[0],
                    prompt=prompt,
                    **image_options,
                    n=1,
                )
            else:
                response = client.images.generate(
                    model=api_model,
                    prompt=prompt,
                    **image_options,
                    n=1,
                )

            if not response.data or not response.data[0]:
                return error_response(
                    error="OpenAI-compatible endpoint returned empty image response",
                    error_type="empty_response",
                    provider=self.name,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )

            item = response.data[0]
            b64_data = getattr(item, "b64_json", None)
            url_data = getattr(item, "url", None)

            if b64_data:
                file_path = save_b64_image(b64_data, prefix="openai_compat")
                return success_response(
                    image=str(file_path),
                    provider=self.name,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                    modality=modality,
                )
            elif url_data:
                file_path = save_url_image(url_data, prefix="openai_compat")
                return success_response(
                    image=str(file_path),
                    provider=self.name,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                    modality=modality,
                )
            else:
                return error_response(
                    error="Endpoint response contained neither b64_json nor url",
                    error_type="invalid_response",
                    provider=self.name,
                    model=model,
                    prompt=prompt,
                    aspect_ratio=aspect,
                )

        except Exception:
            logger.debug("OpenAI-compatible image generation failed", exc_info=True)
            return error_response(
                error="OpenAI-compatible image generation request failed",
                error_type="api_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )


def register(ctx: Any) -> None:
    """Register OpenAI-compatible image gen provider with Hermes Agent."""
    ctx.register_image_gen_provider(OpenAICompatibleImageGenProvider())
