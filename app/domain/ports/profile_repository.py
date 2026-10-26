"""
Domain Port: `IDmdProfileRepository` for persistent per-DMD profile and playlist storage.
"""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, Dict, List, Optional


class IDmdProfileRepository(ABC):
    """Repository port managing persisted DMD profiles (`config_source`, geometry, playlists)."""

    @abstractmethod
    def list_profiles(self) -> List[Dict[str, Any]]:
        """Return all persisted DMD profiles ordered by `dmd_id`."""

    @abstractmethod
    def get_profile(self, dmd_id: str) -> Optional[Dict[str, Any]]:
        """Return a deep copy of `dmd_id`'s profile, or `None` if absent."""

    @abstractmethod
    def upsert_profile(self, dmd_id: str, data: Dict[str, Any]) -> Dict[str, Any]:
        """Create or update `dmd_id`'s profile atomically."""

    @abstractmethod
    def ensure_from_hello(
        self, dmd_id: str, payload: Dict[str, Any], detected_ip: str = ""
    ) -> Dict[str, Any]:
        """Create or refresh hardware coordinates from a `hello` announcement without losing playlists."""

    @abstractmethod
    def delete_profile(self, dmd_id: str) -> bool:
        """Delete `dmd_id`'s persisted profile, returning `True` if it existed."""
