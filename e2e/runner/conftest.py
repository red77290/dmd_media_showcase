"""
Pytest configuration and automated JSON + Markdown report generator for the
100% local, air-gapped E2E Frontend Sync + Showcase Docker Compose testbench.
"""

from __future__ import annotations

import json
import os
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Dict, List

import pytest

SCENARIO_RECORDS: List[Dict[str, Any]] = []
_SUITE_STARTED_MONOTONIC = time.monotonic()


def record_scenario_evidence(entry: Dict[str, Any]) -> None:
    SCENARIO_RECORDS.append(entry)


@pytest.hookimpl(tryfirst=True, hookwrapper=True)
def pytest_runtest_makereport(item: pytest.Item, call: pytest.CallInfo[None]):
    outcome = yield
    rep = outcome.get_result()
    if rep.when == "call":
        ev = getattr(item, "e2e_evidence", None)
        if isinstance(ev, dict):
            ev["status"] = "PASSED" if rep.passed else "FAILED"
            ev["duration_s"] = round(rep.duration, 3)
            if rep.failed:
                ev["failure"] = str(rep.longrepr)
            record_scenario_evidence(ev)
        else:
            record_scenario_evidence(
                {
                    "id": item.name,
                    "dmd": "unknown",
                    "os": "unknown",
                    "status": "PASSED" if rep.passed else "FAILED",
                    "duration_s": round(rep.duration, 3),
                    "failure": str(rep.longrepr) if rep.failed else "",
                }
            )


def pytest_sessionfinish(session: pytest.Session, exitstatus: int) -> None:
    report_dir = Path(os.environ.get("E2E_REPORT_DIR", "/reports"))
    report_dir.mkdir(parents=True, exist_ok=True)
    total_dur = round(time.monotonic() - _SUITE_STARTED_MONOTONIC, 2)
    passed = sum(1 for r in SCENARIO_RECORDS if r.get("status") == "PASSED")
    failed = sum(1 for r in SCENARIO_RECORDS if r.get("status") != "PASSED")

    payload = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(),
        "network_isolation": "docker bridge (internal: true, zero internet access)",
        "total_scenarios": len(SCENARIO_RECORDS),
        "passed": passed,
        "failed": failed,
        "suite_duration_s": total_dur,
        "exit_status": exitstatus,
        "scenarios": SCENARIO_RECORDS,
    }
    (report_dir / "e2e_validation_report.json").write_text(
        json.dumps(payload, indent=2), encoding="utf-8"
    )

    lines = [
        "# Local Containerized E2E Validation Report (`FrontendSync` + `dmd_media_showcase`)",
        "",
        f"- **Generated (UTC)**: `{payload['generated_at_utc']}`",
        "- **Runtime Network**: `dmd_e2e_net` (`driver: bridge`, `internal: true` — 100% offline/air-gapped)",
        f"- **Summary**: **{passed} / {len(SCENARIO_RECORDS)} PASSED** ({failed} failed) in `{total_dur}s`",
        "",
        "## Scenario Matrix",
        "",
        "| ID | DMD Target | Gaming OS | Game (`sys/rom`) | Marquee Verified | Showcase Stream Verified | Stop & Cleanup | Duration | Status |",
        "|---|---|---|---|---|---|---|---|---|",
    ]
    for r in SCENARIO_RECORDS:
        lines.append(
            f"| `{r.get('id', '')}` | `{r.get('dmd', '')}` | `{r.get('os', '')}` | "
            f"`{r.get('game', '-')}` | `{r.get('marquee_verified', False)}` | "
            f"`{r.get('showcase_verified', '-')}` | `{r.get('cleanup_verified', False)}` | "
            f"`{r.get('duration_s', 0)}s` | **{r.get('status', '')}** |"
        )

    lines.extend(["", "## Per-Scenario Telemetry Details", ""])
    for r in SCENARIO_RECORDS:
        lines.append(f"### `{r.get('id')}` — `{r.get('dmd')}` × `{r.get('os')}` ({r.get('status')})")
        for k, v in r.items():
            if k in ("id", "dmd", "os", "status"):
                continue
            lines.append(f"- **{k}**: `{v}`")
        lines.append("")

    (report_dir / "e2e_validation_report.md").write_text("\n".join(lines), encoding="utf-8")
