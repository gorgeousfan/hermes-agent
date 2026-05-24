"""contract test: docker entrypoint repairs runtime-writable install dirs

regression guard for #23402. remapping the hermes uid/gid at container start
must also fix the runtime-mutable install-tree paths that the dashboard/TUI may
write into, otherwise chat startup can fail with EACCES under custom
HERMES_UID/HERMES_GID deployments.
"""
from __future__ import annotations

from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
ENTRYPOINT = REPO_ROOT / "docker" / "entrypoint.sh"


def test_entrypoint_chowns_runtime_mutable_install_dirs_for_remapped_uid() -> None:
    text = ENTRYPOINT.read_text()

    assert "runtime_mutable_paths=(" in text
    assert 'if [ -n "$HERMES_UID" ] && [ "$HERMES_UID" != "10000" ]; then' in text

    for required_path in ('"$INSTALL_DIR/ui-tui"', '"$INSTALL_DIR/node_modules"'):
        assert required_path in text, (
            f"{required_path} must be included in the runtime install-tree chown "
            "list for custom HERMES_UID deployments; see #23402"
        )

    assert 'chown -R hermes:hermes "$runtime_path"' in text, (
        "entrypoint must recursively repair ownership on runtime-mutable "
        "install dirs before dropping privileges"
    )
