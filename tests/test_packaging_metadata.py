from pathlib import Path
import tomllib


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_faster_whisper_is_not_a_base_dependency():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    deps = data["project"]["dependencies"]

    assert not any(dep.startswith("faster-whisper") for dep in deps)

    voice_extra = data["project"]["optional-dependencies"]["voice"]
    assert any(dep.startswith("faster-whisper") for dep in voice_extra)


def test_manifest_includes_bundled_skills():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")

    assert "graft skills" in manifest
    assert "graft optional-skills" in manifest
    assert "graft ui-tui" in manifest
    assert "graft scripts" in manifest


def test_pyproject_includes_all_namespace_wildcards():
    data = tomllib.loads((REPO_ROOT / "pyproject.toml").read_text(encoding="utf-8"))
    includes = data["tool"]["setuptools"]["packages"]["find"]["include"]

    # Every top-level namespace with subpackages must have a .* wildcard
    # so that subpackages (e.g. hermes_cli.proxy) are included in the wheel.
    # This is an invariant — adding a subpackage to any of these namespaces
    # without adding the wildcard will silently drop it from packaged installs.
    namespaces_that_need_wildcards = {
        "acp_adapter",
        "agent",
        "gateway",
        "hermes_cli",
        "plugins",
        "providers",
        "tools",
        "tui_gateway",
    }

    for ns in namespaces_that_need_wildcards:
        assert f"{ns}.*" in includes, \
            f"{ns} is missing its .* wildcard in pyproject.toml packages.find.include"
