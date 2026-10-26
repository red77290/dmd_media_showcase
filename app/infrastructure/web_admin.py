"""
Web Administration Dashboard Entry Facade (`app/infrastructure/web_admin.py`).

Exposes `ADMIN_DASHBOARD_HTML` and static asset helpers backed by externalized
HTML5, CSS, and ES module files in `app/infrastructure/web/static/`.
"""

from __future__ import annotations

from app.infrastructure.web.dashboard_loader import (
    STATIC_WEB_DIR,
    iter_static_css_files,
    iter_static_js_files,
    load_admin_dashboard_html,
    load_combined_static_css,
    load_combined_static_js,
)

ADMIN_DASHBOARD_HTML: str = load_admin_dashboard_html()

__all__ = [
    "ADMIN_DASHBOARD_HTML",
    "STATIC_WEB_DIR",
    "iter_static_css_files",
    "iter_static_js_files",
    "load_admin_dashboard_html",
    "load_combined_static_css",
    "load_combined_static_js",
]
