"""OpenAI-Compatible Image Generation Provider Plugin for Hermes Agent.

Provides a generic backend for any OpenAI-compatible `/v1/images/generations` &
`/v1/images/edits` API (LiteLLM, LocalAI, vLLM, private reverse proxies, etc.).
"""

from __future__ import annotations

import io
import logging
import os
from typing import Any, Dict, List, Optional, Tuple

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

DEFAULT_MODEL = "image-model-v1"
DEFAULT_BASE_URL = "http://localhost:8000/v1"


def _load_image_bytes(ref: str) -> Tuple[bytes, str]:
    """Load image bytes from a URL, base64 data URI, or local file path."""
    ref = ref.strip()
    lower = ref.lower()
    if lower.startswith(("http://", "https://")):
        import requests

        resp = requests.get(ref, timeout=60)
        resp.raise_for_status()
        name = ref.split("?", 1)[0].rsplit("/", 1)[-1] or "image.png"
        return resp.content, name
    if lower.startswith("data:"):
        import base64

        header, _, b64 = ref.partition(",")
        ext = "png"
        if "image/" in header:
            ext = header.split("image/", 1)[1].split(";", 1)[0] or "png"
        return base64.b64decode(b64), f"image.{ext}"

    from agent.file_safety import raise_if_read_blocked

    raise_if_read_blocked(ref)
    with open(ref, "rb") as fh:
        data = fh.read()
    name = os.path.basename(ref) or "image.png"
    return data, name


class OpenAICompatibleImageGenProvider(ImageGenProvider):
    """Generic OpenAI-compatible image generation and editing backend."""

    @property
    def name(self) -> str:
        return "openai-compatible"

    @property
    def display_name(self) -> str:
        return "OpenAI-Compatible"

    def is_available(self) -> bool:
        """Check if openai package is available."""
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
                    "prompt": "Base URL (default: http://localhost:8000/v1)",
                    "url": "http://localhost:8000/v1",
                    "optional": True,
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
        return {"modalities": ["text", "image"], "max_reference_images": 4}

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

        base_url = os.environ.get("OPENAI_COMPAT_IMAGE_BASE_URL", DEFAULT_BASE_URL).rstrip("/")
        api_key = os.environ.get("OPENAI_COMPAT_IMAGE_API_KEY", "dummy")

        model_override = os.environ.get("OPENAI_COMPAT_IMAGE_MODEL", "").strip()
        if model_override:
            model = model_override
        else:
            raw_model = (kwargs.get("model") or "").strip()
            if raw_model and not raw_model.startswith("fal-ai/"):
                model = raw_model
            else:
                model = DEFAULT_MODEL

        # Collect source images for image-to-image / edit
        sources: List[str] = []
        if isinstance(image_url, str) and image_url.strip():
            sources.append(image_url.strip())
        for ref in (normalize_reference_images(reference_image_urls) or []):
            sources.append(ref)
        sources = sources[:4]
        is_edit = bool(sources)
        modality = "image" if is_edit else "text"

        try:
            client = openai.OpenAI(base_url=base_url, api_key=api_key)

            if is_edit:
                files = []
                for ref in sources:
                    data, fname = _load_image_bytes(ref)
                    bio = io.BytesIO(data)
                    bio.name = fname
                    files.append(bio)

                response = client.images.edit(
                    model=model,
                    image=files if len(files) > 1 else files[0],
                    prompt=prompt,
                    n=1,
                )
            else:
                response = client.images.generate(
                    model=model,
                    prompt=prompt,
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

        except Exception as exc:
            logger.debug("OpenAI-compatible image generation failed", exc_info=True)
            return error_response(
                error=f"OpenAI-compatible image generation failed: {exc}",
                error_type="api_error",
                provider=self.name,
                model=model,
                prompt=prompt,
                aspect_ratio=aspect,
            )


def register(ctx: Any) -> None:
    """Register OpenAI-compatible image gen provider with Hermes Agent."""
    ctx.register_image_gen_provider(OpenAICompatibleImageGenProvider())
