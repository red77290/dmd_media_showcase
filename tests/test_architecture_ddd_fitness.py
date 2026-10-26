"""
Automated Hexagonal & DDD Architectural Fitness Suite (`tests/test_architecture_ddd_fitness.py`).

Enforces structural invariants continuously so God Classes, layer coupling, or inline HTML/JS
monoliths can never regress:
1. Strict inward dependency rule for `app/domain/` (zero outward imports).
2. Strict inward dependency rule for `app/application/` (depends only on `app/domain/` & ports).
3. Anti-God-Module (`<= 800` lines) & Anti-God-Class (`<= 750` lines) budgets across `app/`,
   plus strict facade caps on refactored entrypoints and zero inline `<style>`/`<script>` in `.py`.
4. Modular static WebUI ES-module graph integrity (`<= 600` lines per `.js` file, valid relative
   imports, and 100% DOM `onclick`/`onchange`/`oninput` binding in `WINDOW_HANDLERS`).
5. True genericity & reusability verification of `BoundedTtlCache`, `DualSlotPipeline`,
   `WeightedPlaylistCursor`, `PriorityPlaybackQueue`, `SynonymCatalogResolver`, and
   `BoundedWorkerExecutorMixin` on domain-independent payloads.
"""

from __future__ import annotations

import ast
import asyncio
import re
import shutil
import subprocess
from pathlib import Path
from typing import Iterable, List, Set, Tuple

import pytest

from app.config import settings
from app.domain.aggregates import (
    BoundedTtlCache,
    DualSlotPipeline,
    PriorityPlaybackQueue,
    QueuedOnDemandEntry,
    WeightedPlaylistCursor,
)
from app.domain.entities import FilterCriteria, MediaCandidate, PanelGeometry
from app.domain.taxonomy import SynonymCatalogResolver
from app.infrastructure.web.dashboard_loader import (
    STATIC_WEB_DIR,
    iter_static_css_files,
    iter_static_js_files,
)
from app.scheduler.executor_lifecycle import BoundedWorkerExecutorMixin

REPO_ROOT = Path(__file__).resolve().parent.parent
APP_ROOT = REPO_ROOT / "app"


def _collect_ast_imports(pkg_dir: Path) -> List[Tuple[str, str]]:
    """Return `(relative_file_path, imported_module_name)` for every `.py` file under `pkg_dir`."""
    imports: List[Tuple[str, str]] = []
    for py_file in sorted(pkg_dir.rglob("*.py")):
        tree = ast.parse(py_file.read_text(encoding="utf-8"), filename=str(py_file))
        rel_path = py_file.relative_to(APP_ROOT).as_posix()
        for node in ast.walk(tree):
            if isinstance(node, ast.Import):
                for alias in node.names:
                    imports.append((rel_path, alias.name))
            elif isinstance(node, ast.ImportFrom) and node.module:
                imports.append((rel_path, node.module))
    return imports


def _find_forbidden_imports(
    imports: Iterable[Tuple[str, str]],
    forbidden_prefixes: Tuple[str, ...],
) -> List[Tuple[str, str]]:
    return [
        (rel_file, mod)
        for rel_file, mod in imports
        if any(mod == prefix or mod.startswith(f"{prefix}.") for prefix in forbidden_prefixes)
    ]


def test_domain_layer_zero_outward_dependencies():
    """Verify `app/domain/` is 100% pure Python with zero Application, Infrastructure, or HTTP/IO imports."""
    forbidden = (
        "app.application",
        "app.infrastructure",
        "app.scheduler",
        "app.providers",
        "app.models",
        "app.main",
        "app.container",
        "fastapi",
        "starlette",
        "uvicorn",
        "httpx",
        "PIL",
        "cv2",
        "yt_dlp",
    )
    violations = _find_forbidden_imports(_collect_ast_imports(APP_ROOT / "domain"), forbidden)
    assert not violations, f"Domain layer inward-dependency violations: {violations}"


def test_application_layer_depends_only_on_domain_and_ports():
    """Verify `app/application/` never imports concrete `app.infrastructure.*`, `app.models`, or FastAPI."""
    forbidden = (
        "app.infrastructure",
        "app.scheduler",
        "app.providers",
        "app.models",
        "app.main",
        "fastapi",
        "starlette",
        "uvicorn",
        "httpx",
        "PIL",
        "cv2",
        "yt_dlp",
    )
    violations = _find_forbidden_imports(_collect_ast_imports(APP_ROOT / "application"), forbidden)
    assert not violations, f"Application layer Hexagonal port-isolation violations: {violations}"


def test_anti_god_module_and_anti_god_class_budgets():
    """
    Enforce strict module (`<= 800` lines) and class (`<= 750` lines) budgets across `app/`,
    strict caps on refactored entrypoints/facades, and zero embedded `<style>`/`<script>` in Python.
    """
    max_module_lines = 800
    max_class_lines = 750
    strict_facade_caps = {
        "main.py": 260,
        "asyncapi_spec.py": 60,
        "application/consume_once_orchestrator.py": 550,
        "infrastructure/web_admin.py": 80,
        "infrastructure/scrapers/referential_store.py": 200,
        "infrastructure/scrapers/games/screenscraper_adapter.py": 420,
        "infrastructure/persistence/dmd_profile_store.py": 320,
        "scheduler/asset_builder.py": 120,
        "scheduler/worker_pool.py": 80,
    }

    oversized_modules: List[Tuple[str, int]] = []
    oversized_classes: List[Tuple[str, str, int]] = []
    inline_ui_leaks: List[str] = []

    for py_file in sorted(APP_ROOT.rglob("*.py")):
        rel = py_file.relative_to(APP_ROOT).as_posix()
        src = py_file.read_text(encoding="utf-8")
        line_count = len(src.splitlines())

        if line_count > max_module_lines:
            oversized_modules.append((rel, line_count))

        if rel in strict_facade_caps:
            assert line_count <= strict_facade_caps[rel], (
                f"Facade {rel} exceeded cap ({line_count} > {strict_facade_caps[rel]} lines)"
            )

        if "<style>" in src or "<script" in src:
            inline_ui_leaks.append(rel)

        tree = ast.parse(src, filename=str(py_file))
        for node in ast.walk(tree):
            if isinstance(node, ast.ClassDef) and getattr(node, "end_lineno", None):
                cls_lines = node.end_lineno - node.lineno + 1
                if cls_lines > max_class_lines:
                    oversized_classes.append((rel, node.name, cls_lines))

    assert not oversized_modules, f"Modules exceeding {max_module_lines} lines: {oversized_modules}"
    assert not oversized_classes, f"Classes exceeding {max_class_lines} lines: {oversized_classes}"
    assert not inline_ui_leaks, f"Embedded HTML <style>/<script> found inside Python modules: {inline_ui_leaks}"


def test_webui_static_es_modules_resolution_and_dom_handler_binding():
    """
    Verify static WebUI modularity (`<= 600` lines per ES module), valid relative ES imports,
    and 100% registration of inline HTML `on*` handlers in `static/js/app.js`.
    """
    js_files = iter_static_js_files()
    assert len(js_files) >= 15

    import_re = re.compile(r"""from\s+['"](\.[^'"]+)['"]""")
    for js_path in js_files:
        src = js_path.read_text(encoding="utf-8")
        lines = len(src.splitlines())
        assert lines <= 600, f"ES module {js_path.name} exceeds 600 lines ({lines}L)"

        for rel_spec in import_re.findall(src):
            target = (js_path.parent / rel_spec).resolve()
            assert target.exists() and target.is_file(), (
                f"Broken relative ES import '{rel_spec}' in {js_path.relative_to(STATIC_WEB_DIR)}"
            )

    html = (STATIC_WEB_DIR / "index.html").read_text(encoding="utf-8")
    app_entry_js = (STATIC_WEB_DIR / "js" / "app.js").read_text(encoding="utf-8")
    wh_match = re.search(r"const\s+WINDOW_HANDLERS\s*=\s*\{([^}]+)\}", app_entry_js, re.S)
    assert wh_match, "Expected WINDOW_HANDLERS map in static/js/app.js"
    window_handlers = {tok.strip() for tok in wh_match.group(1).split(",") if tok.strip()}

    # Extract every inline handler call `on<event>="fnName(...)"` from index.html AND dynamic JS templates
    inline_attr_calls: Set[str] = set()
    for src_doc in [html, *(p.read_text(encoding="utf-8") for p in js_files)]:
        for expr in re.findall(r'\bon(?:click|change|input|keydown|submit)="([^"]+)"', src_doc):
            for fn_match in re.findall(r"\b([a-zA-Z_$][a-zA-Z0-9_$]*)\s*\(", expr):
                if fn_match not in {"event", "if", "for", "while", "return", "Number", "String", "Boolean"}:
                    inline_attr_calls.add(fn_match)

    assert len(inline_attr_calls) >= 60, "Expected >= 60 inline DOM & template event handlers"
    missing_bindings = sorted(inline_attr_calls - window_handlers)
    assert not missing_bindings, f"DOM handlers missing from app.js WINDOW_HANDLERS: {missing_bindings}"

    node_bin = shutil.which("node")
    if node_bin:
        app_url = (STATIC_WEB_DIR / "js" / "app.js").resolve().as_uri()
        eval_res = subprocess.run(
            [node_bin, "--input-type=module", "-e", f"await import({app_url!r});"],
            capture_output=True,
            text=True,
        )
        assert eval_res.returncode == 0, f"ES module evaluation failed in Node.js: {eval_res.stderr}"


def test_webui_css_brace_balance_cascade_order_and_responsive_tables():
    """
    Verify CSS syntactic integrity (balanced braces, zero accidental unclosed selector nesting),
    strict stylesheet cascade order (`dashboard.css` -> `studio.css` -> `responsive.css`),
    and responsive table wrappers + sticky action columns so buttons never get clipped.
    """
    css_files = iter_static_css_files()
    assert [p.name for p in css_files] == ["dashboard.css", "studio.css", "responsive.css"]

    for css_path in css_files:
        raw_css = css_path.read_text(encoding="utf-8")
        clean_css = re.sub(r"/\*.*?\*/", "", raw_css, flags=re.S)
        block_stack: List[Tuple[int, str]] = []
        buf = ""
        line_no = 1
        for ch in clean_css:
            if ch == "\n":
                line_no += 1
            if ch == "{":
                header = buf.strip().splitlines()[-1].strip() if buf.strip() else ""
                assert not (block_stack and not block_stack[-1][1].startswith("@")), (
                    f"Unclosed CSS block in {css_path.name}:{block_stack[-1][0]} "
                    f"('{block_stack[-1][1]}') swallowed '{header}' at line {line_no}"
                )
                block_stack.append((line_no, header))
                buf = ""
            elif ch == "}":
                assert block_stack, f"Stray closing '}}' in {css_path.name}:{line_no}"
                block_stack.pop()
                buf = ""
            else:
                buf += ch
        assert not block_stack, f"Unclosed '{{' at EOF in {css_path.name}: {block_stack}"

    # Base stylesheets must not define @media rules that could be overridden by later base rules;
    # all responsive @media breakpoints belong in responsive.css loaded last.
    dash_css = (STATIC_WEB_DIR / "css" / "dashboard.css").read_text(encoding="utf-8")
    studio_css = (STATIC_WEB_DIR / "css" / "studio.css").read_text(encoding="utf-8")
    resp_css = (STATIC_WEB_DIR / "css" / "responsive.css").read_text(encoding="utf-8")
    assert "@media" not in dash_css
    assert "@media" not in studio_css
    assert "@media (max-width: 960px)" in resp_css
    assert "@media (max-width: 720px)" in resp_css

    # Verify top-level modal backdrop hiding and responsive table / sticky-control invariants
    assert re.search(r"^\.modal-backdrop\s*\{[^}]*display:\s*none;", dash_css, re.M | re.S)
    assert ".table-wrap" in dash_css and "overflow-x: auto" in dash_css
    assert "position: sticky;" in dash_css and "right: 0;" in dash_css
    assert ".slot-pill" in dash_css and "text-overflow: ellipsis;" in dash_css

    html = (STATIC_WEB_DIR / "index.html").read_text(encoding="utf-8")
    table_count = len(re.findall(r"<table\b", html))
    wrap_count = len(re.findall(r'<div\s+class="table-wrap"', html))
    assert table_count >= 2 and wrap_count == table_count, (
        f"Every <table> in index.html must be wrapped in .table-wrap ({wrap_count}/{table_count})"
    )


def test_generic_domain_aggregates_reusability_on_arbitrary_types():
    """
    Prove that Domain Aggregates (`BoundedTtlCache`, `DualSlotPipeline`, `WeightedPlaylistCursor`,
    `PriorityPlaybackQueue`, `SynonymCatalogResolver`) are genuinely generic and reusable.
    """
    # 1. BoundedTtlCache[str, int] with deterministic synthetic clock and eviction hook
    fake_now = [100.0]
    evicted_log: List[Tuple[str, int]] = []
    cache: BoundedTtlCache[str, int] = BoundedTtlCache(
        ttl_seconds=10.0,
        max_entries=2,
        on_evict=lambda k, v: evicted_log.append((k, v)),
        clock=lambda: fake_now[0],
    )
    cache.put("a", 1)
    fake_now[0] = 102.0
    cache.put("b", 2)
    fake_now[0] = 104.0
    cache.put("c", 3)  # Overflows max_entries=2 -> evicts oldest ("a", 1)
    assert cache.get("a") is None
    assert evicted_log == [("a", 1)]
    assert cache.get("b") == 2

    # Advance past TTL for "b" (`104 + 9 = 113`, `113 - 102 = 11 > 10`)
    fake_now[0] = 113.0
    assert cache.get("b") is None
    assert cache.get("c") == 3
    assert ("b", 2) in evicted_log

    # `pop()` transfers ownership without triggering `on_evict`
    assert cache.pop("c") == 3
    assert ("c", 3) not in evicted_log

    # 2. DualSlotPipeline[str, Tuple[str, int]] double-buffering & priority demotion
    pipeline: DualSlotPipeline[str, Tuple[str, int]] = DualSlotPipeline(item_id_getter=lambda x: x[0])
    assert pipeline.store_prepared("lane_1", ("pkt_1", 10)) == "ready"
    assert pipeline.store_prepared("lane_1", ("pkt_2", 20)) == "standby"
    assert pipeline.is_full("lane_1") is True

    claimed, promoted = pipeline.claim_ready("lane_1")
    assert claimed == ("pkt_1", 10)
    assert promoted == ("pkt_2", 20)
    assert pipeline.ready["lane_1"] == ("pkt_2", 20)
    assert not pipeline.has_standby("lane_1")

    # Priority injection demotes existing Ready into empty Standby without losing pre-warmed work
    displaced = pipeline.promote_priority_ready("lane_1", ("urgent_pkt", 99))
    assert displaced is None
    assert pipeline.ready["lane_1"] == ("urgent_pkt", 99)
    assert pipeline.standby["lane_1"] == ("pkt_2", 20)
    assert pipeline.pop_active("pkt_1") == ("pkt_1", 10)

    # 3. WeightedPlaylistCursor deterministic slot quota rotation
    cursor = WeightedPlaylistCursor()
    playlist = [
        {"domain": "sensor_temp", "count": 2, "enabled": True},
        {"domain": "sensor_DISABLED", "count": 5, "enabled": False},
        {"domain": "sensor_hum", "count": 1, "enabled": True},
    ]
    seq = [cursor.advance("board_a", playlist)["domain"] for _ in range(4)]
    assert seq == ["sensor_temp", "sensor_temp", "sensor_hum", "sensor_temp"]

    # 4. PriorityPlaybackQueue FIFO `enqueue_next` vs head-preempting `play_now`
    pq = PriorityPlaybackQueue()
    geom = PanelGeometry(width=128, height=32)
    crit = FilterCriteria(domain="games")

    def _mk_cand(cid: str, title: str) -> MediaCandidate:
        return MediaCandidate(
            id=cid,
            domain="games",
            title=title,
            year="1992",
            badge="SNES",
            badge_color="#ff5500",
            subtitle="",
            rating="9.0",
            synopsis="",
        )

    cand1 = _mk_cand("g1", "Alpha")
    cand2 = _mk_cand("g2", "Beta")
    cand3 = _mk_cand("g3", "Urgent")
    pq.enqueue(QueuedOnDemandEntry("q1", "dmd_1", "games", cand1, geom, crit, mode="enqueue_next"))
    pq.enqueue(QueuedOnDemandEntry("q2", "dmd_1", "games", cand2, geom, crit, mode="enqueue_next"))
    pq.enqueue(QueuedOnDemandEntry("q3", "dmd_1", "games", cand3, geom, crit, mode="play_now"))
    assert [e.queue_id for e in pq.list_entries("dmd_1")] == ["q3", "q1", "q2"]
    assert pq.remove("dmd_1", "q1").candidate.title == "Alpha"
    assert [e.queue_id for e in pq.list_entries("dmd_1")] == ["q3", "q2"]

    # 5. SynonymCatalogResolver on a synthetic non-media hardware catalog
    hw_catalog = {
        "hub75_rgb": {
            "synonyms": ["hub75", "led-matrix-panel", "rgb565_panel"],
            "labels": {"en": "HUB75 Matrix", "fr": "Matrice LED HUB75"},
        },
        "oled_ssd1306": {
            "synonyms": ["ssd1306", "i2c_oled"],
            "labels": {"en": "Monochrome OLED"},
        },
    }
    resolver = SynonymCatalogResolver(hw_catalog, synonym_fields=("synonyms",), include_labels=True)
    assert resolver.resolve_exact("Matrice LED HUB75") == "hub75_rgb"
    assert resolver.resolve_with_substring_fallback("waveshare_ssd1306_128x64") == "oled_ssd1306"


@pytest.mark.asyncio
async def test_generic_bounded_worker_executor_mixin_lifecycle(monkeypatch):
    """Verify `BoundedWorkerExecutorMixin[TJob]` runs generic async jobs, scales workers, and stops cleanly."""
    monkeypatch.setattr(settings, "use_process_pool", False)
    monkeypatch.setattr(settings, "max_concurrent_jobs", 2)

    processed: List[int] = []

    class _IntWorkerPool(BoundedWorkerExecutorMixin[Tuple[int, int]]):
        def __init__(self) -> None:
            self._init_executor_state()

        async def _worker_loop(self, worker_id: int) -> None:
            while self._running and worker_id < self._target_workers:
                try:
                    prio, value = await asyncio.wait_for(self._queue.get(), timeout=0.05)
                except asyncio.TimeoutError:
                    continue
                self._busy_worker_ids.add(worker_id)
                try:
                    processed.append(value * 10)
                    self._completed_jobs += 1
                finally:
                    self._busy_worker_ids.discard(worker_id)
                    self._queue.task_done()

    pool = _IntWorkerPool()
    pool.start()
    try:
        await pool._queue.put((1, 3))
        await pool._queue.put((2, 7))
        await asyncio.wait_for(pool._queue.join(), timeout=2.0)
        assert sorted(processed) == [30, 70]
        assert pool.completed_jobs_total == 2

        monkeypatch.setattr(settings, "max_concurrent_jobs", 3)
        new_workers = await pool.reconfigure_workers()
        assert new_workers == 3
    finally:
        await pool.stop()
        assert pool._running is False
