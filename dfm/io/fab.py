"""
制造数据读取与完整性检查 (Gerber / ODB++ / NC Drill / 压缩包)
=============================================================
本模块是**统一入口与业务层**; 各格式的具体解析实现见:

  :mod:`dfm.io.gerber`    Gerber RS-274X (宏光圈/阵列/极性/真实焊盘形状/X2 属性)
  :mod:`dfm.io.excellon`  Excellon 钻孔 (刀具表/重复孔/G85 与 M15-M16 槽孔)
  :mod:`dfm.io.odbpp`     ODB++ 作业 (matrix/符号表/铺铜 surface/阵列/netlist/components,
                          支持 gzip 与 tgz/tar/zip 自动解包)
  :mod:`dfm.io.aperture`  光圈与光圈宏模型 (含表达式求值)

本模块负责:
  1. :func:`read_fab_data` 统一读取 (文件/目录/列表/压缩包)
  2. :func:`integrity_check` 制造数据完整性检查
  3. :func:`collect_fab_nets` / :func:`compare_netlists` CAD vs FAB 网表比对
  4. :func:`summarize_fab` / :func:`analyze_fab` 统计与汇总
  5. :func:`fab_to_design` 转为统一设计模型 (供几何/装配/开短路检查复用)

输出:
  - fab_data: 统一的层数据列表 (kind = gerber | odbpp | drill), 供判定、报告与版图渲染
  - issues:   与 :mod:`dfm.core.issue` 一致的 DFM 问题结构
"""
import logging
import os
import re
from collections import OrderedDict
from typing import Any, Dict, List, Optional, Set, Tuple

from dfm.core.issue import make_issue
from dfm.io import excellon as _excellon
from dfm.io import gerber as _gerber
from dfm.io import odbpp as _odbpp

logger = logging.getLogger(__name__)

INCH_TO_MM = 25.4

INTEGRITY_ISSUE_PREFIX = "FAB"

# 板框轮廓层名识别 (Gerber/ODB 常见命名)
_OUTLINE_RE = re.compile(
    r"outline|oln|border|rout|gko|keep.?out|board.?geom|profile|mechanical|成型|板框",
    re.IGNORECASE)


def _num(value) -> Optional[float]:
    try:
        return float(value)
    except (TypeError, ValueError):
        return None


# ============================================================
# 解析器入口 (实现见 dfm.io.gerber / dfm.io.excellon / dfm.io.odbpp)
# ============================================================
def parse_gerber(path: str, layer_name: Optional[str] = None) -> dict:
    """解析 Gerber RS-274X。

    支持: ``%FS`` 位宽与零省略、``%MO``、光圈宏 ``%AM``(含 ``$n`` 参数表达式)、
    阵列光圈与 ``%SR`` 区域阵列、``%LPD/%LPC`` 极性、``%IP``/``%TF.FilePolarity``
    负片层、``%AS``/``%SF``/``%LN``、``G01/02/03``、``G74/G75``、``G36/G37``
    区域填充、X2 属性 (``%TO.N/%TO.P/%TF.FileFunction``)。
    焊盘保留真实形状 (``shape/w/h/rot/points/hole``)。
    """
    return _gerber.parse(path, layer_name)


def parse_excellon(path: str, plating: Optional[str] = None) -> dict:
    """解析 Excellon NC 钻孔 (刀具表 / 重复孔 R## / G85 与 M15-M16 槽孔)。"""
    return _excellon.parse(path, plating)


def parse_odbpp(job_dir: str) -> dict:
    """解析 ODB++ 作业 (matrix / features 含符号表与铺铜 / 阵列 R / netlist / components)。"""
    return _odbpp.parse_job(job_dir)


def flatten_odb_job(job: dict) -> List[dict]:
    """把 ODB++ 作业解析结果摊平为与其它数据源一致的层列表。"""
    out: List[dict] = []
    for layer in job.get("layers") or []:
        out.append(layer)
    for drill in job.get("drills") or []:
        out.append(drill)
    for note in job.get("notes") or []:
        for layer in out:
            layer.setdefault("notes", [])
            if note not in layer["notes"]:
                layer["notes"].append(note)
            break
    if job.get("components"):
        for layer in out:
            layer["components"] = job["components"]
            break
    return out


# ============================================================
# 统一读取 (目录 / 文件 / 文件列表 / 压缩包)
# ============================================================
ARCHIVE_EXTS = (".zip", ".tgz", ".tar.gz", ".tar", ".gz")

#: 解析结果缓存: {(路径, mtime, 大小): fab_data}
#: 用途: 改规则集/换板厂后重跑同一份数据时不必重新解析 (大 Gerber 解析是主要耗时项)。
#: 键含 mtime 与大小, 文件一变即失效; 进程内有效, 容量上限由 _CACHE_MAX 控制。
_PARSE_CACHE: "OrderedDict[Tuple[str, float, int], List[dict]]" = OrderedDict()
_CACHE_MAX = 8


def _cache_key(path: str) -> Optional[Tuple[str, float, int]]:
    try:
        st = os.stat(path)
    except OSError:
        return None
    return (os.path.abspath(path), st.st_mtime, st.st_size)


def _dir_cache_key(directory: str) -> Optional[Tuple[str, float, int]]:
    """目录缓存键: 取目录内全部相关文件的 (数量, 最大 mtime, 总大小) 指纹。"""
    count = 0
    newest = 0.0
    total = 0
    for root, _dirs, files in os.walk(directory):
        for fn in files:
            try:
                st = os.stat(os.path.join(root, fn))
            except OSError:
                continue
            count += 1
            total += st.st_size
            newest = max(newest, st.st_mtime)
    if not count:
        return None
    return (os.path.abspath(directory), newest, total)


def clear_parse_cache() -> None:
    """清空解析缓存 (规则库/数据变更后可调用; 正常无需手动调用)。"""
    _PARSE_CACHE.clear()


def read_fab_data(path, use_cache: bool = True) -> List[dict]:
    """读取制造数据, 返回统一的层数据列表。

    支持: Gerber 文件/目录、Excellon 钻孔、ODB++ 作业目录、
    以及 ``.zip`` / ``.tgz`` / ``.tar`` 压缩包 (自动解包到临时目录后解析)。

    :param use_cache: 命中 (路径, mtime, 大小) 指纹时复用上次解析结果
    """
    if isinstance(path, (list, tuple)):
        result: List[dict] = []
        for p in path:
            result.extend(read_fab_data(p, use_cache=use_cache))
        return result
    if not path:
        return []
    p = str(path)

    key = None
    if use_cache:
        key = _dir_cache_key(p) if os.path.isdir(p) else _cache_key(p)
        if key is not None and key in _PARSE_CACHE:
            _PARSE_CACHE.move_to_end(key)
            logger.debug("解析缓存命中: %s", p)
            return _PARSE_CACHE[key]

    if os.path.isdir(p):
        data = _read_dir(p)
    elif p.lower().endswith(ARCHIVE_EXTS):
        data = _read_archive(p)
    else:
        data = _read_single(p)

    if use_cache and key is not None and data:
        _PARSE_CACHE[key] = data
        while len(_PARSE_CACHE) > _CACHE_MAX:
            _PARSE_CACHE.popitem(last=False)
    return data


def _read_dir(directory: str) -> List[dict]:
    if os.path.isfile(os.path.join(directory, "matrix", "matrix")):
        return flatten_odb_job(parse_odbpp(directory))
    result: List[dict] = []
    for root, _dirs, files in os.walk(directory):
        for fn in sorted(files):
            full = os.path.join(root, fn)
            if full.lower().endswith(ARCHIVE_EXTS):
                result.extend(_read_archive(full))
            else:
                result.extend(_read_single(full))
    return result


def _read_archive(archive: str) -> List[dict]:
    import shutil
    import tempfile

    tmp = tempfile.mkdtemp(prefix="evan_dfm_fab_")
    try:
        root = _odbpp.extract_archive(archive, tmp)
        if not root:
            logger.warning("无法解包: %s", archive)
            return []
        return _read_dir(root)
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


def _read_single(path: str) -> List[dict]:
    low = path.lower()
    if low.endswith((".drl", ".exc", ".nc", ".txt")) or "drill" in low or "npt" in low:
        try:
            return [parse_excellon(path)]
        except Exception as exc:
            logger.warning("解析钻孔文件失败, 已跳过: %s (%s)", path, exc)
            return []
    if low.endswith((".gbr", ".ger", ".art", ".pho", ".gtl", ".gbl", ".gts", ".gbs",
                     ".gto", ".gbo", ".gtp", ".gbp", ".gko", ".gm1")) or _looks_like_gerber(path):
        try:
            return [parse_gerber(path)]
        except Exception as exc:
            logger.warning("解析 Gerber 文件失败, 已跳过: %s (%s)", path, exc)
            return []
    return []


def _looks_like_gerber(path: str) -> bool:
    """通过头部特征判断是否为 Gerber 文件。"""
    try:
        with open(path, encoding="utf-8", errors="ignore") as f:
            head = f.read(8192)
    except OSError as exc:
        logger.debug("探测 Gerber 文件失败: %s (%s)", path, exc)
        return False
    return any(tok in head for tok in ("%FS", "%MO", "%ADD", "%AM", "G04", "G36"))



# ============================================================
# 完整性检查
# ============================================================
def integrity_check(fab_data: List[dict]) -> List[dict]:
    issues: List[dict] = []
    code_counter = [0]

    def add(category, rule, severity, description, x=None, y=None,
            measured=None, required=None, unit="", suggestion="", layer=""):
        code_counter[0] += 1
        issues.append(make_issue(
            code=f"{INTEGRITY_ISSUE_PREFIX}-{code_counter[0]:02d}",
            category=category, rule=rule, severity=severity,
            description=description, x=x, y=y, measured=measured,
            required=required, unit=unit, suggestion=suggestion, layer=layer,
        ))

    if not fab_data:
        add("制造数据", "数据缺失", "危险", "未解析到任何 Gerber/ODB++/NC Drill 制造数据",
            suggestion="确认制造文件路径与格式")
        return issues

    gerbers = [d for d in fab_data if d["kind"] == "gerber"]
    drills = [d for d in fab_data if d["kind"] == "drill"]
    odb = [d for d in fab_data if d["kind"] == "odbpp"]
    images = gerbers + odb            # 图形层 (Gerber 与 ODB++ 层同构处理)

    if not images:
        add("制造数据", "图形层缺失", "危险", "缺少线路/图形层 (Gerber 或 ODB++)",
            suggestion="补充 Gerber 或 ODB++ 数据")
    if not drills:
        add("制造数据", "钻孔层缺失", "危险", "缺少 NC Drill / ODB++ 钻孔数据",
            suggestion="补充 NC Drill 文件")

    seen_layers: Set[str] = set()
    for g in images:
        name = str(g.get("name") or "")
        base = re.sub(r"[^a-z0-9]", "", name.lower())
        if base and base in seen_layers:
            add("制造数据", "图层重复", "中",
                f"检测到疑似重复图层: {name}", layer=name,
                suggestion="核对叠层/图层命名, 删除重复输出")
        seen_layers.add(base)
        if not g.get("attributes_present") and _is_copper_layer(g):
            add("制造数据", "网络属性缺失", "中",
                f"图层 {name} 无网络属性 (Gerber X2 的 TO.N/TO.P 或 ODB++ .net_name), "
                f"无法进行网络表比对",
                layer=name, suggestion="EDA/CAM 导出时启用 X2 属性, 或改用 ODB++ / 提供 CAD 网表")
        if not g.get("feature_count"):
            add("制造数据", "空图层", "高",
                f"图层 {name} 无有效图形", layer=name,
                suggestion="检查该层是否漏输出")
        bbox = g.get("bbox") or [None] * 4
        if bbox[0] is not None and bbox[2] is not None:
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
            # 仅在图形范围真正退化(单点/负值)时告警: 只有横向走线的层 H=0 属正常
            if w < 0 or h < 0 or (w == 0 and h == 0):
                add("制造数据", "坐标偏移", "高",
                    f"图层 {name} 图形范围异常 (W={w:.3f}, H={h:.3f})", layer=name,
                    suggestion="检查坐标系/偏移与单位设定")
        # 负片层: 正常工艺, 但需提示判定方式 (已做极性合成)
        if g.get("image_polarity") == "negative" or g.get("negative_layer"):
            add("制造数据", "负片图层", "低",
                f"图层 {name} 为负片 (图像/层极性为 negative), 铜箔 = 整面减去 clear 图形; "
                f"已做极性合成, 但建议人工复核平面层连接",
                layer=name, suggestion="确认平面层 anti-pad/散热环设计意图, 必要时提供正片输出")
        # 解析异常图元 (未定义光圈的 flash 等)
        for prim in _layer_prims(g):
            if prim.get("type") == "error":
                rule = {"flash_no_aperture": "孔径未定义",
                        "draw_no_aperture": "孔径未定义",
                        "macro_expand_failed": "光圈宏展开失败",
                        "aperture_no_size": "孔径定义错误"}.get(prim.get("error"), "图形畸变")
                add("制造数据", rule, "高",
                    f"图层 {name} 存在异常图形 ({prim.get('error')})", layer=name,
                    x=prim.get("x1"), y=prim.get("y1"),
                    suggestion="修正光圈定义 (ADD) 或重新导出该层")
                break
        # 光圈/符号定义异常
        for dcode, ap in (g.get("apertures") or {}).items():
            params = ap.get("params") or []
            bad = (params and isinstance(params[0], (int, float)) and params[0] <= 0)
            if bad or ap.get("kind") == "unknown" or ap.get("note"):
                add("制造数据", "孔径定义错误", "高",
                    f"图层 {name} 光圈 D{dcode} 定义异常"
                    + (f": {ap.get('note')}" if ap.get("note") else " (尺寸<=0 或类型未知)"),
                    layer=name, suggestion="修正光圈尺寸/类型定义后重新导出")

    for d in drills:
        if d.get("hole_count", 0) == 0:
            add("制造数据", "钻孔为空", "高", f"钻孔文件 {d['name']} 无孔", layer=d["name"],
                suggestion="检查钻孔输出")
        if d.get("plating") == "unknown":
            add("制造数据", "镀通孔标注", "低",
                f"钻孔文件 {d['name']} 未标注 PTH/NPTH", layer=d["name"],
                suggestion="按镀通孔/非镀通孔分文件输出")
        slots = [h for h in (d.get("holes") or []) if h.get("is_slot")]
        no_dia = [h for h in (d.get("holes") or []) if not h.get("diameter_mm")]
        if no_dia:
            add("制造数据", "孔径未定义", "高",
                f"钻孔文件 {d['name']} 有 {len(no_dia)} 个孔无孔径 (刀具未定义)",
                layer=d["name"], suggestion="补全刀具表 (TxxCyy) 后重新导出")
        if slots:
            add("制造数据", "含槽孔", "低",
                f"钻孔文件 {d['name']} 含 {len(slots)} 个槽孔 (G85/铣槽), "
                f"已按槽参与孔到线/孔到板边判定",
                layer=d["name"], suggestion="确认板厂槽孔能力 (最小宽度/长度/间距)")
        for note in d.get("notes") or []:
            add("制造数据", "解析提示", "低", f"钻孔文件 {d['name']}: {note}",
                layer=d["name"], suggestion="核对钻孔文件格式")

    if len(images) > 1:
        # 只在**同类层**(铜层与铜层)之间比较原点: 阻焊/丝印/外形层的图形范围
        # 本来就与铜层不同, 跨类型比较会产生大量假"坐标偏移"
        boxes = [(str(g.get("name")), g.get("bbox")) for g in images
                 if _is_copper_layer(g) and (g.get("bbox") or [None])[0] is not None]
        if len(boxes) > 1:
            ref_name, ref = boxes[0]
            for name, b in boxes[1:]:
                if abs(b[0] - ref[0]) > 1.0 or abs(b[1] - ref[1]) > 1.0:
                    add("制造数据", "层间坐标偏移", "高",
                        f"铜层 {name} 与 {ref_name} 坐标原点偏移 >1mm", layer=name,
                        x=b[0], y=b[1],
                        suggestion="统一各层输出坐标系/原点 (同一 CAM 作业一次性导出)")

    return issues


def _layer_prims(layer: dict) -> List[dict]:
    """取一个图形层的图元列表 (Gerber 用 primitives, ODB++ 用 features)。"""
    return layer.get("primitives") or layer.get("features") or []


#: 非铜层关键字: 这些层本来就不带网络属性, 不应据此报"网络属性缺失"
_NON_COPPER_RE = re.compile(
    r"mask|silk|legend|paste|stencil|profile|outline|rout|document|drill|"
    r"gts|gbs|gto|gbo|gtp|gbp|gko|gm1|keep.?out|mechanical|成型|板框|阻焊|丝印",
    re.IGNORECASE)


def _is_copper_layer(layer: dict) -> bool:
    """判断一个图形层是否为铜箔层 (只有铜层才要求带网络属性)。"""
    kind = str(layer.get("layer_kind") or layer.get("odb_type") or "").lower()
    if kind:
        return kind in ("copper", "signal", "power_ground", "mixed")
    ff = str(layer.get("file_function") or "")
    if ff:
        return ff.split(",")[0].strip().lower() in ("copper", "mixed")
    name = str(layer.get("name") or "")
    return not bool(_NON_COPPER_RE.search(name))


# ============================================================
# 网络表比对
# ============================================================
def collect_fab_nets(fab_data: List[dict]) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    for d in fab_data:
        for net, nodes in (d.get("nets") or {}).items():
            nets.setdefault(net, set()).update(nodes)
    return nets


def normalize_cad_nets(cad: Any) -> Dict[str, Set[str]]:
    nets: Dict[str, Set[str]] = {}
    if isinstance(cad, dict):
        for net, nodes in cad.items():
            if isinstance(nodes, (list, set, tuple)):
                nets[net] = {str(n).replace("-", ".") for n in nodes}
            else:
                nets[net] = set()
    elif isinstance(cad, list):
        for item in cad:
            if isinstance(item, dict) and "net" in item:
                nodes = item.get("nodes") or item.get("pins") or []
                nets[item["net"]] = {str(n).replace("-", ".") for n in nodes}
    return nets


def compare_netlists(cad_nets: Dict[str, Set[str]],
                     fab_nets: Dict[str, Set[str]]) -> dict:
    cad_nets = cad_nets or {}
    fab_nets = fab_nets or {}
    all_nets = sorted(set(cad_nets) | set(fab_nets))
    net_only_cad = sorted(set(cad_nets) - set(fab_nets))
    net_only_fab = sorted(set(fab_nets) - set(cad_nets))
    node_open: List[dict] = []
    node_short: List[dict] = []
    matched = 0
    for net in all_nets:
        c = cad_nets.get(net, set())
        f = fab_nets.get(net, set())
        if net in cad_nets and net in fab_nets:
            matched += 1
        for node in sorted(c - f):
            node_open.append({"net": net, "node": node,
                              "reason": "CAD 有网络连接但制造数据无此连接 (疑似开路 Open)"})
        for node in sorted(f - c):
            node_short.append({"net": net, "node": node,
                               "reason": "制造数据有额外网络连接 (疑似短路 Short)"})

    passed = not (net_only_cad or net_only_fab or node_open or node_short)
    return {
        "cad_net_count": len(cad_nets),
        "fab_net_count": len(fab_nets),
        "matched_nets": matched,
        "net_only_cad": net_only_cad,
        "net_only_fab": net_only_fab,
        "node_open": node_open,
        "node_short": node_short,
        "open_count": len(node_open) + len(net_only_cad),
        "short_count": len(node_short) + len(net_only_fab),
        "passed": passed,
    }


def netlist_compare_issues(comparison: dict) -> List[dict]:
    issues: List[dict] = []
    counter = 0

    def add(rule, severity, desc, measured=None, required=None, unit="", suggestion=""):
        nonlocal counter
        counter += 1
        issues.append(make_issue(
            code=f"NET-{counter:02d}", category="网络比对", rule=rule,
            severity=severity, description=desc, measured=measured,
            required=required, unit=unit, suggestion=suggestion,
        ))

    for item in comparison.get("node_open", []):
        add("开路(Open)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中缺失连接",
            item["node"], "应连接", "", "核对网络/铺铜连接, 修正开路")
    for item in comparison.get("node_short", []):
        add("短路(Short)", "危险",
            f"网络 {item['net']} 节点 {item['node']} 在制造数据中出现额外连接",
            item["node"], "无连接", "", "检查孤立铜/残铜, 修正短路")
    for net in comparison.get("net_only_cad", []):
        add("网络缺失", "高", f"CAD 网络 {net} 在制造数据中不存在",
            net, "需存在", "", "核对网络是否漏输出/被合并")
    for net in comparison.get("net_only_fab", []):
        add("多余网络", "高", f"制造数据出现 CAD 中不存在的网络 {net}",
            net, "不应存在", "", "核对多余网络来源")
    if not issues:
        issues.append(make_issue(code="NET-00", category="网络比对", rule="网络一致性",
                                 severity="低", description="网络表比对通过, 未发现开路/短路",
                                 suggestion="无需处理"))
    return issues


def summarize_fab(data: List[dict]) -> dict:
    """从已解析的制造数据统计关键指标 (含派生网表)。"""
    return {
        "gerber_layers": [d["name"] for d in data if d["kind"] == "gerber"],
        "drill_files": [d["name"] for d in data if d["kind"] == "drill"],
        "odb_jobs": [d["name"] for d in data if d["kind"] == "odbpp"],
        "feature_count": sum(d.get("feature_count", 0) for d in data),
        "hole_count": sum(d.get("hole_count", 0) for d in data),
        "min_line_width_mm": _min_attr(data, "min_line_width_mm"),
        "min_hole_mm": _min_attr(data, "min_hole_mm"),
        "nets": collect_fab_nets(data),
    }


def analyze_fab(path: str) -> dict:
    data = read_fab_data(path)
    issues = integrity_check(data)
    stats = summarize_fab(data)
    return {"data": data, "issues": issues, "stats": stats}


def _min_attr(data, key):
    vals = [d.get(key) for d in data if d.get(key) is not None]
    return round(min(vals), 4) if vals else None



# ============================================================
# fab_data -> 统一 design schema (复用 BRD 的几何 DFM/开短路/报告)
# ============================================================
def fab_to_design(fab_data: List[dict], board_thickness: Optional[float] = None) -> dict:
    """把 Gerber/ODB++/NC Drill 解析结果转换为与 SKILL/extracta 导出相同的
    design schema, 从而复用 dfm.checks.fabrication / dfm.checks.netlist / dfm.report.writers。
    - draw/arc  -> clines (真实逐段线宽, etch_width_is_average=False)
    - flash     -> pads (焊盘/过孔焊盘, 含 net/refdes/pin/XY)
    - region    -> shapes (铺铜, 取包围盒)
    - drill 孔  -> vias (孔径; 就近匹配 flash 取网络/焊盘尺寸算孔环)
    - 网表节点  -> 由带 net+refdes+pin 的 flash 汇总 (含 XY)
    """
    clines: List[dict] = []
    pads: List[dict] = []
    vias: List[dict] = []
    shapes: List[dict] = []
    components: List[dict] = []
    parse_notes: List[str] = []
    layers_seen: List[str] = []
    net_nodes: Dict[str, List[dict]] = {}
    pad_by_loc: Dict[Tuple[float, float], dict] = {}
    drill_holes: List[dict] = []
    outline_pts: List[List[float]] = []
    all_x: List[float] = []
    all_y: List[float] = []

    def _note_layer(nm):
        if nm and nm not in layers_seen:
            layers_seen.append(nm)

    def _is_outline(layer: str) -> bool:
        return bool(_OUTLINE_RE.search(layer or ""))

    for d in fab_data:
        kind = d.get("kind")
        if kind in ("gerber", "odbpp"):
            feats = d.get("primitives") or d.get("features") or []
            for p in feats:
                t = p.get("type")
                net = p.get("net", "") or ""
                layer = p.get("layer", "") or d.get("name", "") or ""
                pol = "clear" if p.get("clear") else "dark"
                _note_layer(layer)
                if t in ("draw", "arc"):
                    x1, y1, x2, y2 = p.get("x1"), p.get("y1"), p.get("x2"), p.get("y2")
                    if None in (x1, y1, x2, y2):
                        continue
                    if _is_outline(layer):
                        outline_pts += [[x1, y1], [x2, y2]]
                        all_x += [x1, x2]; all_y += [y1, y2]
                        continue
                    rec = {"net": net, "layer": layer, "width": p.get("width"),
                           "points": [[x1, y1], [x2, y2]], "polarity": pol}
                    if t == "arc":
                        rec["arc"] = {"cx": p.get("cx"), "cy": p.get("cy"),
                                      "i": p.get("i"), "j": p.get("j"),
                                      "cw": p.get("cw")}
                    clines.append(rec)
                    all_x += [x1, x2]; all_y += [y1, y2]
                elif t == "flash":
                    x, y = p.get("x1"), p.get("y1")
                    if x is None or y is None:
                        continue
                    # 真实焊盘尺寸: 优先 shape/w/h, 退化到 width
                    w = p.get("w") or p.get("width") or 0.0
                    h = p.get("h") or p.get("width") or w
                    pad = {"refdes": p.get("refdes", "") or "", "pin": p.get("pin", "") or "",
                           "net": net, "x": x, "y": y, "layer": layer, "padstack": "",
                           "is_smd": False, "pad_w": w or 0.3, "pad_h": h or w or 0.3,
                           "drill_mm": 0,
                           "pad_shape": p.get("shape") or "circle",
                           "pad_rot": p.get("rot") or 0.0,
                           "polarity": pol}
                    if p.get("points"):
                        pad["points"] = p["points"]
                        pad["pad_shape"] = "poly"
                    if p.get("hole"):
                        pad["hole_mm"] = p["hole"]
                    if str(p.get("feat_type", "")).upper() == "TEST":
                        pad["is_testpoint"] = True
                    pads.append(pad)
                    pad_by_loc[(round(x, 2), round(y, 2))] = pad
                    all_x.append(x); all_y.append(y)
                    if net and pad["refdes"]:
                        net_nodes.setdefault(net, []).append(
                            {"refdes": pad["refdes"], "pin": pad["pin"],
                             "x": x, "y": y, "layer": layer})
                elif t == "region":
                    pts = p.get("points") or []
                    if len(pts) >= 3:
                        xs = [q[0] for q in pts]; ys = [q[1] for q in pts]
                        shapes.append({"net": net, "layer": layer, "polarity": pol,
                                       "points": [[q[0], q[1]] for q in pts],
                                       "holes": [[[q[0], q[1]] for q in hole]
                                                 for hole in (p.get("holes") or [])],
                                       "bbox": [min(xs), min(ys), max(xs), max(ys)]})
                        all_x += xs; all_y += ys
            # gerber/odb 自带 net->节点名 (无XY) 作为网表补充
            for nm, nodes in (d.get("nets") or {}).items():
                bucket = net_nodes.setdefault(nm, [])
                have = {f"{x['refdes']}.{x['pin']}" for x in bucket}
                for node in nodes:
                    if node not in have:
                        ref, _, pin = str(node).partition(".")
                        bucket.append({"refdes": ref, "pin": pin,
                                        "x": None, "y": None, "layer": ""})
            if d.get("components"):
                components.extend(d["components"])
            for note in d.get("notes") or []:
                if note not in parse_notes:
                    parse_notes.append(note)
        elif kind == "drill":
            for h in d.get("holes", []):
                if h.get("x") is None:
                    continue
                drill_holes.append(h)
                all_x.append(h["x"]); all_y.append(h["y"])
            for note in d.get("notes") or []:
                if note not in parse_notes:
                    parse_notes.append(note)

    # 钻孔 -> 过孔 (就近匹配 flash 取网络/焊盘尺寸); 槽孔保留起止点
    for h in drill_holes:
        x, y, dia = h.get("x"), h.get("y"), h.get("diameter_mm")
        pad = pad_by_loc.get((round(x, 2), round(y, 2)))
        via = {
            "net": (pad or {}).get("net", "") or "",
            "x": x, "y": y, "drill_mm": dia,
            "pad_mm": (pad or {}).get("pad_w"),
            "padstack": "", "start_layer": "", "end_layer": "",
            "type": "npth" if str(h.get("plating", "")).upper() == "NPTH" else "through",
            "is_slot": bool(h.get("is_slot")),
        }
        if h.get("is_slot") and h.get("x2") is not None:
            via["x2"] = h["x2"]
            via["y2"] = h["y2"]
            via["slot_length_mm"] = h.get("length_mm")
        vias.append(via)

    # 板框: 仅当检测到专门轮廓层时使用真实轮廓 (否则留空, 不做铜箔到板边检查,
    # 避免以铜箔外包框当板边导致全部误判为"到边0mm")
    outline = outline_pts

    nets = [{"name": nm, "nodes": [nd for nd in nodes if nd.get("x") is not None]}
            for nm, nodes in sorted(net_nodes.items())]

    # 测试点: ODB++ 带 .type=TEST 的焊盘 (Gerber 无此信息)
    testpoints = [{"net": p.get("net", ""), "refdes": p.get("refdes", ""),
                   "pin": p.get("pin", ""), "x": p["x"], "y": p["y"],
                   "layer": p.get("layer", ""),
                   "pad_mm": min(p.get("pad_w") or 0, p.get("pad_h") or 0) or None}
                  for p in pads if p.get("is_testpoint") and p.get("x") is not None]

    return {
        "format": "brd-dfm-export",
        "version": "1.0",
        "tool": "Gerber/ODB++/NC Drill",
        "source": "fabrication",
        "units": "mm",
        "source_units": "mm",
        "connectivity_source": "",            # 无 Allegro 连通性 -> 走几何开短路
        "etch_width_is_average": False,       # Gerber 线宽为逐段实测
        "board": {
            "name": "Fabrication",
            "revision": "",
            "thickness_mm": board_thickness,
            "layer_count": len(layers_seen),
        },
        "outline": outline,
        "stackup": [{"layer": l, "type": "signal"} for l in layers_seen],
        "nets": nets,
        "components": components,
        "clines": clines,
        "vias": vias,
        "pads": pads,
        "shapes": shapes,
        "testpoints": testpoints,
        "opens": [],
        "drc": [],
        "net_avg_width_mm": {},
        "parse_notes": parse_notes,
    }
