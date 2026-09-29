import base64
import importlib.util
import os
import sys
import types
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_provider", ROOT / "__init__.py")
provider = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = provider
spec.loader.exec_module(provider)


class FakeImage:
    b64_json = base64.b64encode(b"fake-image").decode()
    url = None
    revised_prompt = None


class FakeImages:
    def __init__(self):
        self.generate_calls = []
        self.edit_calls = []

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return types.SimpleNamespace(data=[FakeImage()])

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return types.SimpleNamespace(data=[FakeImage()])


class FakeClient:
    instances = []

    def __init__(self, *, base_url, api_key):
        self.base_url = base_url
        self.api_key = api_key
        self.images = FakeImages()
        self.__class__.instances.append(self)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        self.env = patch.dict(os.environ, {
            "OPENAI_COMPAT_IMAGE_BASE_URL": "https://gateway.example/v1",
            "OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2",
            "OPENAI_COMPAT_IMAGE_API_KEY": "test-key",
        }, clear=True)
        self.env.start()
        self.openai = patch.dict(sys.modules, {"openai": types.SimpleNamespace(OpenAI=FakeClient)})
        self.openai.start()
        self.saved = patch.object(provider, "save_b64_image", return_value=Path("/tmp/fake-image.png"))
        self.saved.start()

    def tearDown(self):
        self.saved.stop()
        self.openai.stop()
        self.env.stop()

    def test_is_available_requires_explicit_endpoint(self):
        self.assertTrue(provider.OpenAICompatibleImageGenProvider().is_available())
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_BASE_URL": ""}, clear=False):
            self.assertFalse(provider.OpenAICompatibleImageGenProvider().is_available())

    def test_gpt_image_2_generation_maps_aspect_ratio_and_quality(self):
        result = provider.OpenAICompatibleImageGenProvider().generate(
            "draw a test",
            aspect_ratio="landscape",
        )
        self.assertTrue(result["success"])
        self.assertEqual(result["model"], "gpt-image-2")
        call = FakeClient.instances[0].images.generate_calls[0]
        self.assertEqual(call["model"], "gpt-image-2")
        self.assertEqual(call["size"], "1536x1024")
        self.assertEqual(call["quality"], "medium")
        self.assertEqual(call["n"], 1)

    def test_gpt_image_2_quality_tier_uses_api_model(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2-high"}, clear=False):
            result = provider.OpenAICompatibleImageGenProvider().generate("draw a test", "square")
        self.assertTrue(result["success"])
        self.assertEqual(result["model"], "gpt-image-2-high")
        call = FakeClient.instances[0].images.generate_calls[0]
        self.assertEqual(call["model"], "gpt-image-2")
        self.assertEqual(call["quality"], "high")
        self.assertEqual(call["size"], "1024x1024")

    def test_gpt_image_2_5_models_default_to_medium_quality(self):
        for model in ("gpt-image-2.5", "gpt-image-2.5-flare", "gpt-image-2.5-sunburst"):
            FakeClient.instances.clear()
            with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": model}, clear=False):
                result = provider.OpenAICompatibleImageGenProvider().generate("draw a test", "portrait")
            self.assertTrue(result["success"], model)
            call = FakeClient.instances[0].images.generate_calls[0]
            self.assertEqual(call["model"], model)
            self.assertEqual(call["quality"], "medium")
            self.assertEqual(call["size"], "1024x1536")

    def test_unknown_model_sends_no_size_or_quality(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "custom-image-model"}, clear=False):
            result = provider.OpenAICompatibleImageGenProvider().generate("draw a test", "square")
        self.assertTrue(result["success"])
        call = FakeClient.instances[0].images.generate_calls[0]
        self.assertNotIn("quality", call)
        self.assertNotIn("size", call)

    def test_gpt_image_2_edit_uses_multipart_input_and_size(self):
        source = "data:image/png;base64," + base64.b64encode(b"source").decode()
        result = provider.OpenAICompatibleImageGenProvider().generate(
            "edit a test",
            aspect_ratio="portrait",
            image_url=source,
        )
        self.assertTrue(result["success"])
        call = FakeClient.instances[0].images.edit_calls[0]
        self.assertEqual(call["model"], "gpt-image-2")
        self.assertEqual(call["size"], "1024x1536")
        self.assertEqual(call["quality"], "medium")
        self.assertEqual(call["n"], 1)
        self.assertEqual(len(call["image"].read()), len(b"source"))

    def test_remote_base_url_must_use_https(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_BASE_URL": "http://gateway.example/v1"}, clear=False):
            result = provider.OpenAICompatibleImageGenProvider().generate("draw a test")
        self.assertFalse(result["success"])
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertIn("HTTPS", result["error"])

    def test_source_data_url_rejects_invalid_base64(self):
        with self.assertRaisesRegex(ValueError, "invalid base64"):
            provider._load_image_bytes("data:image/png;base64,not-valid!")

    def test_registers_image_generation_provider(self):
        seen = []

        class Context:
            def register_image_gen_provider(self, item):
                seen.append(item)

        provider.register(Context())
        self.assertEqual(len(seen), 1)
        self.assertEqual(seen[0].name, "openai-compatible")
        self.assertEqual(seen[0].capabilities()["max_reference_images"], 16)


if __name__ == "__main__":
    unittest.main()
