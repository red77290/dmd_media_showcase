"""
Pure Domain Layer for DMD Media Showcase (Hexagonal / Domain-Driven Architecture).
"""

from app.domain.aggregates import (
    BoundedTtlCache,
    DualSlotPipeline,
    PriorityPlaybackQueue,
    QueuedOnDemandEntry,
    WeightedPlaylistCursor,
)
from app.domain.board_identity import (
    DmdBoardIdentity,
    canonical_dmd_board_key,
    is_usable_physical_lan_ip,
    select_best_udp_ip,
    strip_mqtt_gen_suffix,
)
from app.domain.entities import (
    DomainType,
    FilterCriteria,
    MediaCandidate,
    PanelGeometry,
    PreparedShowcasePackage,
    ShowcaseCard,
)

__all__ = [
    "BoundedTtlCache",
    "DmdBoardIdentity",
    "DomainType",
    "DualSlotPipeline",
    "FilterCriteria",
    "MediaCandidate",
    "PanelGeometry",
    "PreparedShowcasePackage",
    "PriorityPlaybackQueue",
    "QueuedOnDemandEntry",
    "ShowcaseCard",
    "WeightedPlaylistCursor",
    "canonical_dmd_board_key",
    "is_usable_physical_lan_ip",
    "select_best_udp_ip",
    "strip_mqtt_gen_suffix",
]
