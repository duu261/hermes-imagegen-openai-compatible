import base64
import importlib.util
import os
import struct
import sys
import tempfile
import types
import unittest
from pathlib import Path
from unittest.mock import patch

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("image_provider", ROOT / "__init__.py")
provider = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = provider
spec.loader.exec_module(provider)

PNG_1536x1024 = b"\x89PNG\r\n\x1a\n" + b"\x00\x00\x00\rIHDR" + struct.pack(">II", 1536, 1024) + b"\x08\x06\x00\x00\x00"


class FakeAPIStatusError(Exception):
    def __init__(self, status_code, body=None, text=""):
        super().__init__(text)
        self.status_code = status_code
        self.body = body
        self.response = types.SimpleNamespace(text=text)


class FakeAPIConnectionError(Exception):
    pass


class FakeAPITimeoutError(FakeAPIConnectionError):
    pass


class FakeImage:
    b64_json = base64.b64encode(PNG_1536x1024).decode()
    url = None
    revised_prompt = None


class FakeImages:
    def __init__(self, raise_exc=None):
        self.generate_calls = []
        self.edit_calls = []
        self.raise_exc = raise_exc

    def _respond(self):
        if self.raise_exc is not None:
            raise self.raise_exc
        return types.SimpleNamespace(data=[FakeImage()])

    def generate(self, **kwargs):
        self.generate_calls.append(kwargs)
        return self._respond()

    def edit(self, **kwargs):
        self.edit_calls.append(kwargs)
        return self._respond()


class FakeClient:
    instances = []
    raise_exc: "Exception | None" = None

    def __init__(self, *, base_url, api_key, default_headers=None):
        self.base_url = base_url
        self.api_key = api_key
        self.default_headers = default_headers or {}
        self.images = FakeImages(self.__class__.raise_exc)
        self.__class__.instances.append(self)


class Ctx:
    def __init__(self, settings=None):
        self.settings = settings or {}
        self.registered = []

    def get_config(self, key, default=None):
        return self.settings.get(key, default)

    def register_image_gen_provider(self, item):
        self.registered.append(item)


class ProviderTests(unittest.TestCase):
    def setUp(self):
        FakeClient.instances.clear()
        FakeClient.raise_exc = None
        self.tmp = tempfile.TemporaryDirectory()
        self.saved_path = Path(self.tmp.name) / "out.png"
        self.saved_path.write_bytes(PNG_1536x1024)
        self.env = patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_API_KEY": "test-key-123456"}, clear=True)
        self.env.start()
        fake_openai = types.SimpleNamespace(
            OpenAI=FakeClient, APIStatusError=FakeAPIStatusError,
            APIConnectionError=FakeAPIConnectionError, APITimeoutError=FakeAPITimeoutError)
        self.openai = patch.dict(sys.modules, {"openai": fake_openai})
        self.openai.start()
        self.saved = patch.object(provider, "save_b64_image", return_value=self.saved_path)
        self.saved.start()

    def tearDown(self):
        self.saved.stop()
        self.openai.stop()
        self.env.stop()
        self.tmp.cleanup()

    def make(self, **settings):
        settings.setdefault("base_url", "https://gateway.example/v1")
        return provider.OpenAICompatibleImageGenProvider(Ctx(settings))

    def call(self, client_index=0, kind="generate_calls"):
        return getattr(FakeClient.instances[client_index].images, kind)[0]

    # -- configuration ---------------------------------------------------------------

    def test_settings_base_url_and_default_key_env(self):
        result = self.make().generate("draw", "landscape", model="gpt-image-2")
        self.assertTrue(result["success"], result)
        client = FakeClient.instances[0]
        self.assertEqual(client.base_url, "https://gateway.example/v1")
        self.assertEqual(client.api_key, "test-key-123456")
        self.assertEqual(client.default_headers["User-Agent"], provider.USER_AGENT)

    def test_key_env_points_at_another_variable(self):
        with patch.dict(os.environ, {"MY_GATEWAY_KEY": "other-key-abcdef"}):
            self.make(key_env="MY_GATEWAY_KEY").generate("draw", model="gpt-image-2")
        self.assertEqual(FakeClient.instances[0].api_key, "other-key-abcdef")

    def test_legacy_env_base_url_still_works(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_BASE_URL": "https://legacy.example/v1"}):
            p = provider.OpenAICompatibleImageGenProvider(Ctx())
            self.assertTrue(p.is_available())
            self.assertTrue(p.generate("draw", model="gpt-image-2")["success"])
        self.assertEqual(FakeClient.instances[0].base_url, "https://legacy.example/v1")

    def test_unconfigured_is_unavailable_with_actionable_error(self):
        p = provider.OpenAICompatibleImageGenProvider(Ctx())
        self.assertFalse(p.is_available())
        result = p.generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertIn("plugins.entries.openai-compatible.settings.base_url", result["error"])

    def test_remote_http_rejected_localhost_allowed(self):
        result = self.make(base_url="http://gateway.example/v1").generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertTrue(self.make(base_url="http://127.0.0.1:8317/v1").generate("draw")["success"])

    def test_http_allowed_only_for_private_networks(self):
        allowed = ["http://127.0.0.1:8317/v1", "http://localhost:8317/v1", "http://[::1]:8317/v1",
                   "http://10.0.0.5:8317/v1", "http://172.29.0.4:8317/v1", "http://192.168.1.10:8317/v1",
                   "http://100.64.0.1:8317/v1", "http://100.127.255.254:8317/v1",
                   "http://[fd7a:115c:a1e0::1]:8317/v1", "http://[::ffff:192.168.1.10]:8317/v1"]
        refused = ["http://8.8.8.8/v1", "http://169.254.169.254/v1", "http://[fe80::1]/v1",
                   "http://100.128.0.1/v1", "http://172.32.0.1/v1", "http://0.0.0.0/v1",
                   "http://224.0.0.1/v1", "http://[::ffff:8.8.8.8]/v1", "http://[2001:4860::8888]/v1",
                   "ftp://127.0.0.1/v1"]
        for url in allowed:
            self.assertEqual(provider._validate_base_url(url), url.rstrip("/"), url)
        for url in refused:
            with self.assertRaises(ValueError, msg=url):
                provider._validate_base_url(url)

    def test_http_hostname_requires_every_address_private(self):
        def fake_resolve(addresses):
            return lambda host, port, type=0: [(2, 1, 6, "", (a, 0)) for a in addresses]
        with patch.object(provider.socket, "getaddrinfo", fake_resolve(["172.29.0.4"])):
            self.assertEqual(provider._validate_base_url("http://cpa:8317/v1"), "http://cpa:8317/v1")
        with patch.object(provider.socket, "getaddrinfo", fake_resolve(["10.0.0.2", "93.184.216.34"])):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://mixed.example/v1")
        with patch.object(provider.socket, "getaddrinfo", side_effect=provider.socket.gaierror()):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://unresolvable.invalid/v1")
        with patch.object(provider.socket, "getaddrinfo", fake_resolve([])):
            with self.assertRaises(ValueError):
                provider._validate_base_url("http://empty.example/v1")

    def test_https_never_needs_resolution(self):
        with patch.object(provider.socket, "getaddrinfo", side_effect=AssertionError("no DNS for https")):
            self.assertEqual(provider._validate_base_url("https://8.8.8.8/v1"), "https://8.8.8.8/v1")

    def test_key_with_newline_rejected_without_echo(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_API_KEY": "secret-value\ninjected: header"}):
            result = self.make().generate("draw")
        self.assertEqual(result["error_type"], "configuration_error")
        self.assertNotIn("secret-value", result["error"])

    # -- model and quality mapping ---------------------------------------------------

    def test_gpt_image_default_quality_and_size(self):
        self.make().generate("draw", "landscape", model="gpt-image-2")
        call = self.call()
        self.assertEqual((call["model"], call["size"], call["quality"], call["n"]),
                         ("gpt-image-2", "1536x1024", "medium", 1))

    def test_quality_setting_and_suffix(self):
        self.make(quality="high").generate("draw", "square", model="gpt-image-2.5-sunburst")
        self.assertEqual(self.call()["quality"], "high")
        FakeClient.instances.clear()
        result = self.make().generate("draw", "portrait", model="gpt-image-2.5-flare-max")
        call = self.call()
        self.assertEqual((call["model"], call["quality"], call["size"]), ("gpt-image-2.5-flare", "max", "1024x1536"))
        self.assertEqual(result["model"], "gpt-image-2.5-flare-max")

    def test_unknown_quality_setting_falls_back_to_medium(self):
        self.make(quality="ultra").generate("draw", model="gpt-image-2")
        self.assertEqual(self.call()["quality"], "medium")

    def test_gateway_defined_model_sent_verbatim_without_size_or_quality(self):
        self.make().generate("draw", model="gemini-3.1-flash-image")
        call = self.call()
        self.assertEqual(call["model"], "gemini-3.1-flash-image")
        self.assertNotIn("size", call)
        self.assertNotIn("quality", call)

    def test_legacy_model_env_used_when_hermes_passes_none(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2.5-flare"}):
            self.make().generate("draw")
        self.assertEqual(self.call()["model"], "gpt-image-2.5-flare")

    def test_image_gen_model_beats_legacy_env(self):
        with patch.dict(os.environ, {"OPENAI_COMPAT_IMAGE_MODEL": "gpt-image-2.5-flare"}):
            self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(self.call()["model"], "gpt-image-2")

    # -- results ---------------------------------------------------------------------

    def test_success_reports_requested_and_output_size(self):
        result = self.make().generate("draw", "square", model="gpt-image-2")
        self.assertEqual(result["requested_size"], "1024x1024")
        self.assertEqual(result["requested_quality"], "medium")
        self.assertEqual(result["output_size"], "1536x1024")
        self.assertEqual(result["api_model"], "gpt-image-2")

    def test_edit_uses_multipart_sources(self):
        source = "data:image/png;base64," + base64.b64encode(b"source").decode()
        result = self.make().generate("edit", "portrait", image_url=source, model="gpt-image-2")
        self.assertTrue(result["success"], result)
        self.assertEqual(result["modality"], "image")
        call = self.call(kind="edit_calls")
        self.assertEqual(call["image"].read(), b"source")

    # -- errors ----------------------------------------------------------------------

    def test_missing_local_source_is_io_error_before_any_request(self):
        result = self.make().generate("edit", image_url="/nonexistent/cache/image.png")
        self.assertEqual(result["error_type"], "io_error")
        self.assertIn("/nonexistent/cache/image.png", result["error"])
        self.assertIn("pruned", result["error"])
        self.assertEqual(FakeClient.instances, [])

    def test_invalid_data_url_is_io_error(self):
        result = self.make().generate("edit", image_url="data:image/png;base64,not-valid!")
        self.assertEqual(result["error_type"], "io_error")

    def test_http_error_passes_status_and_message(self):
        FakeClient.raise_exc = FakeAPIStatusError(
            400, {"error": {"message": "model gpt-image-9 is not enabled on this channel"}})
        result = self.make().generate("draw", model="gpt-image-2")
        self.assertEqual(result["error_type"], "api_error")
        self.assertIn("HTTP 400", result["error"])
        self.assertIn("not enabled on this channel", result["error"])

    def test_auth_error_redacts_key(self):
        FakeClient.raise_exc = FakeAPIStatusError(
            401, {"error": {"message": "invalid token test-key-123456 Bearer abc.def"}})
        result = self.make().generate("draw")
        self.assertEqual(result["error_type"], "auth_error")
        self.assertNotIn("test-key-123456", result["error"])
        self.assertNotIn("abc.def", result["error"])

    def test_cloudflare_1010_hint(self):
        FakeClient.raise_exc = FakeAPIStatusError(403, None, "error code: 1010")
        result = self.make().generate("draw")
        self.assertIn("Cloudflare", result["error"])

    def test_connection_and_timeout_errors(self):
        FakeClient.raise_exc = FakeAPITimeoutError()
        self.assertEqual(self.make().generate("draw")["error_type"], "timeout")
        FakeClient.raise_exc = FakeAPIConnectionError()
        self.assertEqual(self.make().generate("draw")["error_type"], "connection_error")

    def test_empty_prompt(self):
        self.assertEqual(self.make().generate("  ")["error_type"], "invalid_argument")

    # -- helpers and registration ----------------------------------------------------

    def test_image_dims_png_and_jpeg(self):
        self.assertEqual(provider._image_dims(PNG_1536x1024), "1536x1024")
        jpeg = b"\xff\xd8" + b"\xff\xc0\x00\x11\x08" + struct.pack(">HH", 768, 1254) + b"\x00" * 8
        self.assertEqual(provider._image_dims(jpeg), "1254x768")
        self.assertIsNone(provider._image_dims(b"not an image"))

    def test_registers_provider_with_ctx(self):
        ctx = Ctx({"base_url": "https://gateway.example/v1"})
        provider.register(ctx)
        self.assertEqual(len(ctx.registered), 1)
        item = ctx.registered[0]
        self.assertEqual(item.name, "openai-compatible")
        self.assertEqual(item.capabilities()["max_reference_images"], 16)
        self.assertTrue(item.is_available())

    def test_user_agent_tracks_manifest_version(self):
        manifest = (ROOT / "plugin.yaml").read_text(encoding="utf-8")
        version = next(line.split(":", 1)[1].strip() for line in manifest.splitlines()
                       if line.startswith("version:"))
        self.assertEqual(version, provider.PLUGIN_VERSION)
        self.assertTrue(provider.USER_AGENT.endswith("/" + version))


if __name__ == "__main__":
    unittest.main()
