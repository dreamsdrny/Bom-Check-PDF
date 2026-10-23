"""检查项注册表 (Check Registry) — 插件化 DFM 检查的核心。

内置检查与第三方插件检查使用同一套注册机制, 编排器
:func:`dfm.core.pipeline.analyze` 按 ``order`` 依次执行, 并按 ``requires``
自动跳过数据不满足的检查。

二次开发一个几何级检查只需三步::

    # plugins/my_check.py
    from dfm.core.registry import register_check
    from dfm.core.issue import make_issue

    @register_check(id="my_copper_balance", name="铜箔平衡度",
                    group="制造 DFM", requires=("design",), order=80)
    def run(ctx):
        top = sum(1 for c in ctx.design.clines if c["layer"] == "TOP")
        bot = sum(1 for c in ctx.design.clines if c["layer"] == "BOTTOM")
        if top and bot and abs(top - bot) / max(top, bot) > 0.5:
            return [make_issue(code="MY-01", category="压合", rule="铜箔平衡度",
                               severity="中",
                               description=f"上下层走线数差异过大 TOP={top} BOTTOM={bot}",
                               measured=abs(top - bot), required=0, unit="条")]
        return []

把文件放入 ``plugins/`` 目录 (或 ``%APPDATA%/EvanDFM/plugins/``) 后重启即可,
界面「规则库」页会显示插件来源, 并允许整体停用。
"""
from __future__ import annotations

import importlib.util
import logging
import os
import sys
from dataclasses import dataclass
from typing import Any, Callable, Dict, Iterable, List, Optional, Set, Tuple

from dfm import paths
from dfm.core.issue import make_issue

logger = logging.getLogger(__name__)

# requires 可选值
REQ_DESIGN = "design"      # 需要归一化几何模型 BrdDesign
REQ_FAB = "fab"            # 需要制造数据几何 fab_data
REQ_PARAMS = "params"      # 需要设计参数字典
REQ_CAD = "cad"            # 需要 CAD 网表
REQ_CAPABILITY = "capability"
VALID_REQUIRES = {REQ_DESIGN, REQ_FAB, REQ_PARAMS, REQ_CAD, REQ_CAPABILITY}


class CheckContext:
    """一次分析中传给每个检查的上下文 (只读数据 + 输出收集)。"""

    def __init__(self, *, params: Optional[dict] = None, design: Any = None,
                 capability: Optional[dict] = None, fab_data: Optional[List[dict]] = None,
                 fab_stats: Optional[dict] = None, cad_nets: Optional[dict] = None,
                 fab_nets: Optional[dict] = None,
                 rules: Any = None, options: Optional[dict] = None,
                 progress: Optional[Callable[[str], None]] = None):
        self.params: Dict[str, Any] = dict(params or {})
        self.design = design
        self.capability: Dict[str, Any] = dict(capability or {})
        self.fab_data: List[dict] = list(fab_data or [])
        self.fab_stats: Dict[str, Any] = dict(fab_stats or {})
        self.cad_nets: Dict[str, Any] = dict(cad_nets or {})
        #: 制造数据侧网表 {net: {refdes.pin, ...}} (Gerber X2 / ODB++ / 几何派生)
        self.fab_nets: Dict[str, Any] = dict(fab_nets or {})
        self.rules = rules
        self.options: Dict[str, Any] = dict(options or {})
        self._progress = progress
        #: 各检查写入的附加统计 (会合并进 analysis 结果)
        self.extras: Dict[str, Any] = {}
        self.issues: List[dict] = []
        self._geometry = None
        self.coverage: List[Any] = []
        self.truncations: List[str] = []

    # ---- 共享几何模型 (构建一次, 多检查复用) ----
    @property
    def geometry(self):
        """共享的 :class:`dfm.geom.board.BoardGeometry` (惰性构建并缓存)。"""
        if self._geometry is None:
            from dfm.geom.board import BoardGeometry

            self._geometry = BoardGeometry(design=self.design, fab_data=self.fab_data)
        return self._geometry

    def set_geometry(self, geom) -> None:
        self._geometry = geom

    def set_coverage(self, cov) -> None:
        """记录本检查的规则覆盖情况 (供数据质量报告)。"""
        if cov is not None:
            self.coverage.append(cov)

    def add_truncation(self, text: str) -> None:
        """记录"实例留存被截断"等信息 (处数仍按真实计数报告)。"""
        if text and text not in self.truncations:
            self.truncations.append(text)

    # ---- 便捷访问 ----
    @property
    def factory(self) -> str:
        return str(self.options.get("factory") or "")

    @property
    def board_type(self) -> str:
        return str(self.options.get("board_type") or "rigid")

    @property
    def level(self) -> str:
        return str(self.options.get("capability_level") or "standard")

    @property
    def copper_um(self) -> float:
        return float(self.options.get("copper_um") or 35.0)

    @property
    def copper_oz(self) -> float:
        return float(self.options.get("copper_oz") or 1.0)

    @property
    def board_thickness(self) -> Optional[float]:
        v = self.options.get("board_thickness")
        if v is None and self.design is not None:
            v = getattr(self.design, "thickness_mm", None)
        try:
            return None if v is None else float(v)
        except (TypeError, ValueError):
            return None

    def param(self, key: str, default=None):
        v = self.params.get(key)
        return default if v in (None, "") else v

    def rule_param(self, group: str, key: str, default=None):
        """读取规则集 (checks.yaml) 中的参数组配置。"""
        if self.rules is None:
            return default
        return self.rules.param(group, key, default)

    def note(self, msg: str) -> None:
        if self._progress:
            try:
                self._progress(msg)
            except Exception:
                pass

    def add(self, issue: dict) -> dict:
        self.issues.append(issue)
        return issue

    def add_many(self, issues: Iterable[dict]) -> None:
        self.issues.extend(issues)

    def issue(self, **kwargs) -> dict:
        """构造并入队一个 Issue (字段见 :func:`dfm.core.issue.make_issue`)。"""
        return self.add(make_issue(**kwargs))

    def available(self) -> Set[str]:
        have: Set[str] = set()
        if self.design is not None:
            have.add(REQ_DESIGN)
        if self.fab_data:
            have.add(REQ_FAB)
        if self.params:
            have.add(REQ_PARAMS)
        if self.cad_nets:
            have.add(REQ_CAD)
        if self.capability:
            have.add(REQ_CAPABILITY)
        return have


@dataclass
class CheckSpec:
    """一个检查项的元数据与执行体。"""

    id: str
    name: str
    run: Callable[[CheckContext], Optional[List[dict]]]
    group: str = "制造 DFM"
    description: str = ""
    requires: Tuple[str, ...] = ()
    order: int = 100
    source: str = "builtin"
    enabled: bool = True

    def missing_requires(self, ctx: CheckContext) -> List[str]:
        have = ctx.available()
        return [r for r in self.requires if r not in have]

    def to_dict(self) -> dict:
        return {
            "id": self.id, "name": self.name, "group": self.group,
            "description": self.description, "requires": list(self.requires),
            "order": self.order, "source": self.source, "enabled": self.enabled,
        }


CHECKS: Dict[str, CheckSpec] = {}


def register_check(id: str, name: str, group: str = "制造 DFM", description: str = "",
                   requires: Iterable[str] = (), order: int = 100,
                   source: str = "builtin",
                   func: Optional[Callable] = None) -> Callable:
    """注册检查项 (可作装饰器使用)。

    :param id: 唯一标识 (界面/配置/命令行均用它引用)
    :param requires: 依赖的数据 (design / fab / params / cad / capability)
    :param order: 执行顺序, 小者先执行
    """
    reqs = tuple(r for r in requires if r in VALID_REQUIRES)
    unknown = [r for r in requires if r not in VALID_REQUIRES]
    if unknown:
        raise ValueError(f"检查 {id} 声明了未知依赖: {unknown} (可用: {sorted(VALID_REQUIRES)})")

    def decorator(fn: Callable) -> Callable:
        if id in CHECKS:
            logger.warning("检查项 %s 已存在, 被 %s 覆盖", id, source)
        CHECKS[id] = CheckSpec(id=id, name=name, run=fn, group=group,
                               description=description, requires=reqs,
                               order=order, source=source)
        return fn

    if func is not None:
        return decorator(func)
    return decorator


def unregister_check(id: str) -> bool:
    return CHECKS.pop(id, None) is not None


def get_check(id: str) -> Optional[CheckSpec]:
    return CHECKS.get(id)


def list_checks() -> List[CheckSpec]:
    """全部已注册检查 (按 order, id 排序)。"""
    return sorted(CHECKS.values(), key=lambda c: (c.order, c.id))


def builtin_checks() -> List[CheckSpec]:
    return [c for c in list_checks() if c.source == "builtin"]

#定义plugin_checks函数，返回所有非内置检查项
def plugin_checks() -> List[CheckSpec]:
    return [c for c in list_checks() if c.source != "builtin"]


def load_builtin_checks(force: bool = False) -> None:
    """导入内置检查模块以完成注册 (幂等)。"""
    modules = [
        "dfm.checks.capability",
        "dfm.checks.fabrication",
        "dfm.checks.assembly",
        "dfm.checks.netlist",
        "dfm.checks.fabdata",
        "dfm.checks.fabnet",
        "dfm.checks.panelization",
        "dfm.checks.netclass",
    ]
    for name in modules:
        if not force and name in sys.modules:
            continue
        try:
            importlib.import_module(name)
        except Exception as exc:
            logger.exception("内置检查模块加载失败: %s (%s)", name, exc)


_PLUGIN_CHECKS: Dict[str, List[str]] = {}


def load_plugins(dirs: Optional[Iterable[str]] = None,
                 force: bool = False) -> List[Dict[str, Any]]:
    """扫描插件目录并加载 (幂等)。

    支持两种形态:
      * ``plugins/xxx.py``            单文件插件
      * ``plugins/xxx/__init__.py``   包插件

    返回加载报告 ``[{name, path, status, checks, error}]``, 供界面展示。
    """

    load_builtin_checks()
    report: List[Dict[str, Any]] = []
    search = list(dirs) if dirs else paths.plugins_dirs()
    for d in search:
        if not os.path.isdir(d):
            continue
        for entry in sorted(os.listdir(d)):
            full = os.path.join(d, entry)
            if entry.startswith(("_", ".")):
                continue
            if os.path.isfile(full) and entry.endswith(".py"):
                report.append(_load_plugin_file(full, entry[:-3], force))
            elif os.path.isdir(full) and os.path.isfile(os.path.join(full, "__init__.py")):
                report.append(_load_plugin_file(os.path.join(full, "__init__.py"), entry, force))
    return report


def _load_plugin_file(path: str, name: str, force: bool) -> Dict[str, Any]:
    mod_name = f"dfm_plugin_{name}"
    if mod_name in sys.modules and not force:
        return {"name": name, "path": path, "status": "cached",
                "checks": _PLUGIN_CHECKS.get(path, []), "error": ""}
    before = set(CHECKS)
    try:
        spec = importlib.util.spec_from_file_location(mod_name, path)
        if spec is None or spec.loader is None:
            raise ImportError("无法创建模块 spec")
        module = importlib.util.module_from_spec(spec)
        sys.modules[mod_name] = module
        spec.loader.exec_module(module)
        if hasattr(module, "register"):
            module.register()
    except Exception as exc:
        sys.modules.pop(mod_name, None)
        logger.exception("插件加载失败: %s", path)
        return {"name": name, "path": path, "status": "error", "checks": [],
                "error": str(exc)}
    added = sorted(set(CHECKS) - before)
    for cid in added:
        CHECKS[cid].source = path
    _PLUGIN_CHECKS[path] = added
    return {"name": name, "path": path, "status": "ok", "checks": added, "error": ""}


def loaded_plugins() -> Dict[str, List[str]]:
    """{插件文件路径: 该插件注册的检查 id 列表}"""
    return dict(_PLUGIN_CHECKS)


def reset() -> None:
    """清空注册表 (测试用)。"""
    CHECKS.clear()
    _PLUGIN_CHECKS.clear()
