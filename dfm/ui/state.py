"""应用状态 (单一数据源) — 各页面共享并通过信号联动。

包含: 当前分析配置、规则集、分析结果、最近工程、用户设置 (持久化到
``%APPDATA%/EvanDFM/settings.json``)。页面之间不直接互相引用, 一律通过
:class:`AppState` 的信号通信, 便于二次开发替换或新增页面。
"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import QObject, Signal

from dfm import paths
from dfm.core.rules_config import RulesConfig

DEFAULT_CONFIG: Dict[str, Any] = {
    "source_type": "",
    "source_path": "",
    "fab_path": "",
    "cad_nets": "",
    "design_params": "",
    "factory": "jlcpcb",
    "board_type": "rigid",
    "capability_level": "standard",
    "copper_um": 35.0,
    "copper_oz": None,
    "board_thickness": None,
    "modules": None,          # None = 全部
    "rules_path": "",
    "customer": "",
    "submitter": "",
    "extracta": "",
}

DEFAULT_SETTINGS: Dict[str, Any] = {
    "theme": "dark",
    "output_dir": "",
    "formats": ["excel", "html"],
    "embed_images": True,
    "load_plugins": True,
    "recent": [],
    "window": {"width": 1440, "height": 900, "maximized": False},
    "last_page": "home",
}

MAX_RECENT = 12


class AppState(QObject):
    """全局状态与页面间事件总线。"""

    config_changed = Signal()
    rules_changed = Signal()
    analysis_started = Signal()
    analysis_progress = Signal(str)
    analysis_finished = Signal(dict)
    analysis_failed = Signal(str)
    issue_selected = Signal(int)
    settings_changed = Signal()
    status = Signal(str)

    def __init__(self, parent: Optional[QObject] = None):
        super().__init__(parent)
        self.config: Dict[str, Any] = dict(DEFAULT_CONFIG)
        self.settings: Dict[str, Any] = json.loads(json.dumps(DEFAULT_SETTINGS))
        self.analysis: Optional[Dict[str, Any]] = None
        self.rules: RulesConfig = RulesConfig.load()
        self.busy = False
        self.load_settings()
        self.apply_rules_path()

    # ---------------- 设置 ----------------
    def load_settings(self) -> None:
        p = paths.settings_path()
        if os.path.isfile(p):
            try:
                with open(p, "r", encoding="utf-8") as f:
                    data = json.load(f) or {}
                for k, v in data.items():
                    if k in self.settings:
                        self.settings[k] = v
                cfg = data.get("config") or {}
                for k, v in cfg.items():
                    if k in self.config:
                        self.config[k] = v
            except Exception:  # noqa: BLE001
                pass

    def save_settings(self) -> None:
        try:
            data = dict(self.settings)
            data["config"] = {k: v for k, v in self.config.items()
                              if k not in ("source_path", "fab_path")}
            with open(paths.settings_path(), "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2, ensure_ascii=False, default=str)
        except Exception:  # noqa: BLE001
            pass

    def setting(self, key: str, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key: str, value: Any) -> None:
        self.settings[key] = value
        self.save_settings()
        self.settings_changed.emit()

    # ---------------- 规则集 ----------------
    def apply_rules_path(self, path: Optional[str] = None) -> RulesConfig:
        self.rules = RulesConfig.load(path or self.config.get("rules_path") or None)
        self.rules_changed.emit()
        return self.rules

    def reload_rules(self) -> RulesConfig:
        from dfm.core.capability import clear_capability_cache

        clear_capability_cache()
        return self.apply_rules_path()

    # ---------------- 配置 ----------------
    def set_config(self, **kwargs: Any) -> None:
        changed = False
        for k, v in kwargs.items():
            if k in self.config and self.config[k] != v:
                self.config[k] = v
                changed = True
        if changed:
            self.config_changed.emit()

    def analysis_config(self) -> Dict[str, Any]:
        """转换为 :func:`dfm.core.pipeline.analyze` 需要的配置。"""
        cfg = {k: v for k, v in self.config.items() if v not in ("", None)}
        cfg["rules_path"] = self.rules.source or cfg.get("rules_path") or None
        cfg["load_plugins"] = bool(self.setting("load_plugins", True))
        if cfg.get("extracta"):
            cfg["extracta"] = cfg["extracta"]
        return cfg

    # ---------------- 分析结果 ----------------
    def set_analysis(self, analysis: Dict[str, Any]) -> None:
        self.analysis = analysis
        self.add_recent()
        self.analysis_finished.emit(analysis)

    def clear_analysis(self) -> None:
        self.analysis = None

    def select_issue(self, index: int) -> None:
        self.issue_selected.emit(index)

    # ---------------- 最近工程 ----------------
    def add_recent(self) -> None:
        if not self.analysis:
            return
        src = self.analysis.get("source") or {}
        meta = self.analysis.get("board_meta") or {}
        item = {
            "path": src.get("path") or "",
            "type": src.get("type") or "",
            "board": meta.get("board_name") or "",
            "factory": self.analysis.get("factory_label") or "",
            "score": (self.analysis.get("risk") or {}).get("score"),
            "issues": (self.analysis.get("issue_summary") or {}).get("总计", 0),
            "time": meta.get("finish_time") or "",
            "config": {k: v for k, v in self.config.items()
                       if k in ("factory", "board_type", "capability_level",
                                "copper_um", "board_thickness")},
        }
        if not item["path"]:
            return
        recent: List[dict] = [r for r in self.settings.get("recent", [])
                              if r.get("path") != item["path"]]
        recent.insert(0, item)
        self.settings["recent"] = recent[:MAX_RECENT]
        self.save_settings()

    def recent(self) -> List[dict]:
        return list(self.settings.get("recent") or [])

    def output_dir(self) -> str:
        d = self.setting("output_dir") or ""
        if d and os.path.isdir(d):
            return d
        return paths.default_output_dir()

    # ---------------- 结果便捷访问 ----------------
    @property
    def issues(self) -> List[dict]:
        return list((self.analysis or {}).get("issues") or [])

    @property
    def summary(self) -> Dict[str, int]:
        return dict((self.analysis or {}).get("issue_summary") or {})

    @property
    def risk(self) -> Dict[str, Any]:
        return dict((self.analysis or {}).get("risk") or {})

    def has_geometry(self) -> bool:
        return bool((self.analysis or {}).get("fab_data"))
