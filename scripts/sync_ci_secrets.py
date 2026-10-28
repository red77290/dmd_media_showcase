#!/usr/bin/env python3
"""
Local `.env` -> GitHub Actions / GitLab CI Secret Synchronizer (`scripts/sync_ci_secrets.py`).

Usage:
  python3 scripts/sync_ci_secrets.py

Features:
1. Reads your local `.env` file (which is ignored by git via `.gitignore`).
2. Automatically generates and persists a 256-bit `VAULT_MASTER_SECRET` in `.env` if missing.
3. Pushes/updates all non-empty developer secrets (`VAULT_MASTER_SECRET`, `TMDB_API_KEY`,
   `TVDB_API_KEY`, `SCREENSCRAPER_DEV_ID`, `SCREENSCRAPER_DEV_PASS`, `IGDB_CLIENT_ID`,
   `IGDB_CLIENT_SECRET`) to your repository's CI/CD secrets (GitHub Actions Secrets and/or
   GitLab CI Masked Variables).
4. Skips empty keys gracefully (e.g., `SCREENSCRAPER_DEV_ID` / `SCREENSCRAPER_DEV_PASS` until
   you receive them from ScreenScraper; simply re-run `python3 scripts/sync_ci_secrets.py`
   later to add or update them).
"""

from __future__ import annotations

import argparse
import re
import secrets
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Dict, Optional, Tuple

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_ENV_FILE = ROOT / ".env"

MANAGED_CI_SECRETS = (
    "VAULT_MASTER_SECRET",
    "TMDB_API_KEY",
    "TVDB_API_KEY",
    "SCREENSCRAPER_DEV_ID",
    "SCREENSCRAPER_DEV_PASS",
    "IGDB_CLIENT_ID",
    "IGDB_CLIENT_SECRET",
)


def _parse_env_file(env_path: Path) -> Dict[str, str]:
    out: Dict[str, str] = {}
    if not env_path.exists():
        return out
    for raw_line in env_path.read_text(encoding="utf-8").splitlines():
        line = raw_line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        k, v = line.split("=", 1)
        k = k.strip()
        v = v.strip().strip('"').strip("'")
        out[k] = v
    return out


def _ensure_vault_master_secret(env_path: Path, env_map: Dict[str, str]) -> str:
    existing = (env_map.get("VAULT_MASTER_SECRET") or "").strip()
    if existing:
        return existing

    generated = secrets.token_hex(32)
    env_map["VAULT_MASTER_SECRET"] = generated

    if env_path.exists():
        content = env_path.read_text(encoding="utf-8")
        if re.search(r"^\s*VAULT_MASTER_SECRET\s*=", content, flags=re.MULTILINE):
            content = re.sub(
                r"^\s*VAULT_MASTER_SECRET\s*=.*$",
                f"VAULT_MASTER_SECRET={generated}",
                content,
                flags=re.MULTILINE,
            )
        else:
            if not content.endswith("\n"):
                content += "\n"
            content += (
                "\n# 0. CI/CD Production Native Vault Master Salt (Auto-generated, never committed)\n"
                f"VAULT_MASTER_SECRET={generated}\n"
            )
        env_path.write_text(content, encoding="utf-8")
        print("[SECRET-SYNC] Generated new 256-bit VAULT_MASTER_SECRET and saved it to .env")
    return generated


def _detect_github_repo() -> Optional[str]:
    try:
        res = subprocess.run(
            ["git", "remote", "get-url", "origin"],
            cwd=str(ROOT),
            capture_output=True,
            text=True,
            check=False,
        )
        url = (res.stdout or "").strip()
        m = re.search(r"github\.com[:/]([^/]+/[^/.]+?)(?:\.git)?$", url)
        if m:
            return m.group(1)
    except Exception:
        pass
    return None


def _resolve_git_credential_token(host: str = "github.com") -> Optional[str]:
    """Query git's configured credential helper (e.g. osxkeychain) for a valid HTTPS token."""
    import os

    env = dict(os.environ)
    env["GIT_TERMINAL_PROMPT"] = "0"
    try:
        proc = subprocess.run(
            ["git", "credential", "fill"],
            input=f"protocol=https\nhost={host}\n\n",
            capture_output=True,
            text=True,
            timeout=3.0,
            env=env,
            check=False,
        )
        if proc.returncode == 0 and proc.stdout:
            for line in proc.stdout.splitlines():
                if line.startswith("password="):
                    pwd = line.split("=", 1)[1].strip()
                    if pwd:
                        return pwd
    except Exception:
        pass
    return None


def _push_github_secret(repo: str, name: str, value: str, token: Optional[str]) -> Tuple[bool, str]:
    gh_bin = shutil.which("gh")
    if not gh_bin:
        return False, "GitHub CLI ('gh') not found in PATH."

    import os

    env = dict(os.environ)
    if token:
        env["GH_TOKEN"] = token

    proc = subprocess.run(
        [gh_bin, "secret", "set", name, "--repo", repo],
        input=value,
        capture_output=True,
        text=True,
        env=env,
        check=False,
    )
    if proc.returncode == 0:
        return True, "OK"
    err = (proc.stderr or proc.stdout or "").strip()
    return False, err


def _push_gitlab_variable(project: str, name: str, value: str, gitlab_url: str, token: str) -> Tuple[bool, str]:
    import urllib.error
    import urllib.parse
    import urllib.request
    import json

    encoded_proj = urllib.parse.quote(project, safe="")
    base_api = f"{gitlab_url.rstrip('/')}/api/v4/projects/{encoded_proj}/variables"
    headers = {
        "PRIVATE-TOKEN": token,
        "Content-Type": "application/json",
    }
    payload = json.dumps(
        {
            "key": name,
            "value": value,
            "protected": False,
            "masked": len(value) >= 8,
            "raw": True,
        }
    ).encode("utf-8")

    # Try PUT (update existing) first, then POST (create)
    for method, url in (("PUT", f"{base_api}/{name}"), ("POST", base_api)):
        req = urllib.request.Request(url, data=payload, headers=headers, method=method)
        try:
            with urllib.request.urlopen(req, timeout=10.0) as resp:
                if 200 <= resp.status < 300:
                    return True, f"OK ({method})"
        except urllib.error.HTTPError as e:
            if method == "PUT" and e.code == 404:
                continue
            return False, f"HTTP {e.code}: {e.reason}"
        except Exception as exc:
            return False, str(exc)
    return False, "Failed to create or update GitLab variable"


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Synchronize local .env developer API keys to GitHub Actions Secrets / GitLab CI Variables."
    )
    parser.add_argument("--env-file", default=str(DEFAULT_ENV_FILE), help="Path to local .env file")
    parser.add_argument("--repo", default="", help="GitHub repository (owner/name), auto-detected from origin")
    parser.add_argument("--gitlab-project", default="", help="Optional GitLab project path or ID (e.g. group/project)")
    parser.add_argument("--gitlab-url", default="https://gitlab.com", help="GitLab instance URL")
    parser.add_argument("--dry-run", action="store_true", help="Show which keys would be synced without network calls")
    args = parser.parse_args()

    env_path = Path(args.env_file).resolve()
    if not env_path.exists():
        print(f"[SECRET-SYNC] Error: {env_path} does not exist.", file=sys.stderr)
        return 1

    env_map = _parse_env_file(env_path)
    _ensure_vault_master_secret(env_path, env_map)

    to_sync: Dict[str, str] = {}
    print(f"[SECRET-SYNC] Inspecting {env_path.name}:")
    for key in MANAGED_CI_SECRETS:
        val = (env_map.get(key) or "").strip()
        if not val:
            print(f"  - {key}: [SKIP] empty in .env (fill it in .env and re-run to push)")
        else:
            masked = val[:3] + "..." + val[-2:] if len(val) > 6 else "***"
            print(f"  - {key}: [READY] ({len(val)} chars, {masked})")
            to_sync[key] = val

    if args.dry_run:
        print(f"[SECRET-SYNC] Dry-run complete ({len(to_sync)} secret(s) ready).")
        return 0

    import os

    gh_repo = args.repo or _detect_github_repo()
    gh_token = os.environ.get("GH_TOKEN") or os.environ.get("GITHUB_TOKEN") or _resolve_git_credential_token("github.com")

    exit_code = 0
    if gh_repo:
        print(f"\n[SECRET-SYNC] Pushing {len(to_sync)} secret(s) to GitHub repository '{gh_repo}'...")
        for k, v in to_sync.items():
            ok, msg = _push_github_secret(gh_repo, k, v, gh_token)
            if ok:
                print(f"  ✓ GitHub Secret '{k}' updated successfully.")
            else:
                print(f"  ✗ GitHub Secret '{k}' failed: {msg}", file=sys.stderr)
                exit_code = 1

    gitlab_proj = args.gitlab_project or os.environ.get("GITLAB_PROJECT", "")
    gitlab_token = (
        (os.environ.get("GITLAB_TOKEN") or _resolve_git_credential_token("gitlab.com") or "")
        if gitlab_proj
        else ""
    )
    if gitlab_proj and gitlab_token:
        print(f"\n[SECRET-SYNC] Pushing {len(to_sync)} variable(s) to GitLab project '{gitlab_proj}'...")
        for k, v in to_sync.items():
            ok, msg = _push_gitlab_variable(gitlab_proj, k, v, args.gitlab_url, gitlab_token)
            if ok:
                print(f"  ✓ GitLab Variable '{k}' updated successfully.")
            else:
                print(f"  ✗ GitLab Variable '{k}' failed: {msg}", file=sys.stderr)
                exit_code = 1

    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
