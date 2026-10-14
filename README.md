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
