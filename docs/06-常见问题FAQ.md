# 06 常见问题 FAQ

## 一、启动与安装

**Q：双击没反应 / 报 `ModuleNotFoundError: No module named 'PySide6'`**
A：依赖没装或用了别的 Python。

```bat
python -m dfm version        :: 看依赖安装状态与实际使用的目录
pip install -r requirements.txt
```

有虚拟环境时先激活；`where python` 确认解释器路径一致。

**Q：界面字体发虚 / 图标模糊**
A：Windows 高 DPI 已启用 Per-Monitor V2（打包时通过 `Evan_DFM.manifest` 声明）。
若在远程桌面或多屏混排 DPI 下仍模糊，右键 exe → 属性 → 兼容性 → 更改高 DPI 设置 → 勾选「替代高 DPI 缩放行为」，缩放执行方选「应用程序」。

**Q：想要浅色界面**
A：左下「切换主题」，或「设置 → 外观」。选择会被记住。

---

## 二、数据源

**Q：`.brd` 分析报错找不到 extracta**
A：三种解决办法，任选其一：

1. 「设置」页填 `extracta.exe` 全路径（一般在 `C:\Cadence\SPB_17.4\tools\bin\extracta.exe`），或点「自动探测」
2. 命令行加 `--extracta "C:\Cadence\SPB_17.4\tools\bin\extracta.exe"`
3. 不用 extracta：「设置」页 → **导出 SKILL 脚本**，在 Allegro 里执行
   ```
   skill load "evan_dfm_export.il"
   brdExportJSON
   ```
   再把导出的 JSON 作为主数据源（结果一致，且不需要本机装 Cadence）

extracta 提取一块板通常 1–2 分钟，日志会显示进度，属正常耗时。

**Q：Gerber 分析后没有开路/短路结论**
A：因为 Gerber 缺少 X2 网络属性（`%TO.N` / `%TO.P`），网络只能由几何连通性派生 —— 可以做间距、孔环、板边、线宽，但**无法判定开短路**。日志与报告都会给出该提示。解决：

- 让 CAM 导出时勾选 X2 扩展属性（多数 CAM 支持）
- 或改用 ODB++（自带网表）
- 或提供 CAD 网表 JSON，勾 `fabnet` 做 CAD vs FAB 比对

**Q：Gerber 目录里文件命名很奇怪，层识别错了**
A：层识别优先看 X2 的 `%TF.FileFunction`，其次按文件名约定匹配（支持 AD/Protel/KiCad/Allegro/Eagle 常见命名：`.gtl/.gbl/.gts/.gbs/.gto/.gbo/.gtp/.gbp/.gko/.gm1`、`top/bottom`、`silk/mask/paste`、`profile/outline/edge.cuts` 等）。
用 `python -m dfm info <目录>` 查看识别结果；识别不对时建议改名或补 X2 属性。

**Q：板厚从哪里来？**
A：`.brd`/JSON 里带板厚；Gerber/ODB++ **不含板厚**，必须在「数据源」页填「板厚」或命令行加 `--thickness`，否则 PTH 纵横比、塞孔纵横比会被跳过。人工填写的值优先于文件内的值。

**Q：ODB++ 目录怎么选？**
A：选到含 `matrix/matrix` 的那一级目录（工程根），程序会自动识别为 `odb`。

---

## 三、判定结果

**Q：明明没问题却报了一堆「到板边 0mm」**
A：几乎都是板框识别问题。程序在**没有真实轮廓层**时会跳过到板边检查（避免把铜箔外包框当板边）；若你提供的轮廓层其实是铜层，请检查层识别结果（`info` 命令或界面预览）。

**Q：线宽判定和 Allegro 不一致**
A：注意两点：

1. 能力库的线宽/线距是**按成品铜厚分档**的，铜厚填错会整档偏移（`um` 与 `oz` 要对应，留空 `oz` 会自动换算）
2. 若导出的走线宽是「网络平均值」（`etch_width_is_average=True`），间距与短路判定只采信焊盘/过孔真实尺寸，走线宽仍按实测报告

**Q：某条规则客户不考核 / 想更严**
A：不要改代码，用规则集：

```yaml
rules:
  最小阻焊桥: { enabled: false }        # 不考核
  最小线宽:   { min: 0.1, severity: 危险 }   # 比板厂更严
```

界面「规则库 → 规则集 → 从当前结果导入规则名」可避免手打规则名出错。

**Q：同一问题在报告里出现两次**
A：正常不会。几何检查（带 XY 定位）与参数化检查会**自动去重**：几何已判定的规则，参数化比对不再重复报。如果你用插件写了同规则的判定，请自行避让或改规则名。

**Q：评分为什么是 0**
A：`score = 100 − min(100, Σ权重×数量)`，默认危险项权重 10，10 个危险项即扣满。
想让评分更平缓，调 `rules/checks.yaml` 的 `scoring.weights` 或 `cap_penalty`。

**Q：器件间距/基准点判定不准**
A：器件本体用**焊盘包围盒近似**（数据里没有器件实体外形），所以：

- 有外形的高器件（连接器、屏蔽罩）实际间距会比计算值小 → 结果偏乐观
- 基准点靠位号关键字识别（`FID/MARK/FP/BENCHMARK/基准`），客户命名特殊时在 `assembly.fiducial_names` 里加关键字

**Q：Via in Pad 报了很多，我们本来就做塞孔**
A：设 `assembly.via_in_pad_allowed: true`，会降级为「低」并提示确认塞孔工艺。

---

## 四、性能

**Q：大板分析很慢**
A：内置检查已做空间索引（扫描剪枝 + 均匀网格）与元素上限（40 万元素、每规则 500 处实例）。仍可这样加速：

- `--modules fabrication,assembly` 只跑需要的检查
- `--no-plugins` 不加载插件
- 报告加 `--no-images`（Excel 内嵌截图是主要耗时项之一）
- 用 JSON 数据源代替 `.brd`（省掉 extracta 提取时间）

**Q：版图页卡顿**
A：图层面板关掉不需要的层（尤其多层内层与铺铜），或用「仅铜层」。渲染已按「同层同线宽合并路径」优化，正常十万级图元可流畅缩放。

**Q：内存占用高**
A：`analysis` 里保留了渲染几何（`fab_data`）与全部定位实例（上限 2 万条）。导出 JSON 时会自动瘦身（`slim_analysis`）；批量处理建议每块板一个进程。

---

## 五、报告

**Q：Excel 报告里没有版图截图**
A：需要 Pillow，且必须有几何数据。检查 `python -m dfm version` 里 Pillow 是否已安装；或导出时未勾选「Excel 内嵌版图截图」。

**Q：HTML 报告能发给客户吗？**
A：可以。它是**单文件、无外网依赖**（CSS/JS/SVG 全内嵌），内网或离线双击即可打开，支持缩放版图、过滤问题、点标记联动。

**Q：报告文件名/客户名怎么改**
A：「报告导出」页填「文件名」「客户」「提交人」；命令行用 `--customer` / `--submitter`，文件名由 `-o` 决定。

**Q：想加公司 Logo 或改报告版式**
A：Excel 改 `dfm/report/writers.py`，HTML 改 `dfm/report/html.py` 的 `_CSS`/`_JS` 与模板。见 [04 二次开发指南 §6](04-二次开发指南.md#6-扩展报告)。

---

## 六、规则库与插件

**Q：改了 YAML 没生效**
A：三件事按顺序检查：

1. 文件位置是否在搜索路径里（`python -m dfm rules path`），用户目录优先于出厂目录
2. YAML 是否有语法错误（缩进、冒号后空格、中文引号）—— 用 `python -m dfm rules show` 验证能否加载
3. 界面点「规则库 → 重新载入」（命令行直接重跑即可；程序按文件 mtime 自动失效缓存）

**Q：新增板厂后界面里看不到**
A：文件名就是板厂 id，必须放在某个搜索路径的 `capability/` 子目录下，扩展名 `.yaml`/`.yml`。放好后点「重新加载」。显示名取 YAML 里的 `meta.factory`。

**Q：插件没被加载**
A：

- 文件名不能以 `_` 或 `.` 开头（示例插件 `_sample_check.py` 默认不加载，改名即启用）
- 必须放在插件搜索目录（界面「规则库 → 插件」页顶部列出全部目录）
- 语法错误或 import 失败会记录在插件页日志与 `analysis["plugins"]`，**不会**影响主流程
- 打包 exe 时新增的检查模块要加进 `EvanDFM.spec` 的 `hiddenimports`

**Q：插件里能用 Qt 吗？**
A：不建议。`core/checks/io/report` 与 Qt 解耦，插件保持纯 Python 才能同时用于 CLI、CI 和界面。

---

## 七、打包与部署

**Q：打包后 exe 很大**
A：`EvanDFM.spec` 已排除 tkinter/matplotlib/scipy/pandas 与未使用的 Qt 模块（Quick/Qml/WebEngine/3D/Charts/Multimedia 等）。若仍偏大，可改 `--onedir` 模式（启动更快、便于替换规则库）。

**Q：打包后想让客户自己改规则**
A：让客户改 `%APPDATA%\EvanDFM\rules\`（优先级高于包内），或把规则目录放在 exe 同级 `rules\`。界面「规则库 → 复制为用户副本」就是为此设计。

**Q：exe 运行时看不到报错**
A：`EvanDFM.spec` 里 `console=False`。排查问题时改成 `True` 重新打包，或直接用源码 `python -m dfm ...` 并设 `DFM_DEBUG=1` 查看完整堆栈；日志始终写在 `%APPDATA%\EvanDFM\logs\evan_dfm.log`。

**Q：能否绿色部署（不装 Python）？**
A：可以，打包后的 exe 自带运行时。目标机无需 Python，仅需 Windows 10/11 x64。

---

## 八、其他

**Q：支持 Altium / Mentor / KiCad 吗？**
A：支持它们的**制造数据输出**（Gerber RS-274X、ODB++、Excellon 钻孔），这是最通用的路径。原生工程文件目前只支持 Cadence Allegro `.brd`（extracta / SKILL）。若要新增原生格式，写一个 `io/` 解析器并转成 `BrdDesign` 即可，检查层无需改动。

**Q：单位会搞错吗？**
A：内部统一 **mm**。解析层负责换算：Gerber `%MOIN/%MOMM`、Excellon `INCH/METRIC`、ODB++ `UNITS`；能力库每项带 `unit`（mm/um/mil/percent/ratio）自动换算。坐标 Y 向上（PCB 习惯）。

**Q：数据会上传吗？**
A：不会。全程本地运行，无网络请求。用户数据只在 `%APPDATA%\EvanDFM\`。

**Q：怎么做版本追溯？**
A：报告封面含板名、版本、板厂、能力等级、铜厚、规则集名称、生成时间、提交人；能力库 YAML 有 `meta.revision` 与 `meta.source_files`；`analysis["rules_source"]` 记录实际使用的规则集路径。建议把导出的 JSON 一并归档。

**Q：命令返回码怎么用于门禁？**
A：`--strict` 时存在「危险」问题返回 1，否则 0；参数错误返回 2；中断返回 130。

---

## 需要更多帮助

1. `python -m dfm version` 收集环境信息
2. 附上 `%APPDATA%\EvanDFM\logs\evan_dfm.log`
3. 命令行加 `--quiet` 去掉进度后重现，并设 `DFM_DEBUG=1` 取完整堆栈
4. 说明数据源类型、板厂/等级、期望结果与实际结果
