from pathlib import Path
import plistlib

from hermes_cli import macos_identity


def test_ensure_app_bundle_creates_named_python_symlink_and_plist(tmp_path):
    python = tmp_path / "python3.11"
    python.write_text("#!/bin/sh\n", encoding="utf-8")
    icon = tmp_path / "logo.png"
    icon.write_bytes(b"png")

    executable = macos_identity.ensure_app_bundle(
        base_dir=tmp_path / "apps",
        display_name="Hermes Gateway (coder)",
        python_path=python,
        icon_source=icon,
    )

    assert executable.name == "Hermes Gateway (coder)"
    assert executable.is_symlink()
    assert executable.resolve() == python

    app_dir = tmp_path / "apps" / "Hermes Gateway (coder).app"
    info = plistlib.loads((app_dir / "Contents" / "Info.plist").read_bytes())
    assert info["CFBundleName"] == "Hermes Gateway (coder)"
    assert info["CFBundleDisplayName"] == "Hermes Gateway (coder)"
    assert info["CFBundleExecutable"] == "Hermes Gateway (coder)"
    assert info["LSBackgroundOnly"] is True
    assert (app_dir / "Contents" / "Resources" / "Hermes.png").exists()


def test_executable_for_role_is_python_on_non_macos(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_identity.sys, "platform", "linux")

    assert macos_identity.executable_for_role(
        role="gateway",
        hermes_home=tmp_path,
        python_path="/usr/bin/python3",
    ) == "/usr/bin/python3"


def test_executable_for_role_creates_profile_scoped_macos_app(monkeypatch, tmp_path):
    monkeypatch.setattr(macos_identity.sys, "platform", "darwin")
    monkeypatch.setattr(macos_identity, "_generate_icns", lambda *args, **kwargs: False)
    python = tmp_path / "python"
    python.write_text("", encoding="utf-8")

    executable = Path(macos_identity.executable_for_role(
        role="gateway",
        hermes_home=tmp_path / "hermes-home",
        python_path=python,
        profile="family-agent",
    ))

    assert executable.name == "Hermes Gateway (family-agent)"
    assert executable.parent.parent.parent == tmp_path / "hermes-home" / "macos-apps" / "Hermes Gateway (family-agent).app"
