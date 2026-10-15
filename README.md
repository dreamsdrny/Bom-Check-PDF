# 程序框架 (分层)

```
入口层    evan_dfm.py(Evan_DFM) / asher_dfm.py(Asher_DFM) / main.py(CLI) / gui.py / layout_brd.py
品牌层    src/branding.py       程序名·标题·图标·AppUserModelID (多品牌: set_brand / DFM_BRAND)
界面层    ui_main.MainLauncher  ← 一级导航 (每张卡片 = 一个独立二级窗口)
            ├─ DFM 检查中心    src/dfm_hub_ui.DfmHubWindow   (二级 → 三级: 清单/统计/交叉定位/报告)
            ├─ 分析BRD-DFM     src/brd_dfm_ui.BrdDFMWindow   (①导出SKILL ②选源 ③分析 ④定位 ⑤报告)
            ├─ 分析BRD         gui.BRD_AIGUI                 (约束/Checklist/SKILL)
            ├─ 分析Gerber/ODB  ui_main.FabWindow
            ├─ 制程能力 DFM    gui.DFMDialog
            ├─ 排版设计        src/panel_ui.PanelDesigner     (PCS/ARRAY/PANEL 三图联动)
            └─ Gerber查看器    src/gerber_viewer.GerberViewer (多图层 + 叠加对比)
编排层    src/dfm_hub.run_hub   ← 多模块统一编排 (能力/几何/开短路/制造数据/网络比对/拼版)
          src/brd_dfm           ← BRD 专用编排 (extracta 或 SKILL JSON)
分析层    dfm_engine(能力库比对) dfm_geometry(几何DFM·定位XY) dfm_netlist(开短路/网表)
          fab_data_reader(Gerber/ODB++/NC Drill 解析·完整性·网络比对) panel_engine(拼版)
数据层    brd_extracta(Allegro extracta 无界面提取) / skill_export(.il) / brd_geometry(JSON→设计模型)
          dxf_reader(外形) / pdf_reader(参考底图)
输出层    dfm_report(Valor式 Excel/文本: Main·Issue summary·Info·Issue1..N)
          dfm_snap(违例截图) report_generator(约束报告) panel_export(PNG/PDF)
          fab_viewer / gerber_viewer(图形交叉定位)
规则模型  src/dfm_rules         统一 Issue: code·category·rule·severity·measured·required·x/y·suggestion
配置      config/checklist_rules.yaml
          config/dfm_capability_hongban.yaml | _sihui.yaml | _jlcpcb.yaml(嘉立创公开能力表)
          samples/brd_export_sample.json (无 Allegro 时可试跑)
```

DFM 数据流（两种源 → 同一 schema → 同一报告）:

```
直接: brd_extracta(extracta 无界面读 .brd) ┐
备选: skill_export(.il) →[Allegro 运行]    ┘→ brd_geometry(JSON)
        → dfm_geometry(几何 DFM, 定位XY) + dfm_netlist(开短路/网表)
        → dfm_hub / brd_dfm(编排) → dfm_report(Valor式) + fab_viewer(交叉定位)
制造数据: fab_data_reader(Gerber/ODB++/Drill) → 完整性 + CAD vs FAB 网络比对
参数化:  dfm_engine.analyze_design(设计参数 vs 板厂能力库)
拼版:    panel_engine(PCS/ARRAY/PANEL) → 拼版 DFM 问题
```

# DFM 检查中心（多级界面 + 多模块）

一级(首页卡片) → 二级(检查中心: 数据源 + 规则集 + 模块勾选 + 执行) → 三级(问题清单 / 统计与评分 / 交叉定位 / 报告导出)。

| 模块 | 说明 | 依赖 |
|---|---|---|
| capability | 制程能力 DFM：设计参数 vs 板厂能力库 | 设计参数 JSON |
| geometry | 几何 DFM：线宽/线距/孔环/孔径/纵横比/板边/阻焊桥/DRC，**定位 XY** | .brd(extracta) 或导出 JSON |
| netlist | 开短路 + 网表一致性 | 同上 |
| fab | 制造数据完整性（Gerber/ODB++/NC Drill） | 制造数据目录 |
| fabnet | CAD vs FAB 网络比对（Open/Short） | 制造数据 + CAD 网表 JSON |
| panel | 拼版/工艺边 DFM | 拼版参数 JSON |

命令行等价：

```powershell
# 参数化 + 拼版 (嘉立创能力库)
python main.py --dfm-hub --design-params design.json --panel-params panel.json --factory jlcpcb

# BRD 几何 + 开短路 + 网表 (extracta 或 SKILL 导出 JSON)
python main.py --dfm-hub --brd-dfm board.brd
python main.py --dfm-hub --brd-dfm samples\brd_export_sample.json

# 叠加制造数据与网络比对
python main.py --dfm-hub --brd-dfm board.brd --fab .\gerbers --cad-nets cad_nets.json

# 只跑指定模块
python main.py --dfm-hub --brd-dfm board.brd --hub-modules geometry,netlist
```

输出：`output/dfm_hub_report.xlsx`（Main / Issue summary / Info / Issue1..N）、`dfm_hub_report.txt`、`dfm_hub_analysis.json`。

板厂能力库：`hongban`(板厂A) / `sihui`(板厂B) / `jlcpcb`(嘉立创，来自其公开能力表：按铜厚分档线宽线距、PTH/NPTH 孔环、各类间距、阻焊桥按铜厚、丝印 0.15mm/字高 1.0mm、拼板 V-Cut/邮票孔/工艺边/Mark、HDI 盲埋孔、背钻、castellated 等)。

---

入口层   main.py / gui.py / ui_main.py / layout_brd.py / evan_dfm.py
          │
核心管线  pcb_reader → signal_classifier → rule_engine → dfm_engine → skill_generator → report_generator
          │                    checklist_reader / checklist_auto_fill
          │
DFM/制造  dfm_rules(基础Issue模型) ← dfm_report / fab_data_reader / fab_viewer / panelizer / panel_engine
           │
BRD-DFM   直接: brd_extracta(extracta无界面读.brd) ┐
          备选: skill_export(导出.il)→[Allegro运行] ┘→ brd_geometry(读JSON) → dfm_geometry(几何DFM定位XY) + dfm_netlist(开短路/网表) → brd_dfm(编排) → dfm_report(Valor式) / fab_viewer(交叉定位)
           │
拼版/查看  panel_engine + panel_ui + panel_export + panelizer + dxf_reader + pdf_reader
          │
配置      config/*.yaml (checklist_rules / dfm_capability_hongban / dfm_capability_sihui)


一、软件定位
项目打包出的 GUI 单机版，产物位于 dist\Layout-brd\。入口为 layout_brd.py，实际界面来自 gui.py:25 的 BRD_AIGUI，打包时额外套用了 evan.ico 图标并**移除了 DFM 功能分析按钮**（layout_brd.py:53）。

核心作用：把 PCB Layout Checklist 规则 + PCB 厂制程能力 自动转换成 Cadence Allegro 可执行的 SKILL 约束脚本，并顺带生成约束报告、DFM 校验报告和已自动填写的 Checklist。

二、运行方式
必须保留整个文件夹（含 _internal 目录），双击：

dist\Layout-brd\自动填写Check-List.exe
不要单独把 exe 拷出来运行。

三、界面各区域
1. Input Files（输入文件）

BRD File：Cadence Allegro .brd 文件
Checklist Excel：Layout Checklist 的 .xlsx
Output Dir：输出目录（默认 output/）
2. Options（选项）

Factory：板厂 hongban(板厂A) / sihui(板厂B)
Board Type：rigid(硬板) / flex(软板及软硬结合板)
Capability：随板厂、板型联动的可用等级（standard / automotive / extreme，advanced 自动映射为 extreme）
Copper(um) / (oz)：成品铜厚，影响线宽线距分档
Auto-fill Checklist：默认勾选，启用后填 Check 与 Approved 列
Checker Name：写入 Approved 列，默认 BRD-AI
Net List (JSON)：可选，用 JSON 网络名列表替代 BRD
3. Buttons（按钮）

Start Generation：需先选 BRD 或 Net List JSON
Demo Run：内置 Demo Board 演示数据，无需任何输入
Open Output Folder：打开输出目录
4. Log / Progress / Status：彩色日志与进度条。

四、运行流程（7 步）
gui.py:242 的后台流水线依次执行：

读取 PCB 数据（BRD / JSON / 演示）
信号自动分类（20 类，DDR/PCIE/USB/MIPI/LVDS/ETH/RF… + 差分对识别）
计算约束规则（线宽 / 间距 / 过孔 / 差分电气）
DFM 制程能力校验
生成 SKILL 脚本
生成 Excel / 文本 / JSON 报告
自动填写 Checklist（勾选且有 Checklist 时）

五、分析BRD-DFM（几何定位XY + 开短路 + 网表测试，参考 Valor）
Cadence .brd 为二进制专有格式，纯 Python 无法可靠解析几何坐标。本功能提供两种
数据来源，二者产出同一 JSON schema，后续分析/报告完全一致：

【方式A｜直接读 .brd（推荐，全自动无界面）】
本机装有 Allegro 时，用其自带命令行工具 extracta.exe **不打开 GUI** 直接提取
网表/几何/DRC/叠层（坐标自动 mils→mm）：
1. 首页『分析BRD-DFM』→『②』直接选择 .brd 文件，选板厂/板型/能力等级/铜厚
2. 点『③ 执行分析』（大板约 1-2 分钟，后台 extracta 提取 + DFM 分析）
3. 『④ 打开交叉定位』版图高亮每个 warning/fail 的 XY；『⑤ 导出报告』出 Valor 式报告
命令行等价：
   `python main.py --brd-dfm <board.brd> [--factory hongban] [--board-type rigid]
    [--capability standard] [--copper 35] [--fab <gerber/odb目录>] [--view]`
extracta 提取的视图：COMPONENT_PIN(网表含XY) / COMPOSITE_PAD(焊盘·过孔) /
LAYER(叠层板厚) / NET(平均线宽) / CONNECTIVITY(未连ratsnest=开路) / COMPONENT /
GEOMETRY(走线·板框) / DRC_ERROR(违规含XY) / PAD_DEF2(孔径·焊盘·镀通)。

【方式B｜SKILL 导出（无 extracta / 需更细逐段线宽时）】
1. 『① 导出SKILL脚本(.il)』或 `python main.py --skill-export`
2. Allegro 内打开 .brd，执行 `skill load "brd_dfm_export.il"` 再 `brdExportJSON`
3. 『②』选择导出的 JSON →『③ 执行分析』

检测项（均定位到 XY）：
- 几何 DFM：最小线宽、最小线距（不同网络铜箔）、单边孔环、最小机械孔/激光孔、
  最大孔、PTH 纵横比、线路到板边、最小阻焊桥、电测/测试点焊盘、Allegro DRC 透传
- 开短路测试：开路 Open（extracta 用 Allegro 连通性 ratsnest 权威判定；SKILL 用几何
  连通域派生）、短路 Short（不同网络铜箔接触；extracta 以 Allegro DRC 为准）
- 网表一致性：逻辑网表 vs 物理连通域；提供 --fab 时再做 CAD vs FAB 网络/节点比对

说明：extracta 的走线宽度取「网络平均线宽」，故方式A 的间距/短路以焊盘·过孔真实
尺寸 + Allegro DRC 为准（避免宽电源走线按平均宽膨胀的误报）；方式B（SKILL）逐段
线宽为实测值，几何间距/短路更细。孔环/孔径/纵横比两种方式的孔径焊盘均为实测值。

报告（output/brd_dfm_report.xlsx）工作表：Main / Issue summary（含 网络·X位置·Y位置）
/ Info / 开短路网表 / Issue1..N（逐条定位点）。
样例数据 samples/brd_export_sample.json 可在无 Allegro 时试跑：
   `python main.py --brd-dfm samples/brd_export_sample.json`
