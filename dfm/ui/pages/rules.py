"""规则库页: 板厂能力库 / 规则集(开关·阈值·严重度·自定义规则) / 插件管理。

所有修改都写入用户规则目录 (``%APPDATA%/EvanDFM/rules``), 不改动随包发布的出厂配置,
因此升级软件不会丢失客户自定义标准。
"""
from __future__ import annotations

import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QAbstractItemView, QCheckBox, QComboBox, QFileDialog,
                               QFrame, QHBoxLayout, QHeaderView, QLabel, QMessageBox,
                               QTabWidget, QTableWidget, QTableWidgetItem, QVBoxLayout,
                               QWidget)

from dfm import paths
from dfm.core import registry
from dfm.core.capability import (capability_file, factory_labels, list_board_types,
                                 list_factories, list_levels)
from dfm.core.issue import SEVERITY_LEVELS
from dfm.core.rules_config import RulesConfig, default_config_path, template_text
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import colors
from dfm.ui.widgets.common import Card, LogView, button, muted


class RulesPage(BasePage):
    page_id = "rules"
    title = "规则库"
    subtitle = "板厂制程能力库 · 规则开关与阈值 · 自定义规则 · 插件"
    icon = "sliders"
    group = "规则"
    order = 60

    def build(self) -> None:
        self.tabs = QTabWidget()
        self.root.addWidget(self.tabs, 1)
        self.tabs.addTab(self._build_capability_tab(), "板厂能力库")
        self.tabs.addTab(self._build_rules_tab(), "规则集 (阈值/严重度)")
        self.tabs.addTab(self._build_custom_tab(), "自定义规则")
        self.tabs.addTab(self._build_plugin_tab(), "插件与检查项")
        self.refresh()

    # ---------------- Tab 1: 能力库 ----------------
    def _build_capability_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(muted(
            "制程能力库是 DFM 判定依据 (YAML)。新增一家板厂 = 复制一份 YAML 改数值并放入 "
            "rules/capability/ 目录, 无需改代码; 用户目录中的同名文件会覆盖出厂文件。"))
        self.cap_table = QTableWidget(0, 6)
        self.cap_table.setHorizontalHeaderLabels(
            ["板厂 id", "显示名", "板型", "能力等级", "文件", "来源"])
        self.cap_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.cap_table.horizontalHeader().setStretchLastSection(True)
        self.cap_table.setColumnWidth(0, 110)
        self.cap_table.setColumnWidth(1, 130)
        self.cap_table.setColumnWidth(2, 150)
        self.cap_table.setColumnWidth(3, 210)
        self.cap_table.setColumnWidth(4, 380)
        self.cap_table.verticalHeader().setVisible(False)
        self.cap_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        self.cap_table.setSelectionBehavior(QAbstractItemView.SelectRows)
        lay.addWidget(self.cap_table, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("打开文件", "file", "", self._open_capability))
        bar.addWidget(button("打开能力库目录", "folder", "", self._open_capability_dir))
        bar.addWidget(button("复制为用户副本", "save", "", self._copy_capability))
        bar.addWidget(button("重新加载", "refresh", "Primary", self._reload))
        bar.addStretch(1)
        lay.addWidget(self._wrap(bar))
        return host

    # ---------------- Tab 2: 规则集 ----------------
    def _build_rules_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)

        top = QHBoxLayout()
        top.setSpacing(10)
        self.rules_path_label = QLabel("")
        self.rules_path_label.setStyleSheet(f"font-family:Consolas,monospace;"
                                            f"color:{colors()['fg_dim']};")
        self.rules_path_label.setTextInteractionFlags(Qt.TextSelectableByMouse)
        top.addWidget(self.rules_path_label, 1)
        top.addWidget(button("导出模板", "export", "", self._export_template))
        top.addWidget(button("打开规则目录", "folder", "", self._open_rules_dir))
        top.addWidget(button("重新载入", "refresh", "", self._reload))
        top.addWidget(button("保存到用户规则集", "save", "Primary", self._save_rules))
        lay.addWidget(self._wrap(top))

        checks_card = Card("检查开关", "关闭后该检查不参与分析")
        self.check_boxes: Dict[str, QCheckBox] = {}
        grid = QHBoxLayout()
        grid.setSpacing(14)
        col1 = QVBoxLayout()
        col2 = QVBoxLayout()
        grid.addLayout(col1, 1)
        grid.addLayout(col2, 1)
        for i, spec in enumerate(registry.list_checks()):
            box = QCheckBox(f"{spec.name}  ({spec.id})")
            box.setChecked(True)
            self.check_boxes[spec.id] = box
            (col1 if i % 2 == 0 else col2).addWidget(box)
        col1.addStretch(1)
        col2.addStretch(1)
        checks_card.add_layout(self._wrap(grid))
        lay.addWidget(checks_card)

        override_card = Card("规则阈值与严重度覆盖",
                             "按规则名匹配 (与问题清单「规则」列一致); min/max 会覆盖能力库数值")
        self.ov_table = QTableWidget(0, 6)
        self.ov_table.setHorizontalHeaderLabels(
            ["规则名", "启用", "严重度", "阈值 min", "阈值 max", "建议文案"])
        self.ov_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.ov_table.horizontalHeader().setStretchLastSection(True)
        self.ov_table.setColumnWidth(0, 190)
        self.ov_table.setColumnWidth(1, 60)
        self.ov_table.setColumnWidth(2, 100)
        self.ov_table.setColumnWidth(3, 90)
        self.ov_table.setColumnWidth(4, 90)
        self.ov_table.verticalHeader().setVisible(False)
        override_card.add_widget(self.ov_table, 1)
        obar = QHBoxLayout()
        obar.setSpacing(8)
        obar.addWidget(button("新增覆盖", "add", "", self._add_override))
        obar.addWidget(button("删除选中", "trash", "Ghost", self._del_override))
        obar.addWidget(button("从当前结果导入规则名", "results", "", self._import_rule_names))
        obar.addStretch(1)
        override_card.add_layout(self._wrap(obar))
        lay.addWidget(override_card, 1)
        return host

    # ---------------- Tab 3: 自定义规则 ----------------
    def _build_custom_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(muted(
            "声明式规则: 对设计参数做比较即可新增判定, 无需写代码。"
            "运算符: ge/le/gt/lt/eq/ne/between/outside/in/not_in/missing/false/true。"
            "参数名见「数据源 → 解析预览」中量测到的设计参数。"))
        self.cu_table = QTableWidget(0, 9)
        self.cu_table.setHorizontalHeaderLabels(
            ["启用", "代码", "规则名", "类别", "参数", "运算符", "目标值", "严重度", "描述/建议"])
        self.cu_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.cu_table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((56, 90, 170, 90, 170, 90, 110, 90)):
            self.cu_table.setColumnWidth(i, w)
        self.cu_table.verticalHeader().setVisible(False)
        lay.addWidget(self.cu_table, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("新增规则", "add", "", self._add_custom))
        bar.addWidget(button("删除选中", "trash", "Ghost", self._del_custom))
        bar.addWidget(button("示例: 板厚必须 1.6mm", "info", "", self._add_custom_example))
        bar.addStretch(1)
        bar.addWidget(button("保存到用户规则集", "save", "Primary", self._save_rules))
        lay.addWidget(self._wrap(bar))
        return host

    # ---------------- Tab 4: 插件 ----------------
    def _build_plugin_tab(self) -> QWidget:
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(14, 14, 14, 14)
        lay.setSpacing(10)
        lay.addWidget(muted(
            "插件 = 一个 Python 文件, 用 @register_check 注册检查函数即可参与分析。"
            "放入下列任一目录后点「重新加载插件」; 详见 docs/04-二次开发指南.md。"))
        self.plugin_dirs = QLabel("")
        self.plugin_dirs.setStyleSheet(f"font-family:Consolas,monospace;"
                                       f"color:{colors()['fg_dim']};")
        self.plugin_dirs.setTextInteractionFlags(Qt.TextSelectableByMouse)
        self.plugin_dirs.setWordWrap(True)
        lay.addWidget(self.plugin_dirs)

        self.check_table = QTableWidget(0, 6)
        self.check_table.setHorizontalHeaderLabels(
            ["检查 id", "名称", "分组", "依赖数据", "顺序", "来源"])
        self.check_table.horizontalHeader().setSectionResizeMode(QHeaderView.Interactive)
        self.check_table.horizontalHeader().setStretchLastSection(True)
        for i, w in enumerate((150, 190, 110, 170, 70)):
            self.check_table.setColumnWidth(i, w)
        self.check_table.verticalHeader().setVisible(False)
        self.check_table.setEditTriggers(QAbstractItemView.NoEditTriggers)
        lay.addWidget(self.check_table, 1)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("重新加载插件", "refresh", "Primary", self._reload_plugins))
        bar.addWidget(button("打开插件目录", "folder", "", self._open_plugin_dir))
        bar.addWidget(button("生成插件示例", "add", "", self._make_plugin_sample))
        bar.addStretch(1)
        lay.addWidget(self._wrap(bar))

        self.plugin_log = LogView()
        self.plugin_log.setMaximumHeight(120)
        lay.addWidget(self.plugin_log)
        return host

    def _wrap(self, layout) -> QWidget:
        host = QWidget()
        host.setLayout(layout)
        layout.setContentsMargins(0, 0, 0, 0)
        return host

    # ---------------- 刷新 ----------------
    def refresh(self) -> None:
        self._refresh_capabilities()
        self._refresh_rules()
        self._refresh_custom()
        self._refresh_plugins()

    def _refresh_capabilities(self) -> None:
        labels = factory_labels()
        factories = list_factories()
        self.cap_table.setRowCount(len(factories))
        for r, fid in enumerate(factories):
            path = capability_file(fid) or ""
            user_copy = "用户" if paths.user_data_dir(create=False) in path else "出厂"
            try:
                types = list_board_types(fid)
                levels = "; ".join(f"{bt}: {','.join(list_levels(bt, factory=fid))}"
                                   for bt in types)
            except Exception:  # noqa: BLE001
                types, levels = [], ""
            for c, val in enumerate((fid, labels.get(fid, fid), ", ".join(types),
                                     levels, path, user_copy)):
                item = QTableWidgetItem(str(val))
                if c == 4:
                    item.setToolTip(str(val))
                self.cap_table.setItem(r, c, item)

    def _refresh_rules(self) -> None:
        rules = self.state.rules
        self.rules_path_label.setText(f"规则集: {rules.source or '(内置默认 — 未找到 checks.yaml)'}")
        registry.load_builtin_checks()
        for cid, box in self.check_boxes.items():
            box.blockSignals(True)
            box.setChecked(rules.check_enabled(cid))
            box.blockSignals(False)
        overrides = rules.data.get("rules") or {}
        self.ov_table.setRowCount(len(overrides))
        for r, (name, cfg) in enumerate(sorted(overrides.items())):
            cfg = cfg or {}
            self.ov_table.setItem(r, 0, QTableWidgetItem(str(name)))
            chk = QCheckBox()
            chk.setChecked(bool(cfg.get("enabled", True)))
            cell = QWidget()
            hl = QHBoxLayout(cell)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignCenter)
            hl.addWidget(chk)
            self.ov_table.setCellWidget(r, 1, cell)
            sev = QComboBox()
            sev.addItem("", None)
            for s in SEVERITY_LEVELS:
                sev.addItem(s, s)
            i = sev.findData(cfg.get("severity"))
            sev.setCurrentIndex(i if i >= 0 else 0)
            self.ov_table.setCellWidget(r, 2, sev)
            self.ov_table.setItem(r, 3, QTableWidgetItem(str(cfg.get("min", ""))))
            self.ov_table.setItem(r, 4, QTableWidgetItem(str(cfg.get("max", ""))))
            self.ov_table.setItem(r, 5, QTableWidgetItem(str(cfg.get("suggestion", ""))))

    def _refresh_custom(self) -> None:
        customs = self.state.rules.data.get("custom_rules") or []
        self.cu_table.setRowCount(len(customs))
        for r, rule in enumerate(customs):
            chk = QCheckBox()
            chk.setChecked(bool(rule.get("enabled", True)))
            cell = QWidget()
            hl = QHBoxLayout(cell)
            hl.setContentsMargins(0, 0, 0, 0)
            hl.setAlignment(Qt.AlignCenter)
            hl.addWidget(chk)
            self.cu_table.setCellWidget(r, 0, cell)
            for c, key in enumerate((("id", 1), ("name", 2), ("category", 3), ("param", 4),
                                     ("op", 5), ("value", 6), ("severity", 7))):
                k, col = key
                self.cu_table.setItem(r, col, QTableWidgetItem(str(rule.get(k, ""))))
            msg = rule.get("message") or ""
            sug = rule.get("suggestion") or ""
            self.cu_table.setItem(r, 8, QTableWidgetItem(
                (msg + ("  ||  " + sug if sug else ""))))

    def _refresh_plugins(self) -> None:
        registry.load_builtin_checks()
        reports = registry.load_plugins()
        self.plugin_dirs.setText("插件目录: " + "\n            ".join(paths.plugins_dirs()))
        specs = registry.list_checks()
        self.check_table.setRowCount(len(specs))
        for r, s in enumerate(specs):
            src = "内置" if s.source == "builtin" else os.path.basename(s.source)
            for c, val in enumerate((s.id, s.name, s.group, ", ".join(s.requires) or "—",
                                     str(s.order), src)):
                self.check_table.setItem(r, c, QTableWidgetItem(val))
        self.plugin_log.clear_log()
        if not reports:
            self.plugin_log.write("(未发现插件文件)")
        for p in reports:
            level = "ok" if p["status"] == "ok" else ("err" if p["status"] == "error" else "dim")
            text = f"[{p['status']}] {p['name']} -> {', '.join(p['checks']) or '(无检查)'}"
            if p.get("error"):
                text += f"  ERROR: {p['error']}"
            self.plugin_log.write(text, level)

    # ---------------- 动作 ----------------
    def _open_capability(self) -> None:
        row = self.cap_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一家板厂")
            return
        fid = self.cap_table.item(row, 0).text()
        path = capability_file(fid)
        if path:
            os.startfile(path)  # noqa: S606

    def _open_capability_dir(self) -> None:
        dirs = paths.capability_dirs()
        target = dirs[0] if dirs else paths.app_root()
        os.makedirs(target, exist_ok=True)
        os.startfile(target)  # noqa: S606

    def _open_rules_dir(self) -> None:
        target = os.path.join(paths.user_data_dir(), "rules")
        os.makedirs(target, exist_ok=True)
        os.startfile(target)  # noqa: S606

    def _open_plugin_dir(self) -> None:
        target = os.path.join(paths.user_data_dir(), "plugins")
        os.makedirs(target, exist_ok=True)
        os.startfile(target)  # noqa: S606

    def _copy_capability(self) -> None:
        row = self.cap_table.currentRow()
        if row < 0:
            QMessageBox.information(self, "提示", "请先选择一家板厂")
            return
        fid = self.cap_table.item(row, 0).text()
        src = capability_file(fid)
        if not src:
            return
        dst_dir = os.path.join(paths.user_data_dir(), "rules", "capability")
        os.makedirs(dst_dir, exist_ok=True)
        dst = os.path.join(dst_dir, os.path.basename(src))
        if os.path.exists(dst):
            if not QMessageBox.question(self, "覆盖", f"{dst} 已存在, 覆盖?"):
                return
        import shutil

        shutil.copyfile(src, dst)
        QMessageBox.information(self, "已复制", f"用户副本 (优先生效):\n{dst}\n\n"
                                              "直接编辑该文件即可自定义板厂能力。")
        self._reload()

    def _export_template(self) -> None:
        path, _ = QFileDialog.getSaveFileName(self, "导出规则集模板",
                                              default_config_path(), "YAML (*.yaml)")
        if not path:
            return
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            f.write(template_text())
        QMessageBox.information(self, "已导出", path)

    def _collect_overrides(self) -> Dict[str, dict]:
        out: Dict[str, dict] = {}
        for r in range(self.ov_table.rowCount()):
            name_item = self.ov_table.item(r, 0)
            if not name_item or not name_item.text().strip():
                continue
            name = name_item.text().strip()
            cell = self.ov_table.cellWidget(r, 1)
            chk = cell.findChild(QCheckBox) if cell else None
            sev_box = self.ov_table.cellWidget(r, 2)
            entry: Dict[str, Any] = {"enabled": bool(chk.isChecked()) if chk else True}
            sev = sev_box.currentData() if sev_box else None
            if sev:
                entry["severity"] = sev
            for col, key in ((3, "min"), (4, "max")):
                item = self.ov_table.item(r, col)
                if item and item.text().strip():
                    try:
                        entry[key] = float(item.text().strip())
                    except ValueError:
                        pass
            sug = self.ov_table.item(r, 5)
            if sug and sug.text().strip():
                entry["suggestion"] = sug.text().strip()
            out[name] = entry
        return out

    def _collect_custom(self) -> List[dict]:
        out: List[dict] = []
        for r in range(self.cu_table.rowCount()):
            cell = self.cu_table.cellWidget(r, 0)
            chk = cell.findChild(QCheckBox) if cell else None
            get = lambda c: (self.cu_table.item(r, c).text().strip()  # noqa: E731
                             if self.cu_table.item(r, c) else "")
            param = get(4)
            if not param:
                continue
            rule = {
                "enabled": bool(chk.isChecked()) if chk else True,
                "id": get(1) or f"CUS-{r + 1:02d}",
                "name": get(2) or param,
                "category": get(3) or "自定义",
                "param": param,
                "op": get(5) or "ge",
                "severity": get(7) or "中",
            }
            value = get(6)
            rule["value"] = self._coerce_value(value, rule["op"])
            text = get(8)
            if "||" in text:
                msg, sug = text.split("||", 1)
                rule["message"] = msg.strip()
                rule["suggestion"] = sug.strip()
            elif text:
                rule["message"] = text
            out.append(rule)
        return out

    @staticmethod
    def _coerce_value(text: str, op: str) -> Any:
        text = (text or "").strip()
        if op in ("between", "outside", "in", "not_in"):
            parts = [p.strip() for p in text.replace("[", "").replace("]", "").split(",") if p.strip()]
            out = []
            for p in parts:
                try:
                    out.append(float(p))
                except ValueError:
                    out.append(p)
            return out
        if text.lower() in ("true", "false"):
            return text.lower() == "true"
        try:
            return float(text)
        except ValueError:
            return text

    def _add_override(self) -> None:
        r = self.ov_table.rowCount()
        self.ov_table.insertRow(r)
        self.ov_table.setItem(r, 0, QTableWidgetItem(""))
        chk = QCheckBox()
        chk.setChecked(True)
        cell = QWidget()
        hl = QHBoxLayout(cell)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignCenter)
        hl.addWidget(chk)
        self.ov_table.setCellWidget(r, 1, cell)
        sev = QComboBox()
        sev.addItem("", None)
        for s in SEVERITY_LEVELS:
            sev.addItem(s, s)
        self.ov_table.setCellWidget(r, 2, sev)
        for c in (3, 4, 5):
            self.ov_table.setItem(r, c, QTableWidgetItem(""))
        self.ov_table.editItem(self.ov_table.item(r, 0))

    def _del_override(self) -> None:
        rows = sorted({i.row() for i in self.ov_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.ov_table.removeRow(r)

    def _import_rule_names(self) -> None:
        issues = self.state.issues
        if not issues:
            QMessageBox.information(self, "提示", "请先执行一次分析, 再从结果导入规则名")
            return
        existing = {self.ov_table.item(r, 0).text()
                    for r in range(self.ov_table.rowCount()) if self.ov_table.item(r, 0)}
        added = 0
        for it in issues:
            name = str(it.get("rule") or "")
            if not name or name in existing:
                continue
            existing.add(name)
            r = self.ov_table.rowCount()
            self._add_override()
            self.ov_table.setItem(r, 0, QTableWidgetItem(name))
            sev = self.ov_table.cellWidget(r, 2)
            i = sev.findData(it.get("severity"))
            if i >= 0:
                sev.setCurrentIndex(i)
            added += 1
        QMessageBox.information(self, "导入完成", f"已导入 {added} 个规则名")

    def _add_custom(self) -> None:
        r = self.cu_table.rowCount()
        self.cu_table.insertRow(r)
        chk = QCheckBox()
        chk.setChecked(True)
        cell = QWidget()
        hl = QHBoxLayout(cell)
        hl.setContentsMargins(0, 0, 0, 0)
        hl.setAlignment(Qt.AlignCenter)
        hl.addWidget(chk)
        self.cu_table.setCellWidget(r, 0, cell)
        defaults = [f"CUS-{r + 1:02d}", "新规则", "线路", "min_line_width", "ge", "0.1", "中",
                    "线宽 {value}mm 低于客户要求 {target}mm  ||  请加宽线路"]
        for c, val in enumerate(defaults, start=1):
            self.cu_table.setItem(r, c, QTableWidgetItem(str(val)))

    def _add_custom_example(self) -> None:
        r = self.cu_table.rowCount()
        self._add_custom()
        for c, val in enumerate(["", "CUS-TH", "板厚必须为 1.6mm", "板厚", "board_thickness",
                                 "eq", "1.6", "高",
                                 "板厚 {value}mm 不符合客户标准 1.6mm  ||  修改叠层板厚为 1.6mm"]):
            if c:
                self.cu_table.setItem(r, c, QTableWidgetItem(val))

    def _del_custom(self) -> None:
        rows = sorted({i.row() for i in self.cu_table.selectedIndexes()}, reverse=True)
        for r in rows:
            self.cu_table.removeRow(r)

    def _save_rules(self) -> None:
        data = self.state.rules.data
        data.setdefault("checks", {})
        for cid, box in self.check_boxes.items():
            data["checks"].setdefault(cid, {})["enabled"] = box.isChecked()
        data["rules"] = self._collect_overrides()
        data["custom_rules"] = self._collect_custom()
        target = self.state.rules.source
        if not target or paths.user_data_dir(create=False) not in target:
            target = default_config_path()
        try:
            self.state.rules.save(target)
        except Exception as exc:  # noqa: BLE001
            QMessageBox.critical(self, "保存失败", str(exc))
            return
        self.state.apply_rules_path(target)
        QMessageBox.information(self, "已保存", f"规则集已保存:\n{target}\n\n分析时将自动生效。")

    def _reload(self) -> None:
        self.state.reload_rules()
        self.refresh()
        self.set_status("规则库已重新加载")

    def _reload_plugins(self) -> None:
        registry.load_builtin_checks(force=True)
        registry.load_plugins(force=True)
        self._refresh_plugins()
        self.set_status("插件已重新加载")

    def _make_plugin_sample(self) -> None:
        target_dir = os.path.join(paths.user_data_dir(), "plugins")
        os.makedirs(target_dir, exist_ok=True)
        target = os.path.join(target_dir, "sample_check.py")
        sample = '''"""示例插件: 统计铜层数量并给出提示。

放到 plugins 目录即可被自动加载; 用 @register_check 注册检查函数,
函数接收 CheckContext, 返回 Issue 列表 (可为空)。
"""
from dfm.core.issue import make_issue
from dfm.core.registry import REQ_DESIGN, register_check


@register_check(id="sample_layer_count", name="铜层数提示", group="制造 DFM",
                description="示例插件: 铜层数超过 12 层时提示压合风险",
                requires=(REQ_DESIGN,), order=90)
def run(ctx):
    layers = ctx.design.copper_layers()
    if len(layers) > 12:
        return [make_issue(code="SMP-01", category="压合", rule="铜层数过多",
                           severity="中",
                           description=f"铜层数 {len(layers)} 层, 压合对位与翘曲风险上升",
                           measured=len(layers), required=12, unit="层",
                           suggestion="与板厂确认层压结构、对位方式与板厚公差")]
    return []
'''
        if os.path.exists(target):
            if not QMessageBox.question(self, "覆盖", f"{target} 已存在, 覆盖?"):
                return
        with open(target, "w", encoding="utf-8") as f:
            f.write(sample)
        QMessageBox.information(self, "已生成", f"示例插件:\n{target}\n\n点「重新加载插件」查看效果。")
        self._reload_plugins()

    def on_show(self) -> None:
        self.refresh()

    def on_rules_changed(self) -> None:
        self.refresh()
