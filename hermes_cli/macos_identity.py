"""macOS process identity helpers for Hermes.

Hermes is a Python application, but launching long-lived services directly via
``python -m ...`` makes Activity Monitor and other macOS surfaces show every
process as ``python3.11`` with the generic Python icon.  On macOS the most
reliable lightweight fix is to launch the interpreter through a Hermes-named
executable inside a tiny ``.app`` bundle:

* the executable is a symlink to the active Python interpreter, so the runtime
  environment stays identical;
* the executable basename becomes the process name;
* the enclosing bundle gives macOS a stable display name and icon.

This module intentionally has no third-party dependencies and is a no-op on
non-macOS platforms.
"""

from __future__ import annotations

import os
import plistlib
import re
import shutil
import subprocess
import sys
from pathlib import Path


PROJECT_ROOT = Path(__file__).parent.parent.resolve()
DEFAULT_ICON_SOURCE = PROJECT_ROOT / "website" / "static" / "img" / "logo.png"


def is_macos() -> bool:
    return sys.platform == "darwin"


def _safe_component(value: str) -> str:
    value = re.sub(r"[^A-Za-z0-9._ ()-]+", "-", value).strip(" .-")
    return value or "Hermes"


def _bundle_identifier(display_name: str) -> str:
    suffix = re.sub(r"[^A-Za-z0-9]+", ".", display_name.lower()).strip(".")
    return f"ai.hermes.{suffix or 'agent'}"


def default_display_name(role: str = "agent", profile: str | None = None) -> str:
    """Return the human-facing macOS process/app name for a Hermes role."""
    normalized = (role or "agent").strip().lower().replace("_", "-")
    role_names = {
        "agent": "Hermes",
        "cli": "Hermes",
        "chat": "Hermes",
        "gateway": "Hermes Gateway",
        "cron": "Hermes Cron",
        "mcp": "Hermes MCP",
        "acp": "Hermes ACP",
    }
    name = role_names.get(normalized, f"Hermes {normalized.title()}")
    if profile and profile not in {"default", "root"}:
        return f"{name} ({profile})"
    return name


def _write_info_plist(app_dir: Path, display_name: str, executable_name: str, icon_stem: str) -> None:
    plist = {
        "CFBundleDevelopmentRegion": "en",
        "CFBundleDisplayName": display_name,
        "CFBundleExecutable": executable_name,
        "CFBundleIconFile": icon_stem,
        "CFBundleIdentifier": _bundle_identifier(display_name),
        "CFBundleInfoDictionaryVersion": "6.0",
        "CFBundleName": display_name,
        "CFBundlePackageType": "APPL",
        "CFBundleShortVersionString": "1.0",
        "CFBundleVersion": "1",
        # Hermes gateway/profile agents are background processes; this prevents
        # a Dock icon while preserving app metadata for Activity Monitor.
        "LSBackgroundOnly": True,
    }
    (app_dir / "Contents").mkdir(parents=True, exist_ok=True)
    with (app_dir / "Contents" / "Info.plist").open("wb") as handle:
        plistlib.dump(plist, handle, sort_keys=False)


def _run_quiet(cmd: list[str], timeout: float = 15) -> None:
    proc = subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        returncode = proc.wait(timeout=timeout)
    except subprocess.TimeoutExpired:
        proc.kill()
        proc.wait()
        raise
    if returncode != 0:
        raise subprocess.CalledProcessError(returncode, cmd)


def _generate_icns(icon_source: Path, resources_dir: Path, icon_stem: str) -> bool:
    """Generate ``Resources/<icon_stem>.icns`` from a PNG using macOS tools.

    Returns True when a real ``.icns`` exists, False when the caller should use
    the PNG fallback.  Failures are deliberately non-fatal: naming the process
    is more important than icon conversion, and iconutil/sips can be absent on
    stripped-down CI images.
    """
    if not icon_source.exists():
        return False
    icns = resources_dir / f"{icon_stem}.icns"
    if icns.exists():
        return True
    if not shutil.which("sips") or not shutil.which("iconutil"):
        return False

    iconset = resources_dir / f"{icon_stem}.iconset"
    try:
        if iconset.exists():
            shutil.rmtree(iconset)
        iconset.mkdir(parents=True, exist_ok=True)
        sizes = [16, 32, 64, 128, 256, 512]
        for size in sizes:
            _run_quiet(
                ["sips", "-z", str(size), str(size), str(icon_source), "--out", str(iconset / f"icon_{size}x{size}.png")],
                timeout=15,
            )
            _run_quiet(
                ["sips", "-z", str(size * 2), str(size * 2), str(icon_source), "--out", str(iconset / f"icon_{size}x{size}@2x.png")],
                timeout=15,
            )
        _run_quiet(
            ["iconutil", "-c", "icns", str(iconset), "-o", str(icns)],
            timeout=15,
        )
        return icns.exists()
    except Exception:
        return False
    finally:
        shutil.rmtree(iconset, ignore_errors=True)


def _link_virtualenv_payload(contents_dir: Path, python_executable: Path) -> None:
    """Make a symlinked venv interpreter keep seeing its venv packages.

    Python determines virtualenv membership from ``pyvenv.cfg`` near the
    executable path.  Once the executable is symlinked into ``Foo.app`` it no
    longer sees the original venv, so imports fall back to the base interpreter.
    Mirroring ``pyvenv.cfg`` and ``lib`` into ``Contents`` preserves the venv
    while keeping the Hermes-named executable basename.
    """
    venv_dir = python_executable.parent.parent
    pyvenv_cfg = venv_dir / "pyvenv.cfg"
    lib_dir = venv_dir / "lib"
    if not pyvenv_cfg.exists() or not lib_dir.exists():
        return

    shutil.copy2(pyvenv_cfg, contents_dir / "pyvenv.cfg")
    lib_link = contents_dir / "lib"
    if lib_link.exists() or lib_link.is_symlink():
        try:
            current_target = Path(os.readlink(lib_link)) if lib_link.is_symlink() else lib_link
            if current_target != lib_dir:
                if lib_link.is_dir() and not lib_link.is_symlink():
                    shutil.rmtree(lib_link)
                else:
                    lib_link.unlink()
        except OSError:
            lib_link.unlink(missing_ok=True)
    if not lib_link.exists():
        lib_link.symlink_to(lib_dir)


def ensure_app_bundle(
    *,
    base_dir: Path,
    display_name: str,
    python_path: str | Path | None = None,
    icon_source: Path | None = None,
) -> Path:
    """Create/update a named Hermes ``.app`` bundle and return its executable.

    ``base_dir`` should live in the relevant Hermes home so profile-specific
    services do not fight over bundle metadata.
    """
    python_executable = Path(python_path or sys.executable).absolute()
    app_dir = base_dir / f"{_safe_component(display_name)}.app"
    contents = app_dir / "Contents"
    macos_dir = contents / "MacOS"
    resources_dir = contents / "Resources"
    executable_name = _safe_component(display_name)
    executable_path = macos_dir / executable_name
    icon_stem = "Hermes"

    macos_dir.mkdir(parents=True, exist_ok=True)
    resources_dir.mkdir(parents=True, exist_ok=True)

    if executable_path.exists() or executable_path.is_symlink():
        try:
            current_target = Path(os.readlink(executable_path)) if executable_path.is_symlink() else executable_path
            if current_target != python_executable:
                executable_path.unlink()
        except OSError:
            executable_path.unlink(missing_ok=True)
    if not executable_path.exists():
        executable_path.symlink_to(python_executable)
    _link_virtualenv_payload(contents, python_executable)

    source = icon_source or DEFAULT_ICON_SOURCE
    icon_file_stem = icon_stem
    if not _generate_icns(source, resources_dir, icon_stem):
        # Best-effort fallback.  Some macOS surfaces accept PNG here; even when
        # they don't, the bundle remains valid and the process name is fixed.
        if source.exists():
            shutil.copy2(source, resources_dir / f"{icon_stem}.png")
            icon_file_stem = f"{icon_stem}.png"

    _write_info_plist(app_dir, display_name, executable_name, icon_file_stem)
    return executable_path


def executable_for_role(
    *,
    role: str,
    hermes_home: Path,
    python_path: str | Path | None = None,
    profile: str | None = None,
) -> str:
    """Return a macOS named executable for ``role``; otherwise return Python.

    This is safe to call from service generators on any platform.
    """
    if not is_macos():
        return str(python_path or sys.executable)
    display_name = default_display_name(role, profile)
    app_base = Path(hermes_home).resolve() / "macos-apps"
    return str(ensure_app_bundle(base_dir=app_base, display_name=display_name, python_path=python_path))


def maybe_reexec_current_process(role: str = "agent", profile: str | None = None) -> None:
    """Re-exec the current macOS Python process through a Hermes-named bundle.

    This is intentionally opt-in because re-execing interactive shells can be
    surprising.  Callers set ``HERMES_MACOS_IDENTITY_REEXEC=1`` when they want
    foreground CLI processes to get the same Activity Monitor identity as
    launchd-managed services.
    """
    if not is_macos() or os.environ.get("HERMES_MACOS_IDENTITY_REEXEC") != "1":
        return
    if os.environ.get("HERMES_MACOS_IDENTITY_ACTIVE") == "1":
        return
    from hermes_constants import get_hermes_home

    executable = executable_for_role(role=role, hermes_home=get_hermes_home(), python_path=sys.executable, profile=profile)
    if Path(executable).resolve() == Path(sys.executable).resolve() and Path(sys.executable).name.startswith("Hermes"):
        return
    os.environ["HERMES_MACOS_IDENTITY_ACTIVE"] = "1"
    os.execv(executable, [executable, *sys.argv])
