from __future__ import annotations

import tempfile
from pathlib import Path, PureWindowsPath
import unittest
from unittest.mock import Mock, patch

import start_mcp


class StartMcpTest(unittest.TestCase):
    def test_ensure_dependencies_skips_install_when_stamp_matches(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            venv_dir = project_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("", encoding="utf-8")
            project_dir.joinpath("pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")
            stamp = start_mcp._dependency_stamp(project_dir)
            start_mcp._dependency_stamp_path(venv_python).write_text(stamp, encoding="utf-8")

            with patch("start_mcp.subprocess.run") as run_mock:
                start_mcp._ensure_dependencies(project_dir, venv_python)

        run_mock.assert_not_called()

    def test_ensure_dependencies_writes_stamp_after_install(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            project_dir = Path(tmpdir) / "project"
            venv_dir = project_dir / ".venv"
            venv_python = venv_dir / "bin" / "python"
            venv_python.parent.mkdir(parents=True)
            venv_python.write_text("", encoding="utf-8")
            project_dir.joinpath("pyproject.toml").write_text("[project]\nname='demo'\n", encoding="utf-8")

            with patch("start_mcp.subprocess.run", return_value=Mock(returncode=0)) as run_mock:
                start_mcp._ensure_dependencies(project_dir, venv_python)

            stamp_path = start_mcp._dependency_stamp_path(venv_python)
            self.assertTrue(stamp_path.exists())
            self.assertEqual(stamp_path.read_text(encoding="utf-8"), start_mcp._dependency_stamp(project_dir))
            run_mock.assert_called_once()

    def test_launch_server_uses_subprocess_on_windows(self) -> None:
        with (
            patch("start_mcp.os.name", "nt"),
            patch("start_mcp.subprocess.run", return_value=Mock(returncode=0)) as run_mock,
        ):
            with self.assertRaises(SystemExit) as exc_info:
                start_mcp._launch_server(PureWindowsPath("C:/repo/.venv/Scripts/python.exe"))

        self.assertEqual(exc_info.exception.code, 0)
        windows_python = str(PureWindowsPath("C:/repo/.venv/Scripts/python.exe"))
        run_mock.assert_called_once_with(
            [windows_python, "-m", "ask_user_via_feishu"],
            check=False,
        )

    def test_launch_server_execs_on_posix(self) -> None:
        with (
            patch("start_mcp.os.name", "posix"),
            patch("start_mcp.os.execv") as execv_mock,
        ):
            start_mcp._launch_server(Path("/repo/.venv/bin/python"))

        execv_mock.assert_called_once_with(
            "/repo/.venv/bin/python",
            ["/repo/.venv/bin/python", "-m", "ask_user_via_feishu"],
        )
