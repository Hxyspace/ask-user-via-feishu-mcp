from __future__ import annotations

import unittest
from pathlib import Path

from ask_user_via_feishu import __version__
from ask_user_via_feishu.config import SERVER_NAME, SERVER_VERSION


def _read_pyproject() -> str:
    pyproject_path = Path(__file__).resolve().parents[1] / "pyproject.toml"
    return pyproject_path.read_text(encoding="utf-8")


class PackageMetadataTest(unittest.TestCase):
    def test_pyproject_uses_dynamic_version_from_package(self) -> None:
        pyproject = _read_pyproject()

        self.assertIn('dynamic = ["version"]', pyproject)
        self.assertIn('version = {attr = "ask_user_via_feishu.__version__"}', pyproject)

    def test_server_metadata_matches_public_version(self) -> None:
        self.assertEqual(SERVER_NAME, "ask-user-via-feishu")
        self.assertEqual(SERVER_VERSION, __version__)
