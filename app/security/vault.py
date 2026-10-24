"""
Production CI/CD Native Vault Loader (`app/security/vault.py`).

Runtime Priority Order (in `ServerConfigStore`):
1. User WebUI overrides (`./data/server_config.json`)
2. Local `.env` / `docker-compose.yml` environment variables (plaintext, zero friction for local runs & forks)
3. Compiled native C extension (`app.security._prod_vault`), built exclusively during CI/CD releases
   using `VAULT_MASTER_SECRET` + API secrets from GitHub Actions Secrets / GitLab CI Variables.

In the open-source Git repository:
- Neither `VAULT_MASTER_SECRET` (the salt/master key) nor any API key is ever present.
- When `_prod_vault` is not compiled (e.g., local dev or a fork without CI secrets), this module
  gracefully returns `""` and the server uses `.env` / `docker-compose.yml` or keyless fallback scrapers.
"""

from __future__ import annotations

from typing import Tuple

VAULT_SECRET_KEYS: Tuple[str, ...] = (
    "tmdb_api_key",
    "screenscraper_dev_id",
    "screenscraper_dev_pass",
    "tvdb_api_key",
)


def _load_native_vault():
    try:
        from app.security import _prod_vault  # type: ignore[attr-defined]

        return _prod_vault
    except Exception:
        return None


def decrypt_prod_vault_secret(key_name: str) -> str:
    """
    Retrieve and decrypt `key_name` in RAM from the compiled native `_prod_vault` extension
    if present (CI/CD production release build). Returns `""` in local/fork environments.
    """
    mod = _load_native_vault()
    if mod is None:
        return ""
    try:
        getter = getattr(mod, "get_secret", None)
        if callable(getter):
            val = getter(key_name)
            return str(val or "").strip()
    except Exception:
        pass
    return ""


def has_prod_vault_secret(key_name: str) -> bool:
    """Return True if `key_name` is available in the compiled native `_prod_vault` extension."""
    return bool(decrypt_prod_vault_secret(key_name))
