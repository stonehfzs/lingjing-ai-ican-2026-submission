"""No network: prevent silent provider model fallbacks in the adapter."""
import unittest
from gateway import Gateway


class ImageModelMappingTest(unittest.TestCase):
    def test_image_canonical_name_overrides_caller_default(self):
        for backend, picker, canonical in [
            ("openai", "g-image-2", "gpt-image-2"),
            ("nano_banana", "banana-pro", "nano_banana_2"),
            ("seedream", "seedream-picker", "doubao-seedream-4-5-251128"),
        ]:
            with self.subTest(backend=backend):
                body = Gateway.build_body("image", {"id": picker, "model_name": canonical, "backend": backend},
                    "A paper boat", {"model_name": "wrong-default", "resolution": "2K"}, [], "test.png")
                self.assertEqual(body["model_id"], canonical)
                self.assertEqual(body["params"]["model_name"], canonical)
                if backend in ("openai", "seedream"):
                    self.assertEqual(body["params"]["resolution"], "2k")

    def test_midjourney_2k_normalization(self):
        body = Gateway.build_body("image", {"id": "midjourney-8.2", "backend": "midjourney"},
            "A paper boat", {"resolution": "2K"}, [], "test.png")
        self.assertEqual(body["params"]["clarity"], "2k")
        self.assertNotIn("resolution", body["params"])
        self.assertEqual(body["prompt"], "A paper boat --hd")


if __name__ == "__main__":
    unittest.main()
