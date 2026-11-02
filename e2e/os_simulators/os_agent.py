#!/usr/bin/env python3
"""
Containerized Gaming OS Control Agent (`Recalbox`, `Batocera`, `RetroPie`).

Executes the REAL, UNMODIFIED repository installer & hook scripts inside each OS container:
- `ArcadeMatrix/tools/rpi_emulationstation_base_os_setup.sh`
- `ArcadeMatrix_RPi/tools/rpi_emulationstation_base_os_setup.sh`

Exposes a local HTTP control API on `:9000` for `e2e-runner`:
- `GET /health`
- `POST /install` (`{"dmd_type": "arcadematrix_esp32"|"arcadematrix_rpi", "dmd_host": "..."}`)
- `POST /event` (`{"action": "browse"|"start"|"stop", "system": "neogeo", "rom": "mslug", "title": "Metal Slug"}`)
"""

from __future__ import annotations

import json
import os
import socket
import subprocess
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any, Dict

OS_FLAVOR = os.environ.get("OS_FLAVOR", "recalbox").strip().lower()
CONTROL_PORT = int(os.environ.get("OS_AGENT_PORT", "9000"))
REPOS_ROOT = Path(os.environ.get("REPOS_ROOT", "/repos"))

_active_dmd_type: str = ""
_active_dmd_host: str = ""


def _ensure_os_dirs() -> None:
    if OS_FLAVOR == "recalbox":
        Path("/recalbox/share/userscripts").mkdir(parents=True, exist_ok=True)
        Path("/recalbox/share/roms/neogeo").mkdir(parents=True, exist_ok=True)
        Path("/recalbox/share/roms/snes").mkdir(parents=True, exist_ok=True)
        Path("/recalbox/share/roms/megadrive").mkdir(parents=True, exist_ok=True)
    elif OS_FLAVOR == "batocera":
        Path("/userdata/system/scripts").mkdir(parents=True, exist_ok=True)
        Path("/userdata/system/configs/emulationstation/scripts").mkdir(parents=True, exist_ok=True)
        Path("/userdata/userscripts").mkdir(parents=True, exist_ok=True)
        Path("/userdata/roms/neogeo").mkdir(parents=True, exist_ok=True)
        Path("/userdata/roms/snes").mkdir(parents=True, exist_ok=True)
        Path("/userdata/roms/megadrive").mkdir(parents=True, exist_ok=True)
    elif OS_FLAVOR == "retropie":
        Path("/opt/retropie/configs/all").mkdir(parents=True, exist_ok=True)
        Path("/home/pi/RetroPie/roms/neogeo").mkdir(parents=True, exist_ok=True)
        Path("/home/pi/RetroPie/roms/snes").mkdir(parents=True, exist_ok=True)
        Path("/home/pi/RetroPie/roms/megadrive").mkdir(parents=True, exist_ok=True)


def _touch_dummy_rom(rom_dir: Path, rom: str) -> Path:
    rom_dir.mkdir(parents=True, exist_ok=True)
    p = rom_dir / f"{rom}.zip"
    if not p.exists():
        p.write_bytes(b"PK\x03\x04dummy")
    return p


def _kill_background_daemons() -> None:
    for pattern in ("arcadematrix_daemon.py", "mosquitto_pub"):
        subprocess.run(["pkill", "-9", "-f", pattern], check=False)

    for cache_f in ("/tmp/arcadematrix_broker_target.cache", "/tmp/es_state.inf"):
        try:
            os.unlink(cache_f)
        except FileNotFoundError:
            pass
    time.sleep(0.25)


def _wait_daemon_lock_port(port: int = 49132, timeout_s: float = 5.0) -> bool:
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        try:
            s.bind(("127.0.0.1", port))
            s.close()
        except OSError:
            s.close()
            return True
        time.sleep(0.1)
    return False


def install_for_target(dmd_type: str, dmd_host: str) -> Dict[str, Any]:
    global _active_dmd_type, _active_dmd_host
    _ensure_os_dirs()
    _kill_background_daemons()

    _active_dmd_type = dmd_type
    _active_dmd_host = dmd_host

    if dmd_type in ("arcadematrix_esp32", "arcadematrix_rpi"):
        setup_script = (
            REPOS_ROOT / "ArcadeMatrix" / "tools" / "rpi_emulationstation_base_os_setup.sh"
            if dmd_type == "arcadematrix_esp32"
            else REPOS_ROOT / "ArcadeMatrix_RPi" / "tools" / "rpi_emulationstation_base_os_setup.sh"
        )
        choice = {"recalbox": "2", "batocera": "3", "retropie": "4"}[OS_FLAVOR]
        # Clean previous RetroPie hooks so repeated installs stay idempotent
        if OS_FLAVOR == "retropie":
            for fn in ("runcommand-onstart.sh", "runcommand-onend.sh"):
                p = Path("/opt/retropie/configs/all") / fn
                if p.exists():
                    p.unlink()

        proc = subprocess.run(
            ["bash", str(setup_script)],
            input=f"{dmd_host}\n{choice}\n",
            text=True,
            capture_output=True,
            timeout=20,
            check=False,
        )
        if proc.returncode != 0:
            return {"ok": False, "stderr": proc.stderr, "stdout": proc.stdout}

        if OS_FLAVOR == "recalbox":
            # Launch the installed Recalbox permanent launcher script
            subprocess.Popen(
                ["sh", "/recalbox/share/userscripts/arcadematrix_launcher(permanent).sh"],
                stdout=open("/tmp/am_recalbox_daemon.log", "w"),
                stderr=subprocess.STDOUT,
            )
            _wait_daemon_lock_port(49132, 4.0)
            # `arcadematrix_daemon.py` sleeps 3s on startup before entering its poll loop
            time.sleep(3.2)
        elif OS_FLAVOR == "retropie":
            _wait_daemon_lock_port(49132, 4.0)
            time.sleep(3.2)

        return {"ok": True, "os": OS_FLAVOR, "installer": str(setup_script)}

    return {"ok": False, "error": f"Unsupported dmd_type={dmd_type}"}


def trigger_os_event(action: str, system: str, rom: str, title: str) -> Dict[str, Any]:
    if _active_dmd_type in ("arcadematrix_esp32", "arcadematrix_rpi"):
        if OS_FLAVOR == "recalbox":
            rom_file = _touch_dummy_rom(Path(f"/recalbox/share/roms/{system}"), rom) if rom else Path("")
            if action == "stop":
                es_txt = "State=stopped\nSystemId=\nGamePath=\n"
            elif action == "browse":
                es_txt = f"State=browsing\nSystemId={system}\nGamePath={rom_file}\nGame={title or rom}\n"
            else:
                es_txt = f"State=playing\nSystemId={system}\nGamePath={rom_file}\nGame={title or rom}\n"
            Path("/tmp/es_state.inf").write_text(es_txt, encoding="utf-8")
            return {"ok": True}

        if OS_FLAVOR == "batocera":
            rom_file = _touch_dummy_rom(Path(f"/userdata/roms/{system}"), rom)
            if action == "browse":
                subprocess.run(
                    [
                        "/userdata/system/configs/emulationstation/scripts/game-selected/arcadematrix_mqtt.sh",
                        system,
                        str(rom_file),
                        title or rom,
                    ],
                    check=False,
                    timeout=5,
                )
            elif action == "start":
                subprocess.run(
                    [
                        "/userdata/system/configs/emulationstation/scripts/game-start/arcadematrix_mqtt.sh",
                        str(rom_file),
                        f"{rom}.zip",
                        title or rom,
                    ],
                    check=False,
                    timeout=5,
                )
            else:
                subprocess.run(
                    [
                        "/userdata/system/configs/emulationstation/scripts/game-end/arcadematrix_mqtt.sh",
                        str(rom_file),
                        system,
                    ],
                    check=False,
                    timeout=5,
                )
            return {"ok": True}

        if OS_FLAVOR == "retropie":
            rom_file = _touch_dummy_rom(Path(f"/home/pi/RetroPie/roms/{system}"), rom)
            if action == "start":
                subprocess.run(
                    [
                        "bash",
                        "/opt/retropie/configs/all/runcommand-onstart.sh",
                        system,
                        "lr-fbneo",
                        str(rom_file),
                        f"retroarch -L fbneo {rom_file}",
                    ],
                    check=False,
                    timeout=5,
                )
            elif action == "stop":
                subprocess.run(
                    ["bash", "/opt/retropie/configs/all/runcommand-onend.sh"],
                    check=False,
                    timeout=5,
                )
            else:
                Path("/tmp/es_state.inf").write_text(
                    f"SystemId={system}\nGamePath={rom_file}\nGame={title or rom}\nState=browsing\n",
                    encoding="utf-8",
                )
            return {"ok": True}

    return {"ok": False, "error": "Target not installed"}


class ControlHandler(BaseHTTPRequestHandler):
    def log_message(self, fmt: str, *args: Any) -> None:
        return

    def _send_json(self, code: int, obj: Dict[str, Any]) -> None:
        raw = json.dumps(obj).encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(raw)))
        self.end_headers()
        self.wfile.write(raw)

    def do_GET(self) -> None:
        if self.path.startswith("/health"):
            self._send_json(200, {"status": "ok", "os": OS_FLAVOR})
            return
        self._send_json(404, {"error": "not_found"})

    def do_POST(self) -> None:
        length = int(self.headers.get("Content-Length", "0"))
        body = json.loads(self.rfile.read(length).decode("utf-8") or "{}")
        if self.path == "/install":
            res = install_for_target(str(body.get("dmd_type", "")), str(body.get("dmd_host", "")))
            self._send_json(200 if res.get("ok") else 500, res)
            return
        if self.path == "/event":
            res = trigger_os_event(
                str(body.get("action", "start")),
                str(body.get("system", "neogeo")),
                str(body.get("rom", "mslug")),
                str(body.get("title", "")),
            )
            self._send_json(200 if res.get("ok") else 500, res)
            return
        self._send_json(404, {"error": "not_found"})


def main() -> None:
    _ensure_os_dirs()
    srv = ThreadingHTTPServer(("0.0.0.0", CONTROL_PORT), ControlHandler)
    print(f"[os-agent] OS={OS_FLAVOR} control server listening on :{CONTROL_PORT}", flush=True)
    srv.serve_forever()


if __name__ == "__main__":
    main()
