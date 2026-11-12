from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent

CHECK = """
import importlib.metadata, importlib.resources, pkgutil, sys
import coding_agent_cli
assert importlib.resources.files("coding_agent_cli").joinpath("py.typed").is_file()
assert not importlib.metadata.requires("coding-agent-cli")
for info in pkgutil.walk_packages(coding_agent_cli.__path__, "coding_agent_cli."):
    __import__(info.name)
assert not any(name == "refract" or name.startswith("refract.") for name in sys.modules)
print("ok")
"""

uv = shutil.which("uv")
pytestmark = pytest.mark.skipif(uv is None, reason="uv is not installed")


@pytest.fixture(scope="module")
def dist(tmp_path_factory: pytest.TempPathFactory) -> Path:
    out = tmp_path_factory.mktemp("dist")
    subprocess.run([str(uv), "build", "--out-dir", str(out), str(ROOT)], check=True, capture_output=True)
    return out


def _python(venv: Path) -> Path:
    return venv / ("Scripts/python.exe" if sys.platform == "win32" else "bin/python")


def test_build_makes_one_wheel_and_one_sdist(dist: Path) -> None:
    assert len(list(dist.glob("*.whl"))) == 1
    assert len(list(dist.glob("*.tar.gz"))) == 1


@pytest.mark.parametrize("pattern", ["*.whl", "*.tar.gz"])
def test_install_and_import(dist: Path, tmp_path: Path, pattern: str) -> None:
    artifact = next(dist.glob(pattern))
    venv = tmp_path / "venv"
    env = {key: value for key, value in os.environ.items() if key != "VIRTUAL_ENV"}
    subprocess.run([str(uv), "venv", "--quiet", str(venv)], check=True, env=env)
    python = _python(venv)
    subprocess.run([str(uv), "pip", "install", "--quiet", "--python", str(python), str(artifact)], check=True, env=env)
    out = subprocess.run([str(python), "-I", "-c", CHECK], check=True, capture_output=True, text=True, cwd=tmp_path)
    assert out.stdout.strip() == "ok"
