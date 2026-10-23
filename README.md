# Evan DFM — PCB 可制造性(DFM)专业分析软件

面向 PCB 制造与 SMT 装配的桌面级 DFM 分析平台：导入 **Allegro .brd / SKILL·extracta 导出 JSON / Gerber(RS-274X) / ODB++ / NC 钻孔**，
按**板厂制程能力库**逐条判定，输出带 **XY 定位**的分级问题清单、**可制造性评分**与 **Valor 式报告**，并支持在版图上交叉定位。

规则库全部外置为 YAML，检查项以插件方式注册 —— 不改代码即可换标准、加规则、扩展新检查。

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多源解析 | Allegro `.brd`（extracta 无界面提取）、SKILL/extracta 导出 JSON、Gerber RS-274X（含**光圈宏 %AM**、**阵列 SR**、**负片极性 %LPC/%IP**、X2 属性）、ODB++ 工程（**符号表真实尺寸**、铺铜 surface、阵列 R、**gzip/tgz/zip 自动解包**）、Excellon 钻孔（刀具表、重复孔 R##、**G85 与 M15/M16 槽孔**） |
| 几何内核 | `dfm/geom`：真实形状（圆/矩形/椭圆/多边形/圆弧）+ 负片极性布尔合成 + 精确净距（GEOS）+ 共享空间索引；矩形焊盘不再按外接圆近似，铺铜按真实轮廓判定 |
| 制造 DFM | 最小线宽/线距（按成品铜厚分档）、单边孔环、机械孔/激光孔径、PTH 纵横比、线路到板边、**孔到板边**、**孔到孔间距(CAF)**、**槽孔宽度**、阻焊桥、丝印、塞孔、覆盖膜、表面处理、电测 PAD |
| 装配 / SMT DFM | 器件间距、器件/焊盘到板边、片式焊盘对称性（墓碑）、细间距与 BGA 孔环、Via in Pad、丝印上焊盘、基准点数量与尺寸、测试点覆盖率/尺寸/到板边 |
| 开短路与网表 | 基于**真实几何与极性合成**判定开路/短路（定位 XY）、逻辑网表一致性、CAD vs FAB 网络比对 |
| 拼板 / 工艺边 | 工作板尺寸、工艺边宽度、Mark 点数量与位置、定位孔、板材利用率 |
| 制造数据完整性 | 缺层、单位/格式声明、光圈未定义、坐标越界、孤立孔、层原点偏移、负片层提示 |
| 判定可信度 | **判定覆盖率**（哪些规则真的判过、哪些因能力库缺项未判）、**数据质量**（解析受限/负片合成/元素截断）、**处数与留存分离**（密集板不再把"留存 500 处"当成"共 500 处"） |
| 评分与报告 | 可制造性评分（权重可配）、良率区间、Excel（多页 + 内嵌版图截图 + 数据质量页）、HTML（交互式单文件）、JSON、TXT、SVG |
| 自定义规则引擎 | `rules/capability/*.yaml` 板厂能力库自动发现；`rules/checks.yaml` 控制开关/阈值/严重度/告警带/评分/损失模型；声明式 `custom_rules` 无需写代码 |
| 二次开发 | 检查项插件注册、共享几何模型 `ctx.geometry`、页面基类、统一 `analysis` 数据结构、CLI 与 JSON 输出 |

## 安装与启动

```bat
:: 1. 安装依赖 (Python 3.9+)
pip install -r requirements.txt

:: 2. 启动图形界面
python evan_dfm.py
::   或
python -m dfm gui
```

打包为独立 exe：

```bat
pip install pyinstaller
build.bat            :: 产物 dist\EvanDFM.exe
```

## 30 秒上手

```bat
:: 分析一个 Allegro 导出 JSON, 输出 Excel + HTML 报告
python -m dfm analyze samples\brd_export_sample.json -o out --format excel,html

:: 分析 Gerber 目录 (指定板厚才能判纵横比)
python -m dfm analyze D:\gerber_dir --factory jlcpcb --level extreme --thickness 1.6 -o out

:: 只看解析结果与自动量测参数
python -m dfm info D:\board.json

:: 查看规则库 / 检查项
python -m dfm rules list
python -m dfm checks
```

图形界面主流程：**数据源 → 检查与执行 → 问题清单 / 版图定位 → 报告导出**，
判定标准在 **规则库** 页维护，环境与主题在 **设置** 页。

## 目录结构

```
DFM-AI/
├─ evan_dfm.py            启动脚本 (GUI / cli / viewer)
├─ build.bat              PyInstaller 打包
├─ EvanDFM.spec           打包配置 (含 shapely/检查模块 hiddenimports)
├─ Evan_DFM.manifest      Per-Monitor V2 高 DPI / UTF-8 / 长路径
├─ pyproject.toml         依赖与打包元数据
├─ requirements.txt       运行依赖 (PySide6 / shapely / openpyxl / PyYAML / Pillow)
├─ dfm/                   主程序包
│  ├─ app.py              应用启动 (QApplication / 主题 / 日志 / DPI / 独立版图窗口)
│  ├─ cli.py              命令行
│  ├─ paths.py            路径解析 (开发 / 打包 / 用户可写目录)
│  ├─ branding.py         品牌信息
│  ├─ geom/               ★ 几何内核 (判定精度基础)
│  │  ├─ shapes.py        真实形状: 线段/圆/矩形/椭圆/多边形/圆弧 (解析包围盒)
│  │  ├─ elements.py      元素: 形状 + 层 + 网络 + 极性
│  │  ├─ polarity.py      负片合成: dark − clear (内层平面/anti-pad)
│  │  ├─ clearance.py     精确净距 (解析快路径 + GEOS 精算) 与广相候选
│  │  ├─ board.py         BoardGeometry: 一次构建、多检查复用的共享模型
│  │  └─ measure.py       器件本体/间距/基准点/引脚间距等共享量测
│  ├─ core/               issue(问题模型+计数) capability(能力库) rules_config(规则集)
│  │                      registry(检查注册/插件) source(数据源归一) coverage(覆盖率/数据质量)
│  │                      pipeline(编排)
│  ├─ checks/             fabrication assembly netlist fabdata fabnet
│  │                      panelization capability netclass
│  ├─ io/                 gerber(RS-274X) aperture(光圈/宏) excellon(钻孔/槽孔)
│  │                      odbpp(作业/符号/压缩包) brd_model extracta skill_export
│  │                      fab(统一入口/完整性/网表比对/转设计模型)
│  ├─ report/             writers(Excel/TXT) html svg snapshot __init__(导出门面)
│  └─ ui/                 main_window state theme icons workers
│                         pages/(home project analysis results board rules report settings)
│                         widgets/(common issue_table board_view)
├─ rules/
│  ├─ capability/         jlcpcb.yaml (示例, 取自公开能力表) ← 板厂制程能力库
│  └─ checks.yaml         规则集 (开关/阈值/严重度/告警带/评分/损失/自定义规则)
├─ plugins/               检查插件目录 (_sample_check.py 为示例, 默认不加载)
├─ assets/                图标 (evan.ico / evan.png)
├─ samples/               示例数据 (brd_export_sample.json / dfm_design_params.json)
├─ tests/                 单元测试 214 项 (python -m unittest discover -s tests)
└─ docs/                  教程与手册
   ├─ 01-快速上手.md
   ├─ 02-用户手册.md
   ├─ 03-规则库配置指南.md
   ├─ 04-二次开发指南.md
   ├─ 05-命令行与自动化.md
   ├─ 06-常见问题FAQ.md
   ├─ 07-检查规则清单.md
   ├─ 08-更新日志.md
   └─ capability-template.yaml   新增板厂能力库模板
```

## 数据流

```
输入                          归一化                          检查                        输出
──────────────────────────────────────────────────────────────────────────────────────────────
.brd ──extracta──┐
.json (SKILL)  ──┤
Gerber(宏/阵列/  ├─→ 解析层 ─→ BrdDesign ─→ BoardGeometry ─┬→ fabrication 几何制造 DFM ─┐
  负片/X2)     ──┤   io/*      (统一模型)   (真实形状+极性  ├→ assembly    装配/SMT DFM  │
ODB++(符号/铺铜/ │                            合成+共享索引) ├→ netlist     开短路/网表    ├→ Issue[]
  压缩包)      ──┤                                          │                            │   (处数与
Excellon(槽孔/ ──┘                                          ├→ fabdata     制造数据完整性 │    留存分离)
  重复孔/刀具表)                                             ├→ fabnet      CAD vs FAB    │      ↓
设计参数 JSON ──────→ params ───────────────────────────────┼→ capability  制程能力比对   │  规则集改判
CAD 网表 JSON ──────→ cad_nets ─────────────────────────────┼→ panelization 拼板/工艺边   │  (阈值/严重度
                                                            └→ netclass    网络分类/阻抗  ─┘   /告警带/开关)
                                                                                        ↓
                                          Coverage 判定覆盖率 + DataQuality 数据质量 → 评分汇总
                                                                                        ↓
                                       Excel(含数据质量页) / HTML / JSON / TXT / SVG + 版图交叉定位
```

**关键不变量**：`core / checks / geom / io / report` 均不依赖 Qt，可单独用于 CI、
服务端与脚本；几何模型 `BoardGeometry` 一次构建、多检查复用。

## 文档

- [快速上手](docs/01-快速上手.md) — 安装、第一次分析、看懂报告
- [用户手册](docs/02-用户手册.md) — 界面逐页说明、工作流、检查项含义
- [规则库配置指南](docs/03-规则库配置指南.md) — 能力库结构、新增板厂、阈值/严重度覆盖、自定义规则、评分模型
- [二次开发指南](docs/04-二次开发指南.md) — 架构、数据结构、写检查插件、加界面页、扩展报告
- [命令行与自动化](docs/05-命令行与自动化.md) — 全部命令与参数、CI/MES 集成
- [常见问题 FAQ](docs/06-常见问题FAQ.md) — extracta、Gerber 无 X2、性能、误报、打包
- [检查规则清单](docs/07-检查规则清单.md) — 全部规则的判定依据与严重度
- [更新日志](docs/08-更新日志.md) — v1.1 判定正确性与解析完整性重构（13 项缺陷修复明细）

## 许可

Apache License 2.0，见 [LICENSE](LICENSE)。

随包提供的示例能力库 `rules/capability/jlcpcb.yaml` 数据取自 JLCPCB **公开**能力表；
你自己板厂的能力库请放在 `%APPDATA%\EvanDFM\rules\capability\`（用户目录，不入版本库），
避免把供应商评估资料提交到公开仓库。
