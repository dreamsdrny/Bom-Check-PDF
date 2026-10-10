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
