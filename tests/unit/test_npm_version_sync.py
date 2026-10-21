"""The npx launcher must ship exactly the version the Python package is.

`npm/package.json` pins `uwr==<version>` when it shells out to uvx/uv/pipx, so a
version skew silently launches an older server. Keep the guard next to the
released artifacts rather than relying on the release workflow alone.
"""

from __future__ import annotations

import json
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
NPM_DIR = REPO_ROOT / "npm"


def _project_version() -> str:
    with (REPO_ROOT / "pyproject.toml").open("rb") as handle:
        return tomllib.load(handle)["project"]["version"]


def _npm_package() -> dict:
    return json.loads((NPM_DIR / "package.json").read_text(encoding="utf-8"))


def test_npm_launcher_version_matches_pyproject() -> None:
    assert _npm_package()["version"] == _project_version()


def test_uv_pin_has_sha256_for_every_supported_asset() -> None:
    pin = json.loads((NPM_DIR / "lib" / "uv-pin.json").read_text(encoding="utf-8"))
    assert pin["version"]
    assets = pin["assets"]
    assert set(assets) == {
        "linux-x64",
        "linux-arm64",
        "darwin-x64",
        "darwin-arm64",
        "win32-x64",
    }
    for key, entry in assets.items():
        digest = entry["sha256"]
        assert len(digest) == 64, key
        assert all(ch in "0123456789abcdef" for ch in digest), key
        assert entry["asset"].startswith("uv-"), key


def test_npm_launcher_is_dependency_free_and_declares_expected_files() -> None:
    pkg = _npm_package()
    assert pkg["name"] == "uwr"
    assert pkg["bin"] == {"uwr": "bin/uwr.js"}
    assert "dependencies" not in pkg and "devDependencies" not in pkg
    assert pkg["license"] == "MIT"
    assert pkg["publishConfig"] == {"access": "public", "provenance": True}
    assert (NPM_DIR / pkg["bin"]["uwr"]).is_file()
    assert (NPM_DIR / "LICENSE").is_file()
    for entry in pkg["files"]:
        assert (NPM_DIR / entry).exists(), f"package.json files entry missing: {entry}"
