#!/usr/bin/env python3
"""Hermetic test for Talon engine standalone provider resolution and CLI."""

import os
import sys
import unittest
from pathlib import Path

# Add src to sys.path
this_dir = Path(__file__).resolve().parent
pkg_root = this_dir.parent
sys.path.insert(0, str(pkg_root / "src"))

from engine import providers, config


class TestStandaloneEngine(unittest.TestCase):
    def test_http_endpoint_resolution(self):
        # http://host/v1::model should resolve with zero DB dependency
        base, model, key = providers.resolve_lane("http://127.0.0.1:8000/v1::meta-llama/Llama-3-8B")
        self.assertEqual(base, "http://127.0.0.1:8000/v1")
        self.assertEqual(model, "meta-llama/Llama-3-8B")
        self.assertTrue(bool(key))

    def test_ollama_resolution(self):
        # ollama/<model> should resolve keylessly
        base, model, key = providers.resolve_lane("ollama/qwen2.5:7b")
        self.assertTrue("11434" in base)
        self.assertEqual(model, "qwen2.5:7b")
        self.assertEqual(key, "local")

    def test_key_lookup_order(self):
        # Env var takes precedence
        os.environ["TEST_SECRET_KEY"] = "sk-test-secret"
        resolved = config.resolve_key("openrouter", "TEST_SECRET_KEY")
        self.assertEqual(resolved, "sk-test-secret")


if __name__ == "__main__":
    unittest.main()
