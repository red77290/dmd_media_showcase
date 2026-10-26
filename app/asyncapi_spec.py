"""
AsyncAPI 2.6.0 Specification Loader (`app/asyncapi_spec.py`).

Loads the canonical MQTT & UDP `DMDF` specification directly from `docs/asyncapi.yaml`
rather than duplicating 600+ lines of YAML inside Python source code.
"""

from __future__ import annotations

from functools import lru_cache

from app.portable_env import get_resource_path


@lru_cache(maxsize=1)
def load_asyncapi_yaml() -> str:
    """Read and cache the canonical `docs/asyncapi.yaml` specification."""
    spec_path = get_resource_path("docs/asyncapi.yaml")
    return spec_path.read_text(encoding="utf-8")


ASYNCAPI_YAML: str = load_asyncapi_yaml()
ASYNCAPI_YAML_SPEC: str = ASYNCAPI_YAML
