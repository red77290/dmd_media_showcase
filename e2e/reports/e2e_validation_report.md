# Local Containerized E2E Validation Report (`FrontendSync` + `dmd_media_showcase`)

- **Generated (UTC)**: `2026-11-02T20:14:34.259498+00:00`
- **Runtime Network**: `dmd_e2e_net` (`driver: bridge`, `internal: true` — 100% offline/air-gapped)
- **Summary**: **8 / 8 PASSED** (0 failed) in `137.93s`

## Scenario Matrix

| ID | DMD Target | Gaming OS | Game (`sys/rom`) | Marquee Verified | Showcase Stream Verified | Stop & Cleanup | Duration | Status |
|---|---|---|---|---|---|---|---|---|
| `E2E-01` | `ArcadeMatrix (ESP32)` | `recalbox` | `neogeo/mslug` | `True` | `True` | `True` | `36.032s` | **PASSED** |
| `E2E-02` | `ArcadeMatrix (ESP32)` | `batocera` | `snes/smw` | `True` | `True` | `True` | `31.152s` | **PASSED** |
| `E2E-03` | `ArcadeMatrix (ESP32)` | `retropie` | `megadrive/sonic` | `True` | `True` | `True` | `34.951s` | **PASSED** |
| `E2E-04` | `ArcadeMatrix (ESP32)` | `batocera (local broker fallback)` | `neogeo/mslug` | `True` | `N/A (local broker fallback mode)` | `True` | `0.551s` | **PASSED** |
| `E2E-05` | `ArcadeMatrix_RPi (Rust)` | `recalbox` | `neogeo/mslug` | `True` | `True` | `True` | `12.3s` | **PASSED** |
| `E2E-06` | `ArcadeMatrix_RPi (Rust)` | `batocera` | `snes/smw` | `True` | `True` | `True` | `8.58s` | **PASSED** |
| `E2E-07` | `ArcadeMatrix_RPi (Rust)` | `retropie` | `megadrive/sonic` | `True` | `True` | `True` | `13.39s` | **PASSED** |
| `E2E-08` | `ArcadeMatrix_RPi (Rust)` | `batocera (rapid preemption mslug -> smw)` | `neogeo/mslug -> snes/smw` | `True` | `True` | `True` | `0.781s` | **PASSED** |

## Per-Scenario Telemetry Details

### `E2E-01` — `ArcadeMatrix (ESP32)` × `recalbox` (PASSED)
- **game**: `neogeo/mslug`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `dmd-showcase:1883 (showcase)`
- **sync_client_id**: `esp32_ut0001_games_128x32_sync`
- **browse_marquee_rgb**: `{'tl': (255, 0, 0), 'tr': (0, 255, 0), 'bot': (0, 80, 255)}`
- **marquee_latency_ms**: `3.3`
- **marquee_rgb_samples**: `{'tl': (255, 0, 0), 'tr': (0, 255, 0), 'bot': (0, 80, 255)}`
- **showcase_stream_codec**: `dmdz2`
- **presented_stream_frames**: `405`
- **distinct_stream_crcs**: `291`
- **duration_s**: `36.032`

### `E2E-02` — `ArcadeMatrix (ESP32)` × `batocera` (PASSED)
- **game**: `snes/smw`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `dmd-showcase:1883 (showcase)`
- **sync_client_id**: `esp32_ut0001_games_128x32_sync`
- **marquee_latency_ms**: `34.8`
- **marquee_rgb_samples**: `{'tl': (255, 255, 0), 'tr': (0, 255, 255), 'bot': (255, 0, 255)}`
- **showcase_stream_codec**: `dmdz2`
- **presented_stream_frames**: `407`
- **distinct_stream_crcs**: `278`
- **duration_s**: `31.152`

### `E2E-03` — `ArcadeMatrix (ESP32)` × `retropie` (PASSED)
- **game**: `megadrive/sonic`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `dmd-showcase:1883 (showcase)`
- **sync_client_id**: `esp32_ut0001_games_128x32_sync`
- **marquee_latency_ms**: `417.8`
- **marquee_rgb_samples**: `{'tl': (0, 121, 255), 'tr': (255, 255, 255), 'bot': (0, 222, 82)}`
- **showcase_stream_codec**: `dmdz2`
- **presented_stream_frames**: `400`
- **distinct_stream_crcs**: `286`
- **duration_s**: `34.951`

### `E2E-04` — `ArcadeMatrix (ESP32)` × `batocera (local broker fallback)` (PASSED)
- **game**: `neogeo/mslug`
- **marquee_verified**: `True`
- **showcase_verified**: `N/A (local broker fallback mode)`
- **cleanup_verified**: `True`
- **broker_target**: `172.18.0.7:1883 (local)`
- **marquee_rgb_samples**: `{'tl': (255, 0, 0), 'tr': (0, 255, 0), 'bot': (0, 80, 255)}`
- **duration_s**: `0.551`

### `E2E-05` — `ArcadeMatrix_RPi (Rust)` × `recalbox` (PASSED)
- **game**: `neogeo/mslug`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `172.18.0.5:1883 (showcase)`
- **sync_client_id**: `rpi_24bcfb_games_128x32_sync`
- **browse_marquee_rgb**: `{'tl': (255, 0, 0), 'tr': (0, 255, 0), 'bot': (0, 80, 255)}`
- **marquee_latency_ms**: `3.4`
- **marquee_rgb_samples**: `{'tl': (255, 0, 0), 'tr': (0, 255, 0), 'bot': (0, 80, 255)}`
- **showcase_stream_codec**: `dmdz2`
- **distinct_crcs**: `68`
- **duration_s**: `12.3`

### `E2E-06` — `ArcadeMatrix_RPi (Rust)` × `batocera` (PASSED)
- **game**: `snes/smw`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `172.18.0.5:1883 (showcase)`
- **sync_client_id**: `rpi_24bcfb_games_128x32_sync`
- **marquee_latency_ms**: `66.9`
- **marquee_rgb_samples**: `{'tl': (255, 255, 0), 'tr': (0, 255, 255), 'bot': (255, 0, 255)}`
- **showcase_stream_codec**: `dmdz2`
- **distinct_crcs**: `37`
- **duration_s**: `8.58`

### `E2E-07` — `ArcadeMatrix_RPi (Rust)` × `retropie` (PASSED)
- **game**: `megadrive/sonic`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **broker_target**: `172.18.0.5:1883 (showcase)`
- **sync_client_id**: `rpi_24bcfb_games_128x32_sync`
- **marquee_latency_ms**: `414.5`
- **marquee_rgb_samples**: `{'tl': (0, 121, 255), 'tr': (255, 255, 255), 'bot': (0, 222, 82)}`
- **showcase_stream_codec**: `dmdz2`
- **distinct_crcs**: `84`
- **duration_s**: `13.39`

### `E2E-08` — `ArcadeMatrix_RPi (Rust)` × `batocera (rapid preemption mslug -> smw)` (PASSED)
- **game**: `neogeo/mslug -> snes/smw`
- **marquee_verified**: `True`
- **showcase_verified**: `True`
- **cleanup_verified**: `True`
- **marquee_rgb_samples**: `{'tl': (255, 255, 0), 'tr': (0, 255, 255), 'bot': (255, 0, 255)}`
- **duration_s**: `0.781`
