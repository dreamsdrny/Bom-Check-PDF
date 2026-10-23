"""判定覆盖率与数据质量 — 让"没检查"变得可见。

旧实现的问题: 能力库缺项、数据缺层、元素超限截断时, 对应规则**静默跳过**,
用户看到"未发现问题"却不知其实没判。本模块把三类信息显式化:

  * :class:`Coverage`    某检查判定了哪些规则、跳过了哪些规则及原因
  * :class:`DataQuality` 解析质量 (层数/图元数/负片/截断/未展开宏) + 判定覆盖率
  * :func:`quality_issues` 把关键缺口转成「低」级提示问题, 进入报告与界面

数据质量结论会写入 ``analysis["data_quality"]`` 与 ``analysis["coverage"]``。
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List

from dfm.core.issue import make_issue


@dataclass
class Coverage:
    """一个检查项的规则覆盖情况。"""

    check_id: str = ""
    evaluated: List[dict] = field(default_factory=list)
    skipped: List[dict] = field(default_factory=list)

    def judged(self, category: str, rule: str, limit: Any = None,
               source: str = "capability") -> None:
        self.evaluated.append({"category": category, "rule": rule,
                               "limit": limit, "source": source})

    def missed(self, category: str, rule: str, reason: str = "能力库无该项数据") -> None:
        self.skipped.append({"category": category, "rule": rule, "reason": reason})

    def miss_if_none(self, category: str, rule: str, limit: Any,
                     reason: str = "能力库无该项数据") -> bool:
        """limit 为 None 时记为"未判定"并返回 True (调用方据此跳过)。"""
        if limit is None:
            self.missed(category, rule, reason)
            return True
        self.judged(category, rule, limit)
        return False

    @property
    def ratio(self) -> float:
        total = len(self.evaluated) + len(self.skipped)
        return (len(self.evaluated) / total) if total else 1.0

    def as_dict(self) -> dict:
        return {
            "check": self.check_id,
            "judged_count": len(self.evaluated),
            "missed_count": len(self.skipped),
            "ratio": round(self.ratio, 3),
            "judged": [f"{e['category']}/{e['rule']}" for e in self.evaluated],
            "missed": [{"rule": f"{m['category']}/{m['rule']}", "reason": m["reason"]}
                       for m in self.skipped],
        }


@dataclass
class DataQuality:
    """一次分析的数据质量汇总。"""

    source_kind: str = ""
    parse_notes: List[str] = field(default_factory=list)
    geometry: Dict[str, Any] = field(default_factory=dict)
    polarity: Dict[str, Any] = field(default_factory=dict)
    coverage: List[dict] = field(default_factory=list)
    truncations: List[str] = field(default_factory=list)
    fatal: List[str] = field(default_factory=list)

    # ---------- 采集 ----------
    def add_geometry(self, geom) -> None:
        try:
            self.geometry = dict(geom.summary())
        except Exception:
            self.geometry = {}
        for w in getattr(geom, "warnings", []) or []:
            if w not in self.parse_notes:
                self.parse_notes.append(w)

    def add_notes(self, notes: Any) -> None:
        for n in notes or []:
            n = str(n)
            if n and n not in self.parse_notes:
                self.parse_notes.append(n)

    def add_coverage(self, cov: Coverage) -> None:
        if cov is not None:
            self.coverage.append(cov.as_dict())

    def add_truncation(self, text: str) -> None:
        if text and text not in self.truncations:
            self.truncations.append(text)

    # ---------- 输出 ----------
    def missed_rules(self) -> List[dict]:
        out: List[dict] = []
        for c in self.coverage:
            out.extend(c.get("missed") or [])
        return out

    def judged_ratio(self) -> float:
        judged = sum(c.get("judged_count", 0) for c in self.coverage)
        missed = sum(c.get("missed_count", 0) for c in self.coverage)
        total = judged + missed
        return (judged / total) if total else 1.0

    def as_dict(self) -> dict:
        geo = dict(self.geometry)
        return {
            "数据源": self.source_kind or "-",
            "解析提示数": len(self.parse_notes),
            "解析提示": self.parse_notes[:40],
            "负片(clear)图元": geo.pop("负片(clear)图元数", 0),
            "被挖除铜箔": (geo.pop("完全挖除的铜箔", 0), geo.pop("部分挖除的铜箔", 0)),
            "几何统计": geo,
            "规则判定覆盖率": f"{self.judged_ratio() * 100:.0f}%",
            "未判定规则数": len(self.missed_rules()),
            "未判定规则": [m["rule"] for m in self.missed_rules()][:60],
            "截断": self.truncations,
            "致命问题": self.fatal,
        }

    def summary_line(self) -> str:
        return (f"判定覆盖率 {self.judged_ratio() * 100:.0f}% "
                f"(未判定 {len(self.missed_rules())} 项规则), "
                f"解析提示 {len(self.parse_notes)} 条"
                + (f", 截断 {len(self.truncations)} 处" if self.truncations else ""))


def quality_issues(dq: DataQuality, warn_on_missing: bool = True) -> List[dict]:
    """把数据质量缺口转成「低」级提示问题 (进入报告与问题清单)。

    * 有未判定规则 → 一条汇总提示 (列出前若干项)
    * 有截断 → 一条提示 (结论可能不完整)
    * 有解析提示且含"无法/未支持/负片" → 一条提示
    """
    out: List[dict] = []
    if warn_on_missing:
        missed = dq.missed_rules()
        if missed:
            names = ", ".join(m["rule"] for m in missed[:12])
            more = f" 等 {len(missed)} 项" if len(missed) > 12 else ""
            out.append(make_issue(
                code="DQ-01", category="制造数据", rule="部分规则未判定", severity="低",
                description=(f"因能力库缺项或数据不足, 以下规则本次**未做判定**: {names}{more}。"
                             f"「未发现问题」不等于「已判定合格」"),
                measured=len(missed), required=0, unit="项",
                suggestion="在 rules/capability/<板厂>.yaml 中补齐对应能力项, "
                           "或改用其它板厂/等级重新分析"))
    if dq.truncations:
        out.append(make_issue(
            code="DQ-02", category="制造数据", rule="数据量超限截断", severity="中",
            description="; ".join(dq.truncations[:5]),
            measured=len(dq.truncations), required=0, unit="处",
            suggestion="结论可能不完整: 建议分层/分区域分析, 或提高 "
                       "dfm.geom.board.MAX_ELEMENTS 上限后重跑"))
    risky = [n for n in dq.parse_notes
             if any(k in n for k in ("无法", "未支持", "未展开", "负片", "未定义", "截断"))]
    if risky:
        out.append(make_issue(
            code="DQ-03", category="制造数据", rule="解析受限", severity="低",
            description=f"解析阶段存在 {len(risky)} 条受限提示, 示例: {risky[0][:120]}",
            measured=len(risky), required=0, unit="条",
            suggestion="详见报告「数据质量」段; 必要时改用 ODB++ 或带 X2 属性的 Gerber"))
    return out
