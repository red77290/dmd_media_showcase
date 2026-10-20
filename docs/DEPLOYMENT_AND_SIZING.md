# ⚙️ Deployment, CPU Sizing & Multi-DMD Scaling Guide

> **[🇫🇷 Lire ce guide en Français (DEPLOYMENT_AND_SIZING_FR.md)](DEPLOYMENT_AND_SIZING_FR.md)**

`dmd-media-showcase` is engineered from the ground up to run reliably on anything from a modest **Raspberry Pi 4 / NAS container** up to a multi-core **Proxmox / Unraid / Docker VM** serving a whole arcade room of DMDs.

---

## 1. How the Bounded Worker Pool Protects Your Server

Video transcoding, AI action-moment scoring, and LED colorimetric processing are CPU-intensive operations. Without strict admission control, 5 DMDs requesting 10 videos simultaneously would spawn 50 FFmpeg processes, causing 100% CPU saturation, thermal throttling, or OOM crashes.

`dmd-media-showcase` solves this through a **Bounded FIFO Worker Pool (`SessionBatchManager`)**:
1. **Strict Concurrency Cap (`MAX_CONCURRENT_JOBS`):** Only `1` to `N` worker processes run simultaneously (`ProcessPoolExecutor`).
2. **Single-Thread FFmpeg (`FFMPEG_THREADS_PER_JOB=1`):** Each worker restricts OpenCV and FFmpeg to a single thread so memory and CPU usage are 100% predictable.
3. **Lowered OS Process Priority (`WORKER_NICE_INCREMENT=10`):** Video conversion workers run at `nice +10`, ensuring the FastAPI HTTP/Streaming event loop always has immediate CPU priority (`< 2 ms` response time).
4. **Elastic Rotation Skipping on DMDs:**
   - **Fast Server (4–8 vCPU):** Batches finish well before the DMD rotation turn arrives $\rightarrow$ **0 skipped rotations**.
   - **Modest Server (1–2 vCPU) with many DMDs:** Jobs stay queued peacefully. When a DMD asks `GET /api/v1/{domain}/batch` (*"Are you ready?"*) and receives `status: "warming"`, the DMD simply **skips its rotation turn** and displays its next active engine (Clock, Weather, Scoreboard, GIF) while the server finishes the batch in the background.
   - **Self-Diagnosing Signal:** If you notice your DMDs skipping several showcase turns in a row, it is a clean visual signal that you should either reduce the number of active showcase engines/items per rotation or assign an extra vCPU to your VM.

---

## 2. Recommended Hardware & VM Sizing Table

| Host / VM Profile | vCPU / Cores | RAM | Recommended `MAX_CONCURRENT_JOBS` | Typical Capacity (Simultaneous DMDs) |
| :--- | :--- | :--- | :--- | :--- |
| **Raspberry Pi 4 / Synology NAS** | 2 vCPU | 512 MB – 1 GB | `1` | **1 to 2 DMDs** (3–5 items/rotation) |
| **Standard Home VM (Proxmox / Unraid)** | 2 to 4 vCPU | 1 GB – 2 GB | `2` | **3 to 6 DMDs** (5–10 items/rotation) |
| **Arcade Room Server / Core i5 / Ryzen** | 6 to 8 vCPU | 2 GB – 4 GB | `4` | **8 to 15+ DMDs** (zero rotation skips) |

---

## 3. Environment Variables Reference (`.env`)

| Variable | Default | Description |
| :--- | :--- | :--- |
| `PORT` | `8080` | HTTP listening port. |
| `DEFAULT_LANG` | `fr` | Default fallback ISO language (`fr`, `en`, `es`, `de`, `it`). |
| `MAX_CONCURRENT_JOBS` | `0` (Auto: `1..4`) | Maximum parallel video conversion workers (`1` for RPi/small NAS, `2..4` for VMs). |
| `FFMPEG_THREADS_PER_JOB` | `1` | Threads per FFmpeg/OpenCV worker process. Keep at `1` for predictable multi-DMD scaling. |
| `WORKER_NICE_INCREMENT` | `10` | POSIX `nice` increment applied to video worker processes (`0..19`). |
| `CACHE_DIR` | `/tmp/dmd_media_showcase_cache` | Ephemeral directory for `CurrentBatch` and `NextBatch` assets (mounted as `tmpfs` in Docker). |
| `BATCH_TTL_SECONDS` | `180` | Minimum lifetime of a served batch before rotating to `NextBatch` and purging old files. |
| `SESSION_TTL_SECONDS` | `1200` | Inactivity timeout (20 min) after which an idle DMD session and its files are purged. |
| `SERVER_MASTER_CLIP_MAX_S` | `12.0` | Master video clip ceiling cached on disk, allowing $O(1)$ dynamic truncation via `?max_duration_s=`. |
