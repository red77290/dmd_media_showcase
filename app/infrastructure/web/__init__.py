"""
Web Administration Static Assets & Loader (`app/infrastructure/web`).
"""

from app.infrastructure.web.dashboard_loader import (
    STATIC_WEB_DIR,
    iter_static_css_files,
    iter_static_js_files,
    load_admin_dashboard_html,
    load_combined_static_css,
    load_combined_static_js,
)

__all__ = [
    "STATIC_WEB_DIR",
    "iter_static_css_files",
    "iter_static_js_files",
    "load_admin_dashboard_html",
    "load_combined_static_css",
    "load_combined_static_js",
]
