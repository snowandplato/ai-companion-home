import sys
import types
import unittest


if "dotenv" not in sys.modules:
    dotenv_stub = types.ModuleType("dotenv")
    dotenv_stub.load_dotenv = lambda *args, **kwargs: None
    sys.modules["dotenv"] = dotenv_stub

if "httpx" not in sys.modules:
    httpx_stub = types.ModuleType("httpx")
    httpx_stub.Response = type("Response", (), {})
    httpx_stub.AsyncClient = type("AsyncClient", (), {})
    sys.modules["httpx"] = httpx_stub

from router import RouterService


class ModelListingTests(unittest.TestCase):
    def setUp(self):
        self.service = RouterService()

    def test_builds_anthropic_model_request(self):
        provider, url, headers, params = self.service.prepare_model_list_request("sk-ant-example")

        self.assertEqual(provider, "anthropic")
        self.assertTrue(url.endswith("/v1/models"))
        self.assertEqual(headers["x-api-key"], "sk-ant-example")
        self.assertEqual(headers["anthropic-version"], "2023-06-01")
        self.assertEqual(params, {"limit": 1000})

    def test_builds_zenmux_model_request(self):
        for key in ("sk-payg-example", "sk-ai-v1-example"):
            with self.subTest(key=key):
                provider, url, headers, params = self.service.prepare_model_list_request(key)
                self.assertEqual(provider, "zenmux")
                self.assertTrue(url.endswith("/models"))
                self.assertEqual(headers["Authorization"], f"Bearer {key}")
                self.assertEqual(params, {})

    def test_recognizes_zenmux_subscription_key(self):
        provider, url, _, _ = self.service.prepare_model_list_request("sk-ss-v1-example")

        self.assertEqual(provider, "zenmux")
        self.assertTrue(url.endswith("/models"))

    def test_openai_models_exclude_non_chat_endpoints(self):
        payload = {
            "data": [
                {"id": "gpt-5.1"},
                {"id": "gpt-image-2"},
                {"id": "text-embedding-3-small"},
                {"id": "o4-mini"},
                {"id": "gpt-5.1"},
            ]
        }

        self.assertEqual(
            self.service.extract_model_ids(payload, "openai"),
            ["gpt-5.1", "o4-mini"],
        )


if __name__ == "__main__":
    unittest.main()
