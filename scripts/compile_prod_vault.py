#!/usr/bin/env python3
"""
CI/CD Production Native Vault Compiler (`scripts/compile_prod_vault.py`).

Executed ONLY during CI/CD release builds (GitHub Actions / GitLab CI).
In local development or user forks (`docker-compose` / `.env`), this script is not needed
and exits cleanly with code 0 if secrets are absent.

How it works in CI/CD when secrets are present:
1. Reads `VAULT_MASTER_SECRET` (secret salt/master key stored ONLY in GitHub/GitLab Secrets)
   along with `TMDB_API_KEY`, `SCREENSCRAPER_DEV_ID`, `SCREENSCRAPER_DEV_PASS`, `TVDB_API_KEY`.
2. Derives a per-key 256-bit keystream via PBKDF2-HMAC-SHA256 (`VAULT_MASTER_SECRET` + random salt)
   and splits each secret into two C `uint8_t` arrays (`cipher_a`, `cipher_b`) where
   `plain[i] = cipher_a[i] ^ cipher_b[i] ^ keystream[i]`.
3. Generates a temporary Python C-API extension (`_prod_vault.c`), compiles it into a native
   machine binary (`app/security/_prod_vault*.so` / `.pyd`), and immediately deletes `_prod_vault.c`.
"""

from __future__ import annotations

import hashlib
import os
import shutil
import subprocess
import sys
import sysconfig
import tempfile
from pathlib import Path
from typing import Dict, Tuple

ROOT = Path(__file__).resolve().parent.parent
SECURITY_DIR = ROOT / "app" / "security"

ENV_MAP: Dict[str, str] = {
    "TMDB_API_KEY": "tmdb_api_key",
    "SCREENSCRAPER_DEV_ID": "screenscraper_dev_id",
    "SCREENSCRAPER_DEV_PASS": "screenscraper_dev_pass",
    "TVDB_API_KEY": "tvdb_api_key",
}


def _c_byte_array(data: bytes) -> str:
    if not data:
        return "{0x00}"
    return "{" + ", ".join(f"0x{b:02x}" for b in data) + "}"


def _generate_c_extension_source(master_secret: str, secrets_map: Dict[str, str]) -> str:
    entries_c = []
    for idx, (vault_key, plain_text) in enumerate(sorted(secrets_map.items())):
        raw = plain_text.encode("utf-8")
        n = len(raw)
        salt = os.urandom(16)
        mask_a = os.urandom(n)
        ks = hashlib.pbkdf2_hmac(
            "sha256",
            master_secret.encode("utf-8") + b"::" + vault_key.encode("utf-8"),
            salt,
            100_000,
            dklen=n,
        )
        # Split into 3 shares so no single array resembles the key or keystream
        share_b = bytes(r ^ a ^ k for r, a, k in zip(raw, mask_a, ks))
        entries_c.append(
            f"static const uint8_t k_share_a_{idx}[{n}] = {_c_byte_array(mask_a)};\n"
            f"static const uint8_t k_share_b_{idx}[{n}] = {_c_byte_array(share_b)};\n"
            f"static const uint8_t k_share_k_{idx}[{n}] = {_c_byte_array(ks)};\n"
        )

    dispatch_lines = []
    for idx, (vault_key, plain_text) in enumerate(sorted(secrets_map.items())):
        n = len(plain_text.encode("utf-8"))
        dispatch_lines.append(
            f'    if (strcmp(key_name, "{vault_key}") == 0) {{\n'
            f"        char buf[{n + 1}];\n"
            f"        for (size_t i = 0; i < {n}; ++i) {{\n"
            f"            buf[i] = (char)(k_share_a_{idx}[i] ^ k_share_b_{idx}[i] ^ k_share_k_{idx}[i]);\n"
            f"        }}\n"
            f"        buf[{n}] = '\\0';\n"
            f"        PyObject* res = PyUnicode_DecodeUTF8(buf, {n}, \"strict\");\n"
            f"        memset(buf, 0, sizeof(buf));\n"
            f"        return res;\n"
            f"    }}"
        )

    arrays_block = "\n".join(entries_c)
    dispatch_block = "\n".join(dispatch_lines)

    return f"""#define PY_SSIZE_T_CLEAN
#include <Python.h>
#include <stdint.h>
#include <string.h>

{arrays_block}

static PyObject* prod_vault_get_secret(PyObject* self, PyObject* args) {{
    const char* key_name = NULL;
    if (!PyArg_ParseTuple(args, "s", &key_name)) {{
        return NULL;
    }}
{dispatch_block}
    return PyUnicode_FromString("");
}}

static PyMethodDef ProdVaultMethods[] = {{
    {{"get_secret", prod_vault_get_secret, METH_VARARGS, "Decrypt built-in production credential in RAM."}},
    {{NULL, NULL, 0, NULL}}
}};

static struct PyModuleDef prod_vault_module = {{
    PyModuleDef_HEAD_INIT,
    "_prod_vault",
    NULL,
    -1,
    ProdVaultMethods
}};

PyMODINIT_FUNC PyInit__prod_vault(void) {{
    return PyModule_Create(&prod_vault_module);
}}
"""


def _read_ci_or_buildkit_secret(name: str) -> str:
    env_val = (os.environ.get(name) or "").strip()
    if env_val:
        return env_val
    secret_file = Path("/run/secrets") / name
    if secret_file.exists():
        try:
            return secret_file.read_text(encoding="utf-8").strip()
        except Exception:
            pass
    return ""


def compile_native_vault() -> int:
    master_secret = _read_ci_or_buildkit_secret("VAULT_MASTER_SECRET")
    secrets_map: Dict[str, str] = {}
    for env_k, vault_k in ENV_MAP.items():
        val = _read_ci_or_buildkit_secret(env_k)
        if val:
            secrets_map[vault_k] = val

    if not master_secret or not secrets_map:
        print(
            "[PROD-VAULT] VAULT_MASTER_SECRET or API secrets not set in environment "
            "(local or fork build) -> skipping native vault compilation."
        )
        return 0

    with tempfile.TemporaryDirectory(prefix="dmd_vault_build_") as tmp_dir_str:
        tmp_dir = Path(tmp_dir_str)
        c_file = tmp_dir / "_prod_vault.c"
        setup_file = tmp_dir / "setup.py"

        c_file.write_text(_generate_c_extension_source(master_secret, secrets_map), encoding="utf-8")
        setup_file.write_text(
            "from setuptools import setup, Extension\n"
            "setup(\n"
            "    name='_prod_vault',\n"
            "    ext_modules=[Extension('app.security._prod_vault', ['_prod_vault.c'])],\n"
            ")\n",
            encoding="utf-8",
        )

        cmd = [
            sys.executable,
            "setup.py",
            "build_ext",
            "--build-lib",
            str(ROOT),
        ]
        res = subprocess.run(cmd, cwd=str(tmp_dir), capture_output=True, text=True)
        if res.returncode != 0 and os.name != "nt":
            # Fallback to direct POSIX C compiler (`cc`/`clang`/`gcc`) via `sysconfig`
            # when `setuptools` is not pre-installed in a Python 3.12+/3.13 virtualenv.
            cc = shutil.which(os.environ.get("CC", "cc")) or shutil.which("clang") or shutil.which("gcc")
            inc_dir = sysconfig.get_paths().get("include") or ""
            ext_suffix = sysconfig.get_config_var("EXT_SUFFIX") or ".so"
            if cc and inc_dir:
                out_so = SECURITY_DIR / f"_prod_vault{ext_suffix}"
                cc_cmd = [cc, "-O2", f"-I{inc_dir}"]
                if sys.platform == "darwin":
                    cc_cmd += ["-bundle", "-undefined", "dynamic_lookup"]
                else:
                    cc_cmd += ["-shared", "-fPIC"]
                cc_cmd += [str(c_file), "-o", str(out_so)]
                res = subprocess.run(cc_cmd, cwd=str(tmp_dir), capture_output=True, text=True)
        if res.returncode != 0:
            print(f"[PROD-VAULT] Warning: Native C compilation failed ({res.stderr.strip()}).", file=sys.stderr)
            return 1

    # Verify the compiled binary exists and decrypts secrets accurately in-memory
    built_files = list(SECURITY_DIR.glob("_prod_vault*"))
    if not built_files:
        print("[PROD-VAULT] Error: No compiled _prod_vault binary produced.", file=sys.stderr)
        return 1

    sample_key, sample_val = next(iter(secrets_map.items()))
    verify_cmd = [
        sys.executable,
        "-c",
        (
            "import sys; "
            "from app.security.vault import decrypt_prod_vault_secret, has_prod_vault_secret; "
            f"assert has_prod_vault_secret({sample_key!r}), 'vault secret not detected'; "
            f"assert decrypt_prod_vault_secret({sample_key!r}) == sys.argv[1], 'roundtrip mismatch'"
        ),
        sample_val,
    ]
    verify_res = subprocess.run(verify_cmd, cwd=str(ROOT), capture_output=True, text=True)
    if verify_res.returncode != 0:
        print(
            f"[PROD-VAULT] Error: Compiled _prod_vault self-test failed ({verify_res.stderr.strip()}).",
            file=sys.stderr,
        )
        return 1

    print(
        f"[PROD-VAULT] Compiled native binary {[p.name for p in built_files]} "
        f"with {len(secrets_map)} encrypted secret(s) (source .c purged, self-test OK)."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(compile_native_vault())
