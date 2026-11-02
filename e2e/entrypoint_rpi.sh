#!/usr/bin/env bash
set -euo pipefail

cd /opt/arcadematrix_rpi
mkdir -p data/marquees data/gifs
cp -a /e2e_sd/rpi_marquees/. data/marquees/

# Start local mosquitto on :1883 so fallback broker scenarios work seamlessly
cat > /tmp/mosquitto_rpi.conf << 'EOF'
listener 1883 0.0.0.0
allow_anonymous true
EOF
mosquitto -c /tmp/mosquitto_rpi.conf -d || true

cat > /opt/arcadematrix_rpi/config.json << 'EOF'
{
  "matrix": {
    "width": 128,
    "height": 32,
    "chain_length": 1,
    "parallel": 1,
    "power_limit_percent": 100,
    "transition_effect": "none",
    "transition_duration_ms": 0,
    "slot_transition": "none",
    "slot_transition_duration_ms": 0
  },
  "system": {
    "night_mode_enabled": false,
    "day_brightness": 100
  },
  "mqtt": {
    "enabled": true,
    "broker": "127.0.0.1",
    "port": 1883,
    "device_name": "arcadematrix-rpi-e2e",
    "use_showcase_broker": true,
    "ingame_showcase_enabled": true,
    "ingame_cycle_mode": "once",
    "ingame_marquee_interval_sec": 30,
    "card_style_source": "custom",
    "include_card_intro": true,
    "card_layout": "classic_split",
    "card_theme": "tinted",
    "include_poster": true,
    "include_synopsis": false,
    "text_scroll_speed": "fast",
    "jacket_panorama": false,
    "marquee_panorama": true,
    "snapshot_panorama_count": 0,
    "include_video": true,
    "video_max_duration_s": 3,
    "video_fps": 12,
    "video_mode": "panorama",
    "color_boost": "auto"
  },
  "showcase": {
    "enabled": true,
    "server_url": "http://dmd-showcase:8890",
    "transport_mode": "http_stream",
    "use_showcase_broker": true,
    "mqtt_broker_source": "global"
  },
  "api_auth_enabled": false,
  "instances": [
    {
      "instance_id": "default_clock",
      "engine_id": "clock",
      "config": {
        "theme": "0",
        "format": "%H:%M:%S"
      }
    }
  ],
  "rotation": [
    {
      "instance_id": "default_clock",
      "duration_sec": 60
    }
  ]
}
EOF

exec /opt/arcadematrix_rpi/arcadematrix
