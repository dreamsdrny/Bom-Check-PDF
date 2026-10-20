"""数据源页: 选择输入数据与板厂/工艺配置, 并可先「解析预览」确认数据可用。"""
from __future__ import annotations

import json
import os
from typing import Any, Dict, List, Optional

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (QDoubleSpinBox, QFileDialog, QFormLayout, QGridLayout,
                               QHBoxLayout, QLabel, QLineEdit, QMessageBox, QScrollArea,
                               QSpinBox, QFrame, QVBoxLayout, QWidget)

from dfm.core.capability import (capability_file, factory_labels, list_board_types,
                                 list_factories, list_levels)
from dfm.core.source import SOURCE_LABELS, SOURCE_TYPES, detect_source_type
from dfm.ui.pages.base import BasePage
from dfm.ui.theme import colors
from dfm.ui.widgets.common import (Card, LogView, PathRow, button, combo, muted,
                                   safe_float)
from dfm.ui.workers import TaskWorker

TYPE_ITEMS = [("自动识别", "")] + [(SOURCE_LABELS[t], t) for t in SOURCE_TYPES]


class ProjectPage(BasePage):
    page_id = "project"
    title = "数据源"
    subtitle = "选择 BRD / Gerber / ODB++ / 参数, 并设置板厂与工艺条件"
    icon = "project"
    group = "分析"
    order = 20

    def build(self) -> None:
        scroll = QScrollArea()
        scroll.setWidgetResizable(True)
        scroll.setFrameShape(QFrame.NoFrame)
        host = QWidget()
        lay = QVBoxLayout(host)
        lay.setContentsMargins(0, 0, 8, 0)
        lay.setSpacing(14)
        scroll.setWidget(host)
        self.root.addWidget(scroll, 1)

        lay.addWidget(self._build_source_card())
        row = QHBoxLayout()
        row.setSpacing(14)
        row.setAlignment(Qt.AlignTop)
        lay.addLayout(row)
        row.addWidget(self._build_board_card(), 3)
        row.addWidget(self._build_env_card(), 2)
        lay.addWidget(self._build_preview_card(), 1)
        lay.addStretch(1)

        self._load_from_state()

    # ---------------- 卡片 ----------------
    def _build_source_card(self) -> Card:
        card = Card("① 数据源", "支持 Allegro .brd、SKILL/extracta 导出 JSON、Gerber+NC 钻孔、ODB++ 工程")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.type_box = combo(TYPE_ITEMS, "")
        self.type_box.currentIndexChanged.connect(self._on_type_changed)
        grid.addWidget(QLabel("数据源类型"), 0, 0)
        grid.addWidget(self.type_box, 0, 1)
        tip = QLabel("选择文件/目录后会自动识别类型")
        tip.setObjectName("Muted")
        grid.addWidget(tip, 0, 2)

        self.src_row = PathRow("主数据源", "file", ".brd / .json / Gerber 文件",
                               "Cadence (*.brd);;JSON (*.json);;Gerber/钻孔 (*.gbr *.ger *.art *.pho *.drl);;所有文件 (*.*)")
        self.src_row.edit.textChanged.connect(self._on_source_text)
        self.src_dir_btn = button("目录...", "folder", "", self._pick_dir)
        grid.addWidget(self.src_row, 1, 0, 1, 2)
        grid.addWidget(self.src_dir_btn, 1, 2, Qt.AlignLeft)

        self.fab_row = PathRow("制造数据", "dir", "Gerber / ODB++ / 钻孔 目录 (可选)")
        grid.addWidget(self.fab_row, 2, 0, 1, 2)
        grid.addWidget(button("选文件", "file", "", self._pick_fab_files), 2, 2, Qt.AlignLeft)

        self.cad_row = PathRow("CAD 网表", "file", 'JSON: {"NET":["R1.1",...]} (用于 CAD vs FAB 比对)',
                               "JSON (*.json);;所有文件 (*.*)")
        grid.addWidget(self.cad_row, 3, 0, 1, 3)

        self.params_row = PathRow("设计参数", "file", "制程能力比对参数 JSON (可选, 缺省自动量测)",
                                  "JSON (*.json);;所有文件 (*.*)")
        grid.addWidget(self.params_row, 4, 0, 1, 2)
        grid.addWidget(button("生成模板", "save", "", self._make_params_template), 4, 2,
                     Qt.AlignLeft)

        grid.setColumnStretch(1, 1)
        card.add_layout(grid)

        bar = QHBoxLayout()
        bar.setSpacing(8)
        bar.addWidget(button("解析并预览", "search", "Primary", self._preview))
        bar.addWidget(button("清空", "trash", "Ghost", self._clear_all))
        bar.addStretch(1)
        self.detect_label = QLabel("")
        self.detect_label.setObjectName("Muted")
        bar.addWidget(self.detect_label)
        card.add_layout(bar)
        return card

    def _build_board_card(self) -> Card:
        card = Card("② 板厂与工艺条件", "决定判定标准 (制程能力库 + 能力等级 + 铜厚)")
        grid = QGridLayout()
        grid.setHorizontalSpacing(10)
        grid.setVerticalSpacing(8)

        self.factory_box = combo([])
        self.factory_box.currentIndexChanged.connect(self._on_factory_changed)
        self.type_board_box = combo([("硬板", "rigid"), ("软板/软硬结合", "flex")], "rigid")
        self.type_board_box.currentIndexChanged.connect(self._on_board_type_changed)
        self.level_box = combo([])

        grid.addWidget(QLabel("板厂"), 0, 0)
        grid.addWidget(self.factory_box, 0, 1)
        grid.addWidget(QLabel("板型"), 0, 2)
        grid.addWidget(self.type_board_box, 0, 3)
        grid.addWidget(QLabel("能力等级"), 1, 0)
        grid.addWidget(self.level_box, 1, 1)

        self.copper_um = QDoubleSpinBox()
        self.copper_um.setRange(1.0, 400.0)
        self.copper_um.setDecimals(1)
        self.copper_um.setValue(35.0)
        self.copper_um.setSuffix(" um")
        self.copper_oz = QDoubleSpinBox()
        self.copper_oz.setRange(0.0, 20.0)
        self.copper_oz.setDecimals(2)
        self.copper_oz.setSpecialValueText("自动")
        self.copper_oz.setValue(0.0)
        self.copper_oz.setSuffix(" oz")
        self.thickness = QDoubleSpinBox()
        self.thickness.setRange(0.0, 20.0)
        self.thickness.setDecimals(3)
        self.thickness.setSpecialValueText("未填写")
        self.thickness.setValue(0.0)
        self.thickness.setSuffix(" mm")

        grid.addWidget(QLabel("成品铜厚"), 1, 2)
        cu = QHBoxLayout()
        cu.addWidget(self.copper_um)
        cu.addWidget(self.copper_oz)
        grid.addLayout(cu, 1, 3)
        grid.addWidget(QLabel("板厚"), 2, 0)
        grid.addWidget(self.thickness, 2, 1)
        hint = QLabel("Gerber/ODB++ 无板厚信息, 填写后才能判 PTH 纵横比")
        hint.setObjectName("Muted")
        hint.setWordWrap(True)
        grid.addWidget(hint, 2, 2, 1, 2)

        self.customer = QLineEdit()
        self.customer.setPlaceholderText("报告封面显示")
        self.submitter = QLineEdit()
        self.submitter.setPlaceholderText("检查人 / 提交人")
        grid.addWidget(QLabel("客户"), 3, 0)
        grid.addWidget(self.customer, 3, 1)
        grid.addWidget(QLabel("提交人"), 3, 2)
        grid.addWidget(self.submitter, 3, 3)

        card.add_layout(grid)
        bar = QHBoxLayout()
        bar.addSpacing(2)
        self.rules_label = QLabel("")
        self.rules_label.setObjectName("Muted")
        bar.addWidget(self.rules_label, 1)
        bar.addWidget(button("规则库...", "sliders", "", lambda: self.navigate("rules")))
        card.add_layout(bar)
        for w in (self.copper_um, self.copper_oz, self.thickness, self.customer,
                  self.submitter):
            self._connect_change(w)
        return card

    def _build_env_card(self) -> Card:
        card = Card("③ 环境", "仅在数据源为 Allegro .brd 时需要")
        self.extracta_row = PathRow("extracta.exe", "file", "留空则自动在 PATH / Cadence 安装目录中查找",
                                    "可执行文件 (*.exe);;所有文件 (*.*)")
        card.add_widget(self.extracta_row)
        bar = QHBoxLayout()
        bar.addWidget(button("自动探测", "search", "", self._detect_extracta))
        bar.addWidget(button("导出 SKILL 脚本", "export", "", self._skill_export))
        bar.addStretch(1)
        card.add_layout(bar)
        self.extracta_row.edit.textChanged.connect(self._on_change)
        card.add_widget(muted(
            "无 extracta 时: 在 Allegro 中加载 SKILL 脚本导出 JSON, 再把 JSON 作为主数据源。"))
        return card

    def _build_preview_card(self) -> Card:
        card = Card("解析预览", "确认层/网络/器件与量测参数是否符合预期")
        self.preview_log = LogView()
        self.preview_log.setMinimumHeight(180)
        card.add_widget(self.preview_log, 1)
        return card

    # ---------------- 交互 ----------------
    def _connect_change(self, widget) -> None:
        if hasattr(widget, "textChanged"):
            widget.textChanged.connect(self._on_change)
        elif hasattr(widget, "valueChanged"):
            widget.valueChanged.connect(self._on_change)
        elif hasattr(widget, "currentIndexChanged"):
            widget.currentIndexChanged.connect(self._on_change)

    def _on_change(self, *_args) -> None:
        self._write_to_state()

    def _on_source_text(self, text: str) -> None:
        text = text.strip()
        if text and self.type_box.currentData() == "":
            kind = detect_source_type(text)
            self.detect_label.setText(f"识别为: {SOURCE_LABELS.get(kind, kind)}")
        self._on_change()

    def _on_type_changed(self, *_args) -> None:
        kind = self.type_box.currentData() or ""
        self.src_row.mode = "dir" if kind in ("gerber", "odb") else "file"
        self._on_change()

    def _pick_dir(self) -> None:
        p = QFileDialog.getExistingDirectory(self, "选择 Gerber / ODB++ 目录",
                                             self.src_row.text())
        if p:
            self.src_row.set_text(p.replace("\\", "/"))
            kind = detect_source_type(p)
            i = self.type_box.findData(kind)
            if i >= 0:
                self.type_box.setCurrentIndex(i)

    def _pick_fab_files(self) -> None:
        paths, _ = QFileDialog.getOpenFileNames(
            self, "选择制造数据文件", self.fab_row.text(),
            "Gerber/钻孔 (*.gbr *.ger *.art *.pho *.drl *.gto *.gbo);;所有文件 (*.*)")
        if paths:
            self.fab_row.set_text(paths[0].replace("\\", "/") if len(paths) == 1
                                  else os.path.commonpath(paths).replace("\\", "/"))

    def _make_params_template(self) -> None:
        tpl = {
            "board_name": "DEMO-BOARD",
            "board_size": [100, 80],
            "board_thickness": 1.6,
            "layer_count": 8,
            "min_line_width": 0.10,
            "min_spacing": 0.10,
            "inner_line_width": 0.10,
            "inner_spacing": 0.12,
            "copper_um": 35,
            "copper_oz": 1.0,
            "min_mech_drill": 0.2,
            "max_mech_drill": 3.2,
            "min_laser_via": 0.1,
            "min_annular_ring": 0.075,
            "min_annular_ring_laser": 0.05,
            "pth_to_pth_spacing": 0.3,
            "copper_to_edge_inner": 0.25,
            "copper_to_edge_outer": 0.2,
            "npth_to_edge": 0.5,
            "solder_mask_bridge": 0.1,
            "solder_mask_opening": 0.1,
            "solder_mask_ink_um": 20,
            "silkscreen_width": 0.15,
            "silkscreen_clearance": 0.15,
            "silkscreen_to_mask": 0.1,
            "silkscreen_to_outline": 0.5,
            "plug_hole_diameter": 0.3,
            "resin_plug_depth_percent": 80,
            "test_pad_2wire": 0.4,
            "test_pad_diameter": 0.8,
            "surface_finish": "ENIG",
            "min_component_spacing": 0.5,
            "component_to_edge": 2.0,
            "fiducial_count_top": 3,
            "fiducial_count_bottom": 3,
            "test_pad_to_edge": 1.27,
            "chip_pad_symmetry_ratio": 0.9,
            "bga_pitch": 0.5,
            "polarity_marked": True,
            "panelized": True,
            "process_edge_mm": 5,
            "mark_count": 4,
            "mark_to_edge_mm": 4,
            "tooling_hole_count": 4,
            "panel_utilization": 0.82,
            "impedance": True,
        }
        path, _ = QFileDialog.getSaveFileName(self, "保存设计参数模板",
                                              os.path.join(self.state.output_dir(),
                                                           "dfm_design_params.json"),
                                              "JSON (*.json)")
        if not path:
            return
        with open(path, "w", encoding="utf-8") as f:
            json.dump(tpl, f, indent=2, ensure_ascii=False)
        self.params_row.set_text(path.replace("\\", "/"))
        QMessageBox.information(self, "已生成", f"设计参数模板:\n{path}")

    def _detect_extracta(self) -> None:
        from dfm.io.extracta import find_extracta

        path = find_extracta()
        if path:
            self.extracta_row.set_text(path.replace("\\", "/"))
            QMessageBox.information(self, "已找到", path)
        else:
            QMessageBox.warning(self, "未找到",
                                "未在 PATH 与常见 Cadence 安装目录中找到 extracta.exe。\n"
                                "可手动指定路径, 或改用 SKILL 导出 JSON 的方式。")

    def _skill_export(self) -> None:
        from dfm.io.skill_export import EXPORT_SKILL_FILENAME, write_export_skill

        out = os.path.join(self.state.output_dir(), EXPORT_SKILL_FILENAME)
        path = write_export_skill(out)
        QMessageBox.information(
            self, "SKILL 脚本已写出",
            f"{path}\n\n用法 (在 Allegro PCB Editor 的 Command 窗口):\n"
            f'  skill load "{os.path.basename(path)}"\n'
            "  brdExportJSON\n\n"
            "导出的 JSON 可直接作为本软件的「主数据源」。")

    def _clear_all(self) -> None:
        for row in (self.src_row, self.fab_row, self.cad_row, self.params_row):
            row.set_text("")
        self.preview_log.clear_log()
        self.detect_label.setText("")
        self._on_change()

    # ---------------- 解析预览 ----------------
    def _preview(self) -> None:
        cfg = self._collect_cfg()
        if not cfg.get("source_path"):
            QMessageBox.warning(self, "提示", "请先选择主数据源")
            return
        self.preview_log.clear_log()
        self.preview_log.write("解析中 ...", "info")
        self.set_status("正在解析数据源 ...")

        def job(progress):
            from dfm.core.source import load_source

            return load_source(cfg, progress=progress)

        self._worker = TaskWorker(job, self.preview_log.write)
        self._worker.finished_.connect(self._preview_done)
        self._worker.failed.connect(self._preview_failed)
        self._worker.start()

    def _preview_done(self, src) -> None:
        self.preview_log.write("解析完成", "ok")
        for w in src.warnings:
            self.preview_log.write(f"[提示] {w}", "warn")
        self.preview_log.write(f"数据源类型: {src.kind}   板名: "
                               f"{src.board_meta.get('board_name', '')}", "info")
        man = (src.info_stats or {}).get("manufacturing") or {}
        asm = (src.info_stats or {}).get("assembly") or {}
        for k, v in man.items():
            self.preview_log.write(f"  {k}: {v}")
        for k, v in asm.items():
            self.preview_log.write(f"  {k}: {v}")
        gm = src.extras.get("gerber_meta") or {}
        if gm:
            self.preview_log.write("  Gerber 层识别:", "info")
            self.preview_log.write(f"    铜层: {', '.join(gm.get('copper_layers') or [])}")
            self.preview_log.write(f"    阻焊: {', '.join(gm.get('mask_layers') or [])}"
                                   f"   丝印: {', '.join(gm.get('silk_layers') or [])}")
            self.preview_log.write(f"    网络来源: "
                                   f"{'X2 属性' if gm.get('has_x2_net') else '几何连通性派生'}")
        self.preview_log.write("  量测到的设计参数:", "info")
        for k, v in sorted(src.params.items()):
            if not k.startswith("_"):
                self.preview_log.write(f"    {k} = {v}")
        if not src.has_design:
            self.preview_log.write("  (无几何模型: 将只做参数化检查)", "warn")
        self.set_status(f"解析完成 — {src.kind} {os.path.basename(src.path or '')}")

    def _preview_failed(self, msg: str) -> None:
        self.preview_log.write(msg, "err")
        self.set_status("解析失败")

    def _collect_cfg(self) -> Dict[str, Any]:
        cfg = dict(self.state.analysis_config())
        cfg["progress_sink"] = None
        return cfg

    # ---------------- 状态同步 ----------------
    def _write_to_state(self) -> None:
        self.state.set_config(
            source_type=self.type_box.currentData() or "",
            source_path=self.src_row.text(),
            fab_path=self.fab_row.text(),
            cad_nets=self.cad_row.text(),
            design_params=self.params_row.text(),
            factory=self.factory_box.currentData() or "hongban",
            board_type=self.type_board_box.currentData() or "rigid",
            capability_level=self.level_box.currentData() or "standard",
            copper_um=self.copper_um.value(),
            copper_oz=(self.copper_oz.value() or None),
            board_thickness=(self.thickness.value() or None),
            customer=self.customer.text().strip(),
            submitter=self.submitter.text().strip(),
            extracta=self.extracta_row.text(),
        )
        self._refresh_rules_label()

    def _refresh_rules_label(self) -> None:
        rules = self.state.rules
        cap = capability_file(self.state.config.get("factory") or "hongban") or ""
        d = rules.describe()
        self.rules_label.setText(
            f"能力库: {os.path.basename(cap) or '(未找到)'}   规则集: "
            f"{os.path.basename(rules.source) if rules.source else '内置默认'}   "
            f"覆盖 {d['rule_overrides']} 条 / 自定义 {d['custom_rules']} 条")

    def _load_from_state(self) -> None:
        cfg = self.state.config
        self._reload_factories()
        i = self.type_box.findData(cfg.get("source_type") or "")
        self.type_box.setCurrentIndex(max(0, i))
        self.src_row.set_text(cfg.get("source_path") or "")
        self.fab_row.set_text(cfg.get("fab_path") or "")
        self.cad_row.set_text(cfg.get("cad_nets") or "")
        self.params_row.set_text(cfg.get("design_params") or "")
        self.copper_um.setValue(float(cfg.get("copper_um") or 35))
        self.copper_oz.setValue(float(cfg.get("copper_oz") or 0))
        self.thickness.setValue(float(cfg.get("board_thickness") or 0))
        self.customer.setText(cfg.get("customer") or "")
        self.submitter.setText(cfg.get("submitter") or "")
        self.extracta_row.set_text(cfg.get("extracta") or "")
        self._reload_levels()
        self._refresh_rules_label()

    def _reload_factories(self) -> None:
        current = self.state.config.get("factory") or "hongban"
        labels = factory_labels()
        self.factory_box.blockSignals(True)
        self.factory_box.clear()
        for fid in list_factories():
            self.factory_box.addItem(f"{labels.get(fid, fid)}  ({fid})", fid)
        i = self.factory_box.findData(current)
        self.factory_box.setCurrentIndex(i if i >= 0 else 0)
        self.factory_box.blockSignals(False)

    def _on_factory_changed(self, *_args) -> None:
        self._reload_levels()
        self._on_change()

    def _on_board_type_changed(self, *_args) -> None:
        self._reload_levels()
        self._on_change()

    def _reload_levels(self) -> None:
        fid = self.factory_box.currentData() or "hongban"
        bt = self.type_board_box.currentData() or "rigid"
        current = self.state.config.get("capability_level") or "standard"
        levels = list_levels(bt, factory=fid) or ["standard"]
        self.level_box.blockSignals(True)
        self.level_box.clear()
        for lv in levels:
            from dfm.core.capability import level_label
            self.level_box.addItem(f"{level_label(bt, lv, factory=fid)}  ({lv})", lv)
        i = self.level_box.findData(current)
        self.level_box.setCurrentIndex(i if i >= 0 else 0)
        self.level_box.blockSignals(False)

    def on_show(self) -> None:
        self._reload_factories()
        self._reload_levels()
        self._refresh_rules_label()

    def on_rules_changed(self) -> None:
        self._reload_factories()
        self._reload_levels()
        self._refresh_rules_label()
