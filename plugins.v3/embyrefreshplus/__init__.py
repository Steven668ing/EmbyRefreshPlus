"""EmbyRefreshPlus: per-library Emby refresh after MoviePilot transfers."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any, Optional

from app.application.mediaserver import MediaServerHelper, get_mediaserver_configs
from app.core.event import Event, eventmanager
from app.log import logger
from app.sdk.network import RequestUtils
from app.modules.emby.emby import Emby
from app.plugins import _PluginBase
from app.schemas import TransferInfo
from app.schemas.types import EventType


class EmbyRefreshPlus(_PluginBase):
    """Batch transfer events and refresh matched Emby directory items only."""
    plugin_name = "Emby精准刷新增强"
    plugin_desc = "整理完成后仅刷新对应 Emby 媒体库目录，不执行全库扫描。"
    plugin_icon = "embyrefreshplus.png"
    plugin_version = "1.0.0"
    plugin_author = "Steven"
    author_url = ""
    plugin_config_prefix = "embyrefreshplus_"
    plugin_order = 15
    auth_level = 1

    def __init__(self) -> None:
        super().__init__()
        self._enabled = False
        self._delay = 10
        self._servers: list[str] = []
        self._ignored_paths = ["/media-115-CD2", "/media-115-strm"]
        self._pending: dict[str, set[str]] = {}
        self._timers: dict[str, threading.Timer] = {}
        self._lock = threading.RLock()
        self._generation = 0

    def init_plugin(self, config: Optional[dict] = None) -> None:
        self.stop_service()
        config = config or {}
        self._enabled = bool(config.get("enabled", False))
        try:
            self._delay = max(0, min(300, int(config.get("delay", 10))))
        except (TypeError, ValueError):
            self._delay = 10
        self._servers = list(config.get("mediaservers") or [])
        paths = config.get("ignore_paths", ["/media-115-CD2", "/media-115-strm"])
        if isinstance(paths, str):
            paths = paths.splitlines()
        self._ignored_paths = [str(p).strip().rstrip("/") for p in paths if str(p).strip()]

    def get_state(self) -> bool:
        return self._enabled

    @staticmethod
    def get_command() -> list[dict[str, Any]]:
        return []

    def get_api(self) -> list[dict[str, Any]]:
        return []

    def get_page(self) -> list[dict]:
        return []

    def get_form(self) -> tuple[list[dict], dict[str, Any]]:
        configs = get_mediaserver_configs()
        embys = [{"title": c.name, "value": c.name} for c in configs.values()
                 if c.name and c.type == "emby"]
        return [{"component": "VForm", "content": [
            {"component": "VSwitch", "props": {"model": "enabled", "label": "启用插件"}},
            {"component": "VSelect", "props": {"model": "mediaservers", "label": "Emby 媒体服务器",
                "items": embys, "multiple": True, "chips": True, "clearable": True}},
            {"component": "VTextField", "props": {"model": "delay", "label": "刷新延迟（秒）", "type": "number"}},
            {"component": "VTextarea", "props": {"model": "ignore_paths", "label": "忽略路径（每行一个）", "rows": 4}},
        ]}], {"enabled": False, "delay": 10, "mediaservers": [],
              "ignore_paths": ["/media-115-CD2", "/media-115-strm"]}

    def _is_ignored(self, path: str) -> bool:
        candidate = Path(path).as_posix().rstrip("/")
        return any(candidate == prefix or candidate.startswith(prefix + "/")
                   for prefix in self._ignored_paths)

    @eventmanager.register(EventType.TransferComplete)
    def on_transfer_complete(self, event: Event) -> None:
        if not self._enabled or not self._servers:
            return
        data = event.event_data or {}
        transfer: TransferInfo | dict | None = data.get("transferinfo")
        if not transfer:
            return
        target = transfer.get("target_diritem") if isinstance(transfer, dict) else transfer.target_diritem
        path = target.get("path") if isinstance(target, dict) else getattr(target, "path", None)
        if not path:
            return
        path = Path(str(path)).as_posix()
        logger.info(f"[EmbyRefreshPlus] 收到整理事件；路径：{path}")
        if self._is_ignored(path):
            logger.info(f"[EmbyRefreshPlus] 判断：忽略路径，跳过刷新：{path}")
            return
        with self._lock:
            for name in self._servers:
                self._pending.setdefault(name, set()).add(path)
                old = self._timers.get(name)
                if old:
                    old.cancel()
                timer = threading.Timer(self._delay, self._flush, args=(name, self._generation))
                timer.daemon = True
                self._timers[name] = timer
                timer.start()

    def _flush(self, server_name: str, generation: int) -> None:
        with self._lock:
            if generation != self._generation or not self._enabled:
                return
            paths = sorted(self._pending.pop(server_name, set()))
            self._timers.pop(server_name, None)
        if not paths:
            return
        service = MediaServerHelper().get_services(type_filter="emby", name_filters=[server_name]).get(server_name)
        if not service or not isinstance(service.instance, Emby):
            logger.error(f"[EmbyRefreshPlus] 执行刷新：失败；无法取得 Emby 实例：{server_name}")
            return
        emby = service.instance
        folders = getattr(emby, "folders", []) or []
        for path in paths:
            match = self._match_library(path, folders)
            if not match:
                logger.warning(f"[EmbyRefreshPlus] 判断：未匹配到媒体库；路径：{path}；不执行全库扫描")
                continue
            library_name, matched_path, item_id = match
            logger.info(f"[EmbyRefreshPlus] 判断：{library_name}媒体库；目录：{matched_path}")
            ok = self._refresh_item(emby, item_id)
            logger.info(f"[EmbyRefreshPlus] 执行刷新：{'成功' if ok else '失败'}；服务器：{server_name}；路径：{path}")

    @staticmethod
    def _match_library(target_path: str, folders: list[dict]) -> Optional[tuple[str, str, str]]:
        target = Path(target_path)
        best: Optional[tuple[int, str, str, str]] = None
        for folder in folders:
            if not isinstance(folder, dict):
                continue
            for candidate in [folder, *(folder.get("SubFolders") or [])]:
                if not isinstance(candidate, dict) or not candidate.get("Path") or not candidate.get("Id"):
                    continue
                try:
                    base = Path(str(candidate["Path"]))
                    if target == base or target.is_relative_to(base):
                        depth = len(base.parts)
                        if best is None or depth > best[0]:
                            best = (depth, str(folder.get("Name") or candidate.get("Name") or "媒体库"),
                                    str(base), str(candidate["Id"]))
                except (OSError, ValueError):
                    continue
        return (best[1], best[2], best[3]) if best else None

    @staticmethod
    def _refresh_item(emby: Emby, item_id: str) -> bool:
        """Emby item-scoped refresh endpoint; deliberately never calls Library/Refresh."""
        if not emby._host or not emby._apikey:
            return False
        response = RequestUtils(timeout=20).post_res(
            f"{emby._host}emby/Items/{item_id}/Refresh",
            params={"Recursive": "true", "api_key": emby._apikey},
        )
        return bool(response)

    def stop_service(self) -> None:
        with self._lock:
            self._generation += 1
            for timer in self._timers.values():
                timer.cancel()
            self._timers.clear()
            self._pending.clear()
        self._enabled = False
