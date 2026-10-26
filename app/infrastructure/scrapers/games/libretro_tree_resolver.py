"""
Libretro Thumbnails & Archive.org Video Snaps Tree Resolver (`app/infrastructure/scrapers/games/libretro_tree_resolver.py`).

Harvests and caches GitHub `libretro-thumbnails/{repo}` file trees and Archive.org EmuMovies
`Video_Snaps` indexes per retro system, resolving verified HTTP 200 asset URLs with regional
priority `(France) -> (Europe) -> (World) -> (USA)`.
"""

from __future__ import annotations

import logging
from typing import Any, Callable, Dict, List, Optional, Tuple
import urllib.parse

from app.domain.taxonomy import (
    RETRO_SYSTEM_CATALOG,
    normalize_category_slug,
    resolve_canonical_system,
)
from app.infrastructure.scrapers.referential_utils import (
    _EDITION_SUBTITLE_SLUGS,
    _REGION_TAG_RE,
    _SUBTITLE_SPLIT_RE,
    is_excluded_nointro_filename,
    normalize_game_lookup_key,
    score_libretro_filename_for_lang,
)

logger = logging.getLogger("dmd_showcase.scrapers.referential.libretro")


class LibretroTreeResolver:
    """Resolves verified Libretro PNG boxart/title/snap and Archive.org MP4 URLs per system."""

    def __init__(self, store_factory: Callable[[str, int], Any]) -> None:
        self._store_factory = store_factory
        self._tree_stores: Dict[str, Any] = {}

    def get_tree_store(self, sys_key: str) -> Any:
        canon = resolve_canonical_system(sys_key)
        if canon not in self._tree_stores:
            self._tree_stores[canon] = self._store_factory(
                f"libretro_tree_{canon}", 604800
            )
        return self._tree_stores[canon]

    async def sync_system_libretro_tree(
        self, client: Any, sys_key: str
    ) -> Dict[str, List[str]]:
        """Fetch and cache `.png` filenames in `Named_Boxarts`/`Named_Titles`/`Named_Snaps` + `Video_Snaps`."""
        canon = resolve_canonical_system(sys_key)
        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        archive_video_repo = str(smeta.get("archive_video_repo") or "").strip()

        tstore = self.get_tree_store(canon)
        cached = tstore.load()
        folders = (cached.get("metadata") or {}).get("folders")
        has_pngs = isinstance(folders, dict) and bool(
            folders.get("Named_Boxarts")
            or folders.get("Named_Titles")
            or folders.get("Named_Snaps")
        )
        has_videos = isinstance(folders, dict) and (
            "Video_Snaps" in folders or not archive_video_repo
        )
        if has_pngs and has_videos:
            return folders  # type: ignore[return-value]

        result_folders: Dict[str, List[str]] = {
            "Named_Boxarts": list((folders or {}).get("Named_Boxarts") or [])
            if isinstance(folders, dict)
            else [],
            "Named_Titles": list((folders or {}).get("Named_Titles") or [])
            if isinstance(folders, dict)
            else [],
            "Named_Snaps": list((folders or {}).get("Named_Snaps") or [])
            if isinstance(folders, dict)
            else [],
            "Video_Snaps": list((folders or {}).get("Video_Snaps") or [])
            if isinstance(folders, dict)
            else [],
        }
        try:
            if not has_pngs:
                root_url = (
                    f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/master"
                )
                resp = await client.get(root_url, timeout=6.0)
                if resp.status_code == 200:
                    tree_nodes = resp.json().get("tree") or []
                    for node in tree_nodes:
                        folder_name = str(node.get("path") or "")
                        sha = str(node.get("sha") or "")
                        if (
                            folder_name in ("Named_Boxarts", "Named_Titles", "Named_Snaps")
                            and sha
                        ):
                            sub_url = f"https://api.github.com/repos/libretro-thumbnails/{repo}/git/trees/{sha}"
                            sub_resp = await client.get(sub_url, timeout=7.0)
                            if sub_resp.status_code == 200:
                                files = [
                                    str(item.get("path"))
                                    for item in (sub_resp.json().get("tree") or [])
                                    if str(item.get("path") or "").endswith(".png")
                                ]
                                result_folders[folder_name] = files

            if archive_video_repo and not result_folders["Video_Snaps"]:
                ia_url = f"https://archive.org/metadata/{archive_video_repo}/files"
                ia_resp = await client.get(ia_url, timeout=7.5)
                if ia_resp.status_code == 200:
                    ia_files = ia_resp.json().get("result") or []
                    mp4_files = [
                        str(item.get("name"))
                        for item in ia_files
                        if isinstance(item, dict)
                        and str(item.get("name") or "").endswith(".mp4")
                    ]
                    result_folders["Video_Snaps"] = mp4_files

            total_files = sum(len(v) for v in result_folders.values())
            if total_files > 0:
                primary_tokens = (
                    result_folders["Named_Boxarts"]
                    or result_folders["Named_Titles"]
                    or result_folders["Named_Snaps"]
                )[:500]
                tstore.save(
                    tokens=primary_tokens,
                    metadata={
                        "repo": repo,
                        "archive_video_repo": archive_video_repo,
                        "folders": result_folders,
                    },
                )
                logger.info(
                    "[REFERENTIAL:libretro] Indexed %d verified assets (%d video snaps) for system '%s' (%s)",
                    total_files,
                    len(result_folders["Video_Snaps"]),
                    canon,
                    repo,
                )
        except Exception as exc:
            logger.debug("[REFERENTIAL:libretro] Tree index skipped for %s: %s", canon, exc)
        return result_folders

    async def resolve_verified_libretro_urls(
        self,
        client: Any,
        sys_key: str,
        game_title: str,
        lang: str = "fr",
    ) -> Dict[str, Optional[str]]:
        """Match `game_title` against cached Libretro file tree and return verified asset URLs."""
        canon = resolve_canonical_system(sys_key)
        smeta = RETRO_SYSTEM_CATALOG.get(canon) or RETRO_SYSTEM_CATALOG["nes"]
        repo = smeta.get("libretro_repo") or "Nintendo_-_Nintendo_Entertainment_System"
        archive_video_repo = str(smeta.get("archive_video_repo") or "").strip()
        folders = await self.sync_system_libretro_tree(client, canon)
        target_key = normalize_game_lookup_key(game_title)
        if not target_key:
            return {"boxart": None, "title": None, "snap": None, "video": None}

        title_parts = _SUBTITLE_SPLIT_RE.split(game_title, maxsplit=1)
        short_key = ""
        if len(title_parts) > 1:
            sub_slug = normalize_category_slug(title_parts[1])
            if sub_slug in _EDITION_SUBTITLE_SLUGS:
                cand_short = normalize_game_lookup_key(title_parts[0])
                if len(cand_short) >= 6:
                    short_key = cand_short

        def _classify_asset_match(asset_fname: str) -> int:
            if is_excluded_nointro_filename(asset_fname):
                return 0
            fkey = normalize_game_lookup_key(asset_fname)
            if not fkey:
                return 0
            if fkey == target_key or (short_key and fkey == short_key):
                return 2
            if len(target_key) >= 6:
                stem = asset_fname
                low_s = stem.lower()
                if low_s.endswith(".ia.mp4"):
                    stem = stem[:-7]
                elif low_s.endswith((".png", ".mp4")):
                    stem = stem[:-4]
                stem = _REGION_TAG_RE.sub("", stem).strip()
                f_parts = _SUBTITLE_SPLIT_RE.split(stem, maxsplit=1)
                if len(f_parts) > 1:
                    f_base_key = normalize_game_lookup_key(f_parts[0])
                    f_sub_slug = normalize_category_slug(f_parts[1])
                    if f_base_key == target_key and not any(
                        bad in f_sub_slug
                        for bad in (
                            "venom",
                            "x_men",
                            "revenge",
                            "carnage",
                            "anxiety",
                            "clone",
                            "arcade_game",
                            "mythologies",
                            "sub_zero",
                            "shredder",
                            "hyperstone",
                            "turtles_in_time",
                        )
                    ):
                        return 1
            return 0

        out: Dict[str, Optional[str]] = {
            "boxart": None,
            "title": None,
            "snap": None,
            "video": None,
        }
        folder_map = {
            "boxart": "Named_Boxarts",
            "title": "Named_Titles",
            "snap": "Named_Snaps",
        }

        for slot_key, folder_name in folder_map.items():
            file_list = folders.get(folder_name) or []
            if not file_list:
                continue
            exact_matches: List[Tuple[int, str]] = []
            edition_matches: List[Tuple[int, str]] = []
            for fname in file_list:
                tier = _classify_asset_match(fname)
                if tier == 2:
                    exact_matches.append((score_libretro_filename_for_lang(fname, lang), fname))
                elif tier == 1:
                    edition_matches.append(
                        (score_libretro_filename_for_lang(fname, lang) - 10, fname)
                    )

            chosen_pool = exact_matches if exact_matches else edition_matches
            if chosen_pool:
                chosen_pool.sort(key=lambda item: item[0], reverse=True)
                best_fname = chosen_pool[0][1]
                encoded = urllib.parse.quote(best_fname)
                out[slot_key] = (
                    f"https://raw.githubusercontent.com/libretro-thumbnails/{repo}/master/"
                    f"{folder_name}/{encoded}"
                )

        video_list = folders.get("Video_Snaps") or []
        if archive_video_repo and video_list:
            v_exact: List[Tuple[int, str]] = []
            v_edition: List[Tuple[int, str]] = []
            for vname in video_list:
                tier = _classify_asset_match(vname)
                if tier == 0:
                    continue
                ia_bonus = 5 if vname.lower().endswith(".ia.mp4") else 0
                base_score = score_libretro_filename_for_lang(vname, lang) + ia_bonus
                if tier == 2:
                    v_exact.append((base_score, vname))
                else:
                    v_edition.append((base_score - 10, vname))
            v_pool = v_exact if v_exact else v_edition
            if v_pool:
                v_pool.sort(key=lambda item: item[0], reverse=True)
                best_vname = v_pool[0][1]
                out["video"] = (
                    f"https://archive.org/download/{archive_video_repo}/{urllib.parse.quote(best_vname)}"
                )

        return out
