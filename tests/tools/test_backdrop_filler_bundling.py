"""contract test: Backdrop's filler image is bundled by Vite, not served from /ds-assets/.

Regression guard for #24355.  The default dashboard backdrop renders a
faint filler image at the top-left of the viewport.  It used to load
from `/ds-assets/filler-bg0.<ext>`, which Vite only emits when
`npm run sync-assets` has populated `web/public/ds-assets/` first.
That prebuild hook can be silently bypassed — `vite build` invoked
directly, `prebuild` failing partway through, or `_build_web_ui`'s
stale-dist fallback (#23817) serving a `web_dist` that never had
`ds-assets/` written.  In any of those cases the URL 404s and the
default backdrop disappears.

Importing the asset through the package's `./assets/*` export forces
Vite to bundle it into the JS chunk graph, so the file always lands in
`web_dist/assets/` with a content-hashed name regardless of whether
`sync-assets` ran.
"""
from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[2]
BACKDROP = REPO_ROOT / "web" / "src" / "components" / "Backdrop.tsx"


def test_backdrop_does_not_reference_public_ds_assets_path() -> None:
    """`/ds-assets/filler-bg0.<ext>` requires the prebuild hook to have run.

    Any source reference to that literal path re-introduces the #24355
    regression — the backdrop silently disappears whenever `sync-assets`
    is bypassed.
    """
    source = BACKDROP.read_text(encoding="utf-8")
    assert "/ds-assets/filler-bg0" not in source, (
        "Backdrop.tsx must not reference `/ds-assets/filler-bg0.<ext>` directly. "
        "Use an ES import (`@nous-research/ui/assets/filler-bg0.webp`) so Vite "
        "bundles the asset and the default backdrop survives flows that skip "
        "the `sync-assets` prebuild hook (see #24355)."
    )


def test_backdrop_imports_filler_via_design_system_package() -> None:
    """The filler image must travel with the JS bundle.

    Vite resolves `@nous-research/ui/assets/filler-bg0.<ext>` through the
    package's `./assets/*` export and emits a content-hashed copy into
    `web_dist/assets/`, removing the dependency on
    `web/public/ds-assets/` being populated before build.

    The asserted pattern tolerates either quote style and either
    extension (`.webp` or `.jpg`) so a reformat or an asset-extension
    bump in `@nous-research/ui` doesn't false-positive this contract.
    """
    source = BACKDROP.read_text(encoding="utf-8")
    pattern = re.compile(
        r"""from\s+['"]@nous-research/ui/assets/filler-bg0\.(?:webp|jpg)['"]"""
    )
    assert pattern.search(source), (
        "Backdrop.tsx must import the filler image from "
        "`@nous-research/ui/assets/filler-bg0.webp` (or `.jpg`) so Vite "
        "bundles it. See #24355."
    )
