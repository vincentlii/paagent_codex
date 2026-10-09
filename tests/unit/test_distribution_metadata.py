from __future__ import annotations

import tomllib
from pathlib import Path


def _project_root() -> Path:
    return Path(__file__).resolve().parents[2]


def test_prompt_knowledge_is_declared_as_wheel_package_data() -> None:
    root = _project_root()
    metadata = tomllib.loads((root / "pyproject.toml").read_text("utf-8"))
    build_requirements = metadata["build-system"]["requires"]
    package_find = metadata["tool"]["setuptools"]["packages"]["find"]
    package_data = metadata["tool"]["setuptools"]["package-data"]

    assert any(requirement.startswith("setuptools>=77") for requirement in build_requirements)
    assert "prompt_engineering*" in package_find["include"]
    assert set(package_data["prompt_engineering"]) == {"**/*.txt", "**/*.md"}
    assert set(package_data["pa_agent.agent_runtime"]) == {
        "assets/**/*.md", "assets/**/*.toml", "assets/**/*.rules"
    }
    assert (root / "prompt_engineering" / "__init__.py").is_file()


def test_runtime_dependency_groups_match_supported_install_profiles() -> None:
    metadata = tomllib.loads(
        (_project_root() / "pyproject.toml").read_text("utf-8")
    )
    core = "\n".join(metadata["project"]["dependencies"])
    extras = metadata["project"]["optional-dependencies"]

    assert "PyQt6" not in core
    assert "MetaTrader5" not in core
    assert "curl_cffi" in "\n".join(extras["vps"])
    assert "pyqtgraph" in "\n".join(extras["desktop"])
    assert "e6f6aaa7de439ac6e454d9b26d2760ded8dc4923" in "\n".join(
        extras["tradingview"]
    )
