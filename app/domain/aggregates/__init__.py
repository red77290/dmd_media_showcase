"""
Reusable Domain Aggregates for DMD Media Showcase (`app/domain/aggregates`).
"""

from app.domain.aggregates.artifact_cache import BoundedTtlCache
from app.domain.aggregates.batch_job import BatchArtifactDescriptor, BatchScrapeJob
from app.domain.aggregates.playlist_cursor import WeightedPlaylistCursor
from app.domain.aggregates.priority_queue import PriorityPlaybackQueue, QueuedOnDemandEntry
from app.domain.aggregates.slot_pipeline import DualSlotPipeline

__all__ = [
    "BatchArtifactDescriptor",
    "BatchScrapeJob",
    "BoundedTtlCache",
    "DualSlotPipeline",
    "PriorityPlaybackQueue",
    "QueuedOnDemandEntry",
    "WeightedPlaylistCursor",
]
