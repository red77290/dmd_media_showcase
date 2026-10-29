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
    port: int = 8890
    udp_stream_port: int = 5569
    mdns_enabled: bool = True
    mdns_hostname: str = "dmdmediashowcase"
    log_level: str = "INFO"
    default_lang: str = "fr"

    # Worker pool & CPU protection
    max_concurrent_jobs: int = 0  # 0 = auto-bounded (1..4 based on CPU cores)
    ffmpeg_threads_per_job: int = 1
    worker_nice_increment: int = 10
    max_queue_size: int = 200
    use_process_pool: bool = True

    # Stateless cache, media defaults & session TTLs
    cache_dir: Path = Path("/tmp/dmd_media_showcase_cache")
    on_demand_cache_enabled: bool = False
    on_demand_cache_max_mb: int = 0
    batch_ttl_seconds: int = 180
    session_ttl_seconds: int = 1200
    server_master_clip_max_s: float = 12.0
    video_max_duration_limit_s: float = 30.0
    video_max_fps_limit: int = 30
    panorama_duration_s: float = 4.0
    ytdlp_max_filesize_mb: int = 60
    default_video_mode: str = "auto_action"
    default_color_boost: str = "auto"

    # Optional Multilingual Scraper Credentials
    tmdb_api_key: str = ""
    screenscraper_user: str = ""
    screenscraper_pass: str = ""
    screenscraper_dev_id: str = ""
    screenscraper_dev_pass: str = ""
    tvdb_api_key: str = ""
    igdb_client_id: str = ""
    igdb_client_secret: str = ""

    # Home-Lab HTTP (default open) & Future Scalable Online HTTPS / API Key Security
    showcase_api_key: str = ""
    ssl_certfile: str = ""
    ssl_keyfile: str = ""
    trust_proxy_headers: bool = True

    @property
    def detected_cpu_cores(self) -> int:
        return max(1, int(os.cpu_count() or 2))

    @property
    def effective_max_workers(self) -> int:
        if self.max_concurrent_jobs > 0:
            return max(1, min(16, self.max_concurrent_jobs))
        cpu_cnt = self.detected_cpu_cores
        if cpu_cnt <= 2:
            return 1
        return max(1, min(4, cpu_cnt // 2))


settings = Settings()
