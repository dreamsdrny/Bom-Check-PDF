# Evan DFM — PCB 可制造性(DFM)专业分析软件

面向 PCB 制造与 SMT 装配的桌面级 DFM 分析平台：导入 **Allegro .brd / SKILL·extracta 导出 JSON / Gerber(RS-274X) / ODB++ / NC 钻孔**，
按**板厂制程能力库**逐条判定，输出带 **XY 定位**的分级问题清单、**可制造性评分**与 **Valor 式报告**，并支持在版图上交叉定位。

规则库全部外置为 YAML，检查项以插件方式注册 —— 不改代码即可换标准、加规则、扩展新检查。

---

## 核心能力

| 能力 | 说明 |
| --- | --- |
| 多源解析 | Allegro `.brd`（extracta 无界面提取）、SKILL/extracta 导出 JSON、Gerber RS-274X（含 X2 属性）、ODB++ 工程、Excellon 钻孔 |
| 制造 DFM | 最小线宽/线距（按成品铜厚分档）、单边孔环、机械孔/激光孔径、PTH 纵横比、线路到板边、阻焊桥、丝印、塞孔、覆盖膜、表面处理、电测 PAD |
| 装配 / SMT DFM | 器件间距、器件/焊盘到板边、片式焊盘对称性（墓碑）、细间距与 BGA 孔环、Via in Pad、丝印上焊盘、基准点数量与尺寸、测试点覆盖率/尺寸/到板边 |
| 开短路与网表 | 几何连通性判定开路/短路（定位 XY）、逻辑网表一致性、CAD vs FAB 网络比对 |
| 拼板 / 工艺边 | 工作板尺寸、工艺边宽度、Mark 点数量与位置、定位孔、板材利用率 |
| 制造数据完整性 | 缺层、单位/格式声明、光圈未定义、坐标越界、孤立孔、层原点偏移 |
| 评分与报告 | 可制造性评分（可配权重）、良率区间、Excel（多页 + 内嵌版图截图）、HTML（交互式单文件）、JSON、TXT、SVG |
| 自定义规则引擎 | `rules/capability/*.yaml` 板厂能力库自动发现；`rules/checks.yaml` 控制开关/阈值/严重度/评分；声明式 `custom_rules` 无需写代码 |
| 二次开发 | 检查项插件注册、页面基类、统一 `analysis` 数据结构、CLI 与 JSON 输出 |

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
python -m dfm analyze D:\gerber_dir --factory 板厂B --level automotive --thickness 1.6 -o out

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
├─ EvanDFM.spec           打包配置
├─ pyproject.toml         依赖与打包元数据
├─ dfm/                   主程序包
│  ├─ app.py              应用启动 (QApplication / 主题 / 日志 / DPI)
│  ├─ cli.py              命令行
│  ├─ paths.py            路径解析 (开发/打包/用户目录)
│  ├─ branding.py         品牌信息
│  ├─ core/               issue(问题模型) capability(能力库) rules_config(规则集)
│  │                      registry(检查注册/插件) source(数据源归一) pipeline(编排)
│  ├─ checks/             fabrication assembly netlist fabdata fabnet
│  │                      panelization capability netclass
│  ├─ io/                 brd_model extracta skill_export fab gerber_model
│  ├─ report/             writers(Excel/TXT) html svg snapshot __init__(导出门面)
│  └─ ui/                 main_window state theme icons workers
│                         pages/(home project analysis results board rules report settings)
│                         widgets/(common issue_table board_view)
├─ rules/
│  ├─ capability/         hongban.yaml sihui.yaml jlcpcb.yaml  ← 板厂制程能力库
│  └─ checks.yaml         规则集 (开关/阈值/严重度/评分/自定义规则)
├─ plugins/               检查插件目录 (_sample_check.py 为示例, 默认不加载)
├─ assets/                图标 (evan.ico / evan.png)
├─ samples/               示例数据 (brd_export_sample.json / dfm_design_params.json)
├─ tests/                 单元测试 (python -m unittest discover -s tests)
└─ docs/                  教程与手册
   ├─ 01-快速上手.md
   ├─ 02-用户手册.md
   ├─ 03-规则库配置指南.md
   ├─ 04-二次开发指南.md
   ├─ 05-命令行与自动化.md
   ├─ 06-常见问题FAQ.md
   ├─ 07-检查规则清单.md
   └─ capability-source/  能力库来源 Excel (可追溯)
```

## 数据流

```
输入                     归一化                        检查                          输出
────────────────────────────────────────────────────────────────────────────────────────
.brd ──extracta──┐
.json (SKILL)  ──┼─→ BrdDesign ─┬─→ fabrication  几何制造 DFM (XY) ─┐
Gerber/ODB++ ────┘   (统一模型)  ├─→ assembly     装配 / SMT DFM     │
                      │          ├─→ netlist      开短路 / 网表一致性 ├─→ Issue[]
设计参数 JSON ─────────┼─→ params ├─→ capability   制程能力比对       │   ↓
CAD 网表 JSON ─────────┘          ├─→ fabnet       CAD vs FAB        │  规则集改判
                                  ├─→ panelization 拼板 / 工艺边      │  (阈值/严重度/开关)
fab_data (渲染几何) ──────────────┴─→ fabdata      制造数据完整性 ────┘   ↓
                                                              评分 + 汇总 → analysis
                                                                            ↓
                                             Excel / HTML / JSON / TXT / SVG + 版图交叉定位
```

## 文档

- [快速上手](docs/01-快速上手.md) — 安装、第一次分析、看懂报告
- [用户手册](docs/02-用户手册.md) — 界面逐页说明、工作流、检查项含义
- [规则库配置指南](docs/03-规则库配置指南.md) — 能力库结构、新增板厂、阈值/严重度覆盖、自定义规则、评分模型
- [二次开发指南](docs/04-二次开发指南.md) — 架构、数据结构、写检查插件、加界面页、扩展报告
- [命令行与自动化](docs/05-命令行与自动化.md) — 全部命令与参数、CI/MES 集成
- [常见问题 FAQ](docs/06-常见问题FAQ.md) — extracta、Gerber 无 X2、性能、误报、打包
- [检查规则清单](docs/07-检查规则清单.md) — 全部规则的判定依据与严重度

## 许可

内部使用。制程能力数据来源于各板厂公开/评估资料，见 `docs/capability-source/`。
