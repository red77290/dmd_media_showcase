"""
Static Web Administration Dashboard Asset Loader (`app/infrastructure/web/dashboard_loader.py`).

Resolves and loads the HTML5 shell (`index.html`), modular stylesheets (`css/*.css`),
and ES JavaScript modules (`js/**/*.js`) from `app/infrastructure/web/static/`,
supporting source checkouts, wheel installs, and PyInstaller portable bundles.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path
from typing import List

from app.portable_env import get_resource_path

STATIC_WEB_DIR: Path = get_resource_path("app/infrastructure/web/static")


@lru_cache(maxsize=1)
def load_admin_dashboard_html() -> str:
    """Load the HTML5 Control Center entry document (`index.html`)."""
    return (STATIC_WEB_DIR / "index.html").read_text(encoding="utf-8")


def iter_static_css_files() -> List[Path]:
    """Return sorted CSS stylesheet paths under `static/css/`."""
    return sorted((STATIC_WEB_DIR / "css").rglob("*.css"))


def iter_static_js_files() -> List[Path]:
    """Return sorted ES module JavaScript paths under `static/js/`."""
    return sorted((STATIC_WEB_DIR / "js").rglob("*.js"))


def load_combined_static_css() -> str:
    """Concatenate all static CSS stylesheets for inspection and test verification."""
    return "\n".join(path.read_text(encoding="utf-8") for path in iter_static_css_files())


def load_combined_static_js() -> str:
    """Concatenate all static ES modules for inspection and test verification."""
    return "\n".join(path.read_text(encoding="utf-8") for path in iter_static_js_files())
