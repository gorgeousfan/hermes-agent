"""Regression tests for .env file permissions in install scripts.

The .env file holds API keys and secrets.  Install scripts that create it
must restrict permissions to owner-only (0600) to prevent other local users
from reading the secrets.

See: https://github.com/NousResearch/hermes-agent/issues/25477
"""

from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "scripts" / "install.sh"
SETUP_HERMES_SH = REPO_ROOT / "setup-hermes.sh"


def test_install_sh_sets_0600_on_new_env() -> None:
    """install.sh must chmod 0600 the .env file after creating it."""
    text = INSTALL_SH.read_text()

    # The chmod must appear inside the "if [ ! -f ... .env ]" block
    assert 'chmod 0600 "$HERMES_HOME/.env"' in text, (
        "install.sh should set 0600 on newly created .env"
    )


def test_setup_hermes_sets_0600_on_new_env() -> None:
    """setup-hermes.sh must chmod 0600 the .env file after creating it."""
    text = SETUP_HERMES_SH.read_text()

    assert "chmod 0600 .env" in text, (
        "setup-hermes.sh should set 0600 on newly created .env"
    )
