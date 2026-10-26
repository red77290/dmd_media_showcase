"""
Generic Double-Buffered Slot Pipeline Aggregate (`app/domain/aggregates/slot_pipeline.py`).

Manages the `Ready (N)` + `Standby (N+1)` + `Active (in-playback)` lifecycle for any keyed
consumer pipeline without coupling to media files, MQTT, or HTTP transport.
"""

from __future__ import annotations

from typing import Callable, Dict, Generic, List, Optional, Tuple, TypeVar

TKey = TypeVar("TKey")
TItem = TypeVar("TItem")


class DualSlotPipeline(Generic[TKey, TItem]):
    """
    Generic `Ready (N)` + `Standby (N+1)` double-buffering state machine:
    - `ready[slot_key]`: item immediately claimable for playback.
    - `standby[slot_key]`: pre-warmed `N+1` item promoted to `ready` as soon as `N` is claimed.
    - `active[item_id]`: claimed items currently playing on a consumer.
    """

    def __init__(self, item_id_getter: Callable[[TItem], str]) -> None:
        self._get_id = item_id_getter
        self.ready: Dict[TKey, TItem] = {}
        self.standby: Dict[TKey, TItem] = {}
        self.active: Dict[str, TItem] = {}

    def has_ready(self, key: TKey) -> bool:
        return key in self.ready

    def has_standby(self, key: TKey) -> bool:
        return key in self.standby

    def is_full(self, key: TKey) -> bool:
        return key in self.ready and key in self.standby

    def store_prepared(self, key: TKey, item: TItem) -> str:
        """
        Place `item` in `ready` if empty, otherwise in `standby`.
        Returns `'ready'` or `'standby'`.
        """
        if key not in self.ready:
            self.ready[key] = item
            return "ready"
        self.standby[key] = item
        return "standby"

    def promote_priority_ready(self, key: TKey, priority_item: TItem) -> Optional[TItem]:
        """
        Insert `priority_item` into `ready[key]`, demoting any existing `ready[key]` into
        `standby[key]` so normal pre-warmed work is preserved if `standby` was empty.
        Returns any displaced `standby` item that must be cleaned up by the caller.
        """
        displaced: Optional[TItem] = None
        prev_ready = self.ready.pop(key, None)
        if prev_ready is not None:
            if key not in self.standby:
                self.standby[key] = prev_ready
            else:
                displaced = prev_ready
        self.ready[key] = priority_item
        return displaced

    def claim_ready(self, key: TKey) -> Tuple[Optional[TItem], Optional[TItem]]:
        """
        Atomically claim `ready[key]` into `active[item_id]` and promote `standby[key]`
        into `ready[key]` if available.
        Returns `(claimed_item, promoted_standby_item)`.
        """
        claimed = self.ready.pop(key, None)
        if claimed is None:
            return None, None
        self.active[self._get_id(claimed)] = claimed
        promoted = self.standby.pop(key, None)
        if promoted is not None:
            self.ready[key] = promoted
        return claimed, promoted

    def invalidate_slot(self, key: TKey) -> List[TItem]:
        """Remove and return both `ready` and `standby` items for `key`."""
        removed: List[TItem] = []
        r_item = self.ready.pop(key, None)
        if r_item is not None:
            removed.append(r_item)
        s_item = self.standby.pop(key, None)
        if s_item is not None:
            removed.append(s_item)
        return removed

    def pop_active(self, item_id: str) -> Optional[TItem]:
        """Remove and return an active item by `item_id`."""
        return self.active.pop(item_id, None)
