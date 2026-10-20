"""用户可配置规则集 (rules/checks.yaml) — 自定义规则引擎的配置层。

三层可定制能力 (由浅入深):

1. **开关与严重度**: 任意规则可停用、可改严重度/建议文案, 不改代码。
2. **阈值覆盖**: 对"实测值 vs 要求值"型规则, 用 ``min`` / ``max`` 覆盖能力库数值,
   实现比板厂能力更严的客户标准 (例如客户强制线宽 ≥ 0.10mm)。
3. **声明式自定义规则**: ``custom_rules`` 中对设计参数做比较运算即可新增规则,
   无需写 Python; 需要几何级判定再写插件 (见 :mod:`dfm.core.registry`)。

配置文件搜索顺序见 :mod:`dfm.paths`; 未找到时使用 :data:`DEFAULT_CONFIG`。
"""
from __future__ import annotations

import copy
import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

import yaml

from dfm import paths
from dfm.core.issue import SEVERITY_LEVELS, SEVERITY_ORDER, SEVERITY_WEIGHT, make_issue

CONFIG_FILENAME = "checks.yaml"

DEFAULT_CONFIG: Dict[str, Any] = {
    "version": 1,
    "limit_warn_ratio": 0.8,
    "scoring": {
        "base": 100.0,
        "cap_penalty": 100.0,
        "weights": dict(SEVERITY_WEIGHT),
    },
    "checks": {
        "capability": {"enabled": True},
        "fabrication": {"enabled": True},
        "assembly": {"enabled": True},
        "netlist": {"enabled": True},
        "fabdata": {"enabled": True},
        "fabnet": {"enabled": True},
        "panelization": {"enabled": True},
        "netclass": {"enabled": True},
    },
    "assembly": {
        "min_component_spacing": 0.5,
        "component_to_edge": 2.0,
        "component_to_edge_severity": "中",
        "fiducial_min": 2,
        "fiducial_diameter": 1.0,
        "fiducial_names": ["FID", "MARK", "FP", "BENCHMARK"],
        "test_pad_diameter": 0.8,
        "test_pad_to_edge": 1.27,
        "test_coverage_min": 0.0,
        "chip_pad_symmetry_ratio": 0.8,
        "bga_pitch_min": 0.4,
        "bga_pad_annular": 0.05,
        "via_in_pad_allowed": False,
        "min_silk_to_pad": 0.05,
        "polarity_required": True,
    },
    "panelization": {
        "process_edge": 3.0,
        "mark_count": 3,
        "mark_to_edge": 3.5,
        "tooling_hole_count": 2,
        "min_utilization": 0.7,
    },
    "rules": {},
    "custom_rules": [],
}

_OPERATORS = {
    "lt": lambda a, b: a < b,
    "le": lambda a, b: a <= b,
    "gt": lambda a, b: a > b,
    "ge": lambda a, b: a >= b,
    "eq": lambda a, b: a == b,
    "ne": lambda a, b: a != b,
}


@dataclass
class RuleOverride:
    """单条规则的覆盖配置。"""

    name: str
    enabled: bool = True
    severity: Optional[str] = None
    min: Optional[float] = None
    max: Optional[float] = None
    warn_ratio: Optional[float] = None
    suggestion: Optional[str] = None
    message: Optional[str] = None
    raw: Dict[str, Any] = field(default_factory=dict)

    @classmethod
    def from_dict(cls, name: str, data: Any) -> "RuleOverride":
        data = data if isinstance(data, dict) else {}
        sev = data.get("severity")
        if sev is not None and sev not in SEVERITY_ORDER:
            sev = None
        return cls(
            name=str(name),
            enabled=bool(data.get("enabled", True)),
            severity=sev,
            min=_float(data.get("min")),
            max=_float(data.get("max")),
            warn_ratio=_float(data.get("warn_ratio")),
            suggestion=data.get("suggestion"),
            message=data.get("message"),
            raw=dict(data),
        )


def _float(v) -> Optional[float]:
    if v is None or isinstance(v, bool):
        return None
    try:
        return float(v)
    except (TypeError, ValueError):
        return None


def _merge(base: Dict[str, Any], over: Dict[str, Any]) -> Dict[str, Any]:
    out = copy.deepcopy(base)
    for k, v in (over or {}).items():
        if isinstance(v, dict) and isinstance(out.get(k), dict):
            out[k] = _merge(out[k], v)
        else:
            out[k] = v
    return out


class RulesConfig:
    """规则集配置 (只读视图 + 覆盖应用)。"""

    def __init__(self, data: Optional[Dict[str, Any]] = None, source: str = ""):
        self.data = _merge(DEFAULT_CONFIG, data or {})
        self.source = source
        self._rule_cache: Dict[str, RuleOverride] = {}

    # ---------- 加载 ----------
    @classmethod
    def load(cls, path: Optional[str] = None) -> "RulesConfig":
        """加载规则集; path 为空时按搜索路径查找 checks.yaml。"""
        if path:
            candidates = [path]
        else:
            candidates = [os.path.join(d, CONFIG_FILENAME) for d in paths.rules_dirs()]
        for c in candidates:
            if c and os.path.isfile(c):
                try:
                    with open(c, "r", encoding="utf-8") as f:
                        return cls(yaml.safe_load(f) or {}, source=c)
                except Exception:  # noqa: BLE001
                    continue
        return cls({}, source="")

    def save(self, path: str) -> str:
        os.makedirs(os.path.dirname(os.path.abspath(path)), exist_ok=True)
        with open(path, "w", encoding="utf-8") as f:
            yaml.safe_dump(self.data, f, allow_unicode=True, sort_keys=False)
        return path

    def reload(self) -> "RulesConfig":
        return RulesConfig.load(self.source or None)

    # ---------- 查询 ----------
    def check_enabled(self, check_id: str) -> bool:
        return bool((self.data.get("checks", {}).get(check_id) or {}).get("enabled", True))

    def check_setting(self, check_id: str, key: str, default=None):
        return (self.data.get("checks", {}).get(check_id) or {}).get(key, default)

    def param(self, group: str, key: str, default=None):
        """读取参数组配置, 如 param('assembly', 'min_component_spacing', 0.5)。"""
        val = (self.data.get(group) or {}).get(key, None)
        return default if val is None else val

    def group(self, name: str) -> Dict[str, Any]:
        return dict(self.data.get(name) or {})

    def limit_warn_ratio(self) -> float:
        return float(self.data.get("limit_warn_ratio") or 0.8)

    def scoring(self) -> Dict[str, Any]:
        s = dict(self.data.get("scoring") or {})
        s.setdefault("base", 100.0)
        s.setdefault("cap_penalty", 100.0)
        s.setdefault("weights", dict(SEVERITY_WEIGHT))
        return s

    def rule(self, name: str) -> Optional[RuleOverride]:
        if name in self._rule_cache:
            return self._rule_cache[name]
        raw = (self.data.get("rules") or {}).get(name)
        if raw is None:
            return None
        ov = RuleOverride.from_dict(name, raw)
        self._rule_cache[name] = ov
        return ov

    def custom_rules(self) -> List[dict]:
        return list(self.data.get("custom_rules") or [])

    def describe(self) -> Dict[str, Any]:
        """供界面展示的配置摘要。"""
        return {
            "source": self.source or "(内置默认)",
            "checks": {k: bool((v or {}).get("enabled", True))
                       for k, v in (self.data.get("checks") or {}).items()},
            "rule_overrides": len(self.data.get("rules") or {}),
            "custom_rules": len(self.data.get("custom_rules") or []),
            "assembly": self.group("assembly"),
            "panelization": self.group("panelization"),
        }

    # ---------- 应用 ----------
    def apply(self, issues: List[dict]) -> List[dict]:
        """按规则集过滤 / 改判 / 改写问题清单。"""
        out: List[dict] = []
        for it in issues:
            ov = self.rule(it.get("rule", ""))
            if ov is None:
                out.append(it)
                continue
            if not ov.enabled:
                continue
            it = dict(it)
            verdict = self._rejudge(it, ov)
            if verdict == "ok":
                continue
            if ov.severity and verdict == "fail":
                it["severity"] = ov.severity
            if ov.suggestion:
                it["suggestion"] = ov.suggestion
            if ov.message:
                it["description"] = ov.message
            out.append(it)
        return out

    def _rejudge(self, issue: dict, ov: RuleOverride) -> str:
        """阈值覆盖重判, 返回 ``fail`` / ``warn`` / ``ok``。

        未配置 min/max 时一律返回 ``fail`` (保留原判定, 仅套用严重度/文案覆盖)。
        """
        measured = _float(issue.get("measured"))
        if measured is None or (ov.min is None and ov.max is None):
            return "fail"
        ratio = ov.warn_ratio if ov.warn_ratio is not None else self.limit_warn_ratio()
        warn = self._downgrade(issue.get("severity", "中"))
        if ov.min is not None:
            if measured < ov.min:
                issue["required"] = ov.min
                return "fail"
            if measured < ov.min * max(ratio, 1.0):
                issue["required"] = ov.min
                issue["severity"] = warn
                return "warn"
            return "ok"
        if ov.max is not None:
            if measured > ov.max:
                issue["required"] = ov.max
                return "fail"
            if measured > ov.max * min(ratio, 1.0):
                issue["required"] = ov.max
                issue["severity"] = warn
                return "warn"
            return "ok"
        return "fail"

    @staticmethod
    def _downgrade(severity: str) -> str:
        idx = SEVERITY_ORDER.get(severity, 2)
        return SEVERITY_LEVELS[min(idx + 1, len(SEVERITY_LEVELS) - 1)]


# ============================================================
# 声明式自定义规则求值
# ============================================================
def _compare(op: str, value: Any, target: Any) -> bool:
    op = str(op or "ge").lower()
    if op == "between":
        lo, hi = (list(target) + [None, None])[:2]
        return _float(value) is not None and float(lo) <= float(value) <= float(hi)
    if op == "outside":
        lo, hi = (list(target) + [None, None])[:2]
        return _float(value) is not None and not (float(lo) <= float(value) <= float(hi))
    if op == "in":
        return value in (target or [])
    if op == "not_in":
        return value not in (target or [])
    if op == "missing":
        return value in (None, "", [])
    if op == "false":
        return not value
    if op == "true":
        return bool(value)
    fn = _OPERATORS.get(op)
    if fn is None:
        raise ValueError(f"未知比较运算符: {op}")
    a, b = _float(value), _float(target)
    if a is None or b is None:
        return str(value) == str(target) if op in ("eq", "ne") else False
    return fn(a, b)


def _violates(op: str, value: Any, target: Any) -> bool:
    """判定是否**违反**规则。

    ``op`` 表示"要求满足的条件"; 特殊三个运算符按直觉语义处理:
      missing → 参数缺失即违反; true → 参数为假即违反; false → 参数为真即违反
    """
    op = str(op or "ge").lower()
    if op == "missing":
        return value in (None, "", [], {})
    if op == "true":
        return not bool(value)
    if op == "false":
        return bool(value)
    return not _compare(op, value, target)


def evaluate_custom_rules(params: Dict[str, Any],
                          rules: Optional[List[dict]] = None,
                          config: Optional[RulesConfig] = None) -> List[dict]:
    """对设计参数执行声明式自定义规则, 产出标准 Issue 列表。

    规则字段::

        id: MY-01              问题代码 (可省略, 自动生成 CUS-xx)
        name: 客户线宽要求      规则名 (显示在报告)
        category: 线路          类别
        param: min_line_width   设计参数键
        op: ge|le|gt|lt|eq|ne|between|outside|in|not_in|missing|false|true
        value: 0.1              比较目标 (between/outside/in 用列表)
        severity: 危险|高|中|低
        unit: mm
        message: 描述 (支持 {value} / {target} 占位)
        suggestion: 建议
        enabled: true
    """
    rules = list(rules if rules is not None else
                 (config.custom_rules() if config else []))
    issues: List[dict] = []
    for i, r in enumerate(rules, start=1):
        if not isinstance(r, dict) or not r.get("enabled", True):
            continue
        key = r.get("param")
        if not key:
            continue
        value = params.get(key) if isinstance(params, dict) else None
        try:
            violated = _violates(r.get("op", "ge"), value, r.get("value"))
        except Exception:  # noqa: BLE001
            continue
        if not violated:
            continue
        msg = r.get("message") or f"{r.get('name', key)}: 实测 {value} 不满足要求 {r.get('value')}"
        msg = str(msg).replace("{value}", str(value)).replace("{target}", str(r.get("value")))
        issue = make_issue(
            code=str(r.get("id") or f"CUS-{i:02d}"),
            category=str(r.get("category") or "自定义"),
            rule=str(r.get("name") or key),
            severity=str(r.get("severity") or "中"),
            description=msg,
            suggestion=str(r.get("suggestion") or ""),
            measured=value,
            required=r.get("value"),
            unit=str(r.get("unit") or ""),
            note=str(r.get("note") or ""),
        )
        issue["keep_code"] = True        # 用户自定义代码不被统一重编覆盖
        issues.append(issue)
    return issues


def default_config_path() -> str:
    """用户规则集应存放的位置 (%APPDATA%/EvanDFM/rules/checks.yaml)。"""
    return os.path.join(paths.user_data_dir(), "rules", CONFIG_FILENAME)


def template_text() -> str:
    """生成带注释的规则集模板 (界面『导出模板』用)。"""
    header = (
        "# Evan DFM 规则集配置 (checks.yaml)\n"
        "# 放置位置: 程序目录/rules/checks.yaml 或 %APPDATA%/EvanDFM/rules/checks.yaml\n"
        "# 同名文件按优先级覆盖, 修改后在界面点『重新加载规则库』即可生效。\n\n"
    )
    sample = copy.deepcopy(DEFAULT_CONFIG)
    sample["rules"] = {
        "最小线宽": {
            "enabled": True,
            "severity": "危险",
            "min": 0.1,
            "warn_ratio": 1.1,
            "suggestion": "客户要求最小线宽 0.10mm, 请加宽线路或更换板厂",
        },
    }
    sample["custom_rules"] = [{
        "id": "CUS-01",
        "name": "板厚必须为 1.6mm",
        "category": "板厚",
        "param": "board_thickness",
        "op": "eq",
        "value": 1.6,
        "severity": "高",
        "unit": "mm",
        "message": "板厚 {value}mm 不符合客户标准 1.6mm",
        "suggestion": "修改叠层板厚为 1.6mm",
    }]
    return header + yaml.safe_dump(sample, allow_unicode=True, sort_keys=False)
