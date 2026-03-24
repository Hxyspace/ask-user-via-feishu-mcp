#!/usr/bin/env python3
from __future__ import annotations

import hashlib
import os
import subprocess
import sys
from pathlib import Path


DEFAULT_PIP_INDEX_URL = "https://pypi.tuna.tsinghua.edu.cn/simple/"
DEPENDENCY_STAMP_VERSION = "1"


def _venv_python_path(venv_dir: Path) -> Path:
    if os.name == "nt":
        return venv_dir / "Scripts" / "python.exe"
    return venv_dir / "bin" / "python"


def _ensure_venv(venv_dir: Path) -> Path:
    venv_python = _venv_python_path(venv_dir)
    if venv_python.exists():
        return venv_python
    subprocess.run([sys.executable, "-m", "venv", str(venv_dir)], check=True)
    return venv_python


def _dependency_stamp(project_dir: Path) -> str:
    pyproject_bytes = (project_dir / "pyproject.toml").read_bytes()
    digest = hashlib.sha256()
    digest.update(DEPENDENCY_STAMP_VERSION.encode("utf-8"))
    digest.update(b"\n")
    digest.update(pyproject_bytes)
    return digest.hexdigest()


def _dependency_stamp_path(venv_python: Path) -> Path:
    return venv_python.parent.parent / ".ask_user_via_feishu_deps_stamp"


def _ensure_dependencies(project_dir: Path, venv_python: Path) -> None:
    expected_stamp = _dependency_stamp(project_dir)
    stamp_path = _dependency_stamp_path(venv_python)
    if stamp_path.exists() and stamp_path.read_text(encoding="utf-8").strip() == expected_stamp:
        return
    pip_index_url = os.environ.get("PIP_INDEX_URL", DEFAULT_PIP_INDEX_URL)
    install_env = dict(os.environ)
    install_env.setdefault("PIP_DISABLE_PIP_VERSION_CHECK", "1")
    install = subprocess.run(
        [
            str(venv_python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--quiet",
            "-e",
            str(project_dir),
            "-i",
            pip_index_url,
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.PIPE,
        text=True,
        check=False,
        env=install_env,
    )
    if install.returncode == 0:
        stamp_path.write_text(expected_stamp, encoding="utf-8")
        return
    sys.stderr.write("Failed to install ask-user-via-feishu dependencies.\n")
    if install.stderr:
        sys.stderr.write(install.stderr)
    raise SystemExit(install.returncode)


def _launch_server(venv_python: Path) -> None:
    argv = [str(venv_python), "-m", "ask_user_via_feishu"]
    if os.name == "nt":
        result = subprocess.run(argv, check=False)
        raise SystemExit(result.returncode)
    os.execv(str(venv_python), argv)


def main() -> None:
    project_dir = Path(__file__).resolve().parent
    venv_dir = project_dir / ".venv"
    venv_python = _ensure_venv(venv_dir)
    _ensure_dependencies(project_dir, venv_python)
    _launch_server(venv_python)


if __name__ == "__main__":
    main()
