"""
Domain Aggregate: Weighted Multi-Domain Playlist Cursor (`app/domain/aggregates/playlist_cursor.py`).

Pure deterministic rotation logic advancing `(slot_index, items_in_slot)` according to each
enabled playlist slot's `count` (`1..10` consecutive items per turn).
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Tuple


class WeightedPlaylistCursor:
    """
    Tracks `(slot_index, items_served_in_current_slot)` per DMD client ID and selects
    the next enabled playlist slot deterministically without external side effects.
    """

    def __init__(self) -> None:
        self.cursors: Dict[str, Tuple[int, int]] = {}

    def clear(self) -> None:
        self.cursors.clear()

    def reset_client(self, dmd_id: str) -> None:
        self.cursors.pop(dmd_id, None)

    def advance(
        self, dmd_id: str, playlist: List[Dict[str, Any]]
    ) -> Optional[Dict[str, Any]]:
        """
        Select the active enabled slot for `dmd_id` and advance the weighted cursor.
        Falls back to `playlist[0]` if all slots are marked disabled, or returns `None`
        only if `playlist` itself is empty.
        """
        enabled = [s for s in playlist if isinstance(s, dict) and s.get("enabled", True)]
        if not enabled:
            enabled = [s for s in playlist if isinstance(s, dict)][:1]
        if not enabled:
            return None

        slot_idx, in_slot = self.cursors.get(dmd_id, (0, 0))
        if slot_idx >= len(enabled):
            slot_idx, in_slot = 0, 0

        chosen = enabled[slot_idx]
        quota = max(1, int(chosen.get("count") or 1))
        next_in_slot = in_slot + 1
        if next_in_slot >= quota:
            self.cursors[dmd_id] = ((slot_idx + 1) % len(enabled), 0)
        else:
            self.cursors[dmd_id] = (slot_idx, next_in_slot)
        return chosen
