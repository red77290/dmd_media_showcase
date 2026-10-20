"""
Server configuration loaded from environment variables or `.env`.
"""

from __future__ import annotations

import os
from pathlib import Path
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    host: str = "0.0.0.0"
    port: int = 8080
    log_level: str = "INFO"
    default_lang: str = "fr"

    # Worker pool & CPU protection
    max_concurrent_jobs: int = 0  # 0 = auto-bounded (1..4 based on CPU cores)
    ffmpeg_threads_per_job: int = 1
    worker_nice_increment: int = 10
    max_queue_size: int = 200
    use_process_pool: bool = True

    # Stateless cache & session TTLs
    cache_dir: Path = Path("/tmp/dmd_media_showcase_cache")
    batch_ttl_seconds: int = 180
    session_ttl_seconds: int = 1200
    server_master_clip_max_s: float = 12.0

    # Optional Multilingual Scraper Credentials
    tmdb_api_key: str = ""
    screenscraper_user: str = ""
    screenscraper_pass: str = ""
    screenscraper_dev_id: str = ""
    screenscraper_dev_pass: str = ""
    tvdb_api_key: str = ""

    @property
    def effective_max_workers(self) -> int:
        if self.max_concurrent_jobs > 0:
            return max(1, min(16, self.max_concurrent_jobs))
        cpu_cnt = os.cpu_count() or 2
        if cpu_cnt <= 2:
            return 1
        return max(1, min(4, cpu_cnt // 2))


settings = Settings()
