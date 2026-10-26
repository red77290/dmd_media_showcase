"""
Static Web Administration Dashboard Asset Loader (`app/infrastructure/web/dashboard_loader.py`).

Resolves and loads the HTML5 shell (`index.html`), modular stylesheets (`css/*.css`),
and ES JavaScript modules (`js/**/*.js`) from `app/infrastructure/web/static/`,
supporting source checkouts, wheel installs, and PyInstaller portable bundles.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import List

from app.portable_env import get_resource_path

STATIC_WEB_DIR: Path = get_resource_path("app/infrastructure/web/static")


def load_admin_dashboard_html() -> str:
    """Load the HTML5 Control Center entry document (`index.html`)."""
    return (STATIC_WEB_DIR / "index.html").read_text(encoding="utf-8")


def iter_static_css_files() -> List[Path]:
    """Return CSS stylesheet paths in exact `index.html` `<link>` cascade order."""
    css_dir = STATIC_WEB_DIR / "css"
    all_css = {p.name: p for p in sorted(css_dir.rglob("*.css"))}
    ordered: List[Path] = []
    index_path = STATIC_WEB_DIR / "index.html"
    if index_path.exists():
        html = index_path.read_text(encoding="utf-8")
        for href in re.findall(r'<link[^>]+rel=["\']stylesheet["\'][^>]+href=["\']([^"\']+)["\']', html):
            clean_name = href.split("?", 1)[0].rsplit("/", 1)[-1]
            if clean_name in all_css and all_css[clean_name] not in ordered:
                ordered.append(all_css[clean_name])
    for path in all_css.values():
        if path not in ordered:
            ordered.append(path)
    return ordered


def iter_static_js_files() -> List[Path]:
    """Return sorted ES module JavaScript paths under `static/js/`."""
    return sorted((STATIC_WEB_DIR / "js").rglob("*.js"))


def load_combined_static_css() -> str:
    """Concatenate all static CSS stylesheets for inspection and test verification."""
    return "\n".join(path.read_text(encoding="utf-8") for path in iter_static_css_files())


def load_combined_static_js() -> str:
    """Concatenate all static ES modules for inspection and test verification."""
    return "\n".join(path.read_text(encoding="utf-8") for path in iter_static_js_files())
