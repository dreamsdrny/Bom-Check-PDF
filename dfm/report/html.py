"""交互式 HTML 报告 (单文件, 无外网依赖, 适合工厂内网分发)。

包含: 评分仪表盘 / 分级统计 / 可过滤问题清单 / 内嵌版图与问题标记双向定位 /
Info 统计 / 网表与网络分类 / 检查执行清单 / 规则集与插件来源。

用法::

    from dfm.report import export_report
    export_report(analysis, "out/report.html")
"""
from __future__ import annotations

import html as _html
import json
import os
from datetime import datetime
from typing import Any, Dict, List, Optional

from dfm.core.issue import SEVERITY_LEVELS
from dfm.report.svg import render_board_svg

SEV_CSS = {
    "危险": "#e5484d",
    "高": "#f76808",
    "中": "#ffc53d",
    "低": "#46a758",
    "关闭": "#8b8d98",
}


def _esc(v: Any) -> str:
    return _html.escape("" if v is None else str(v))


def _fmt(v: Any) -> str:
    if v is None:
        return "—"
    if isinstance(v, float):
        return f"{v:.4g}"
    return str(v)


def _kv_table(title: str, data: Dict[str, Any]) -> str:
    if not data:
        return ""
    rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(_fmt(v))}</td></tr>"
                   for k, v in data.items() if v not in (None, ""))
    if not rows:
        return ""
    return f"<section class='card'><h3>{_esc(title)}</h3><table class='kv'>{rows}</table></section>"


def _donut(summary: Dict[str, int]) -> str:
    """分级占比环形图 (纯 SVG)"""
    total = int(summary.get("总计") or 0)
    if not total:
        return ("<svg viewBox='0 0 42 42' class='donut'><circle cx='21' cy='21' r='15.9' "
                "fill='none' stroke='#2a2f36' stroke-width='6'/></svg>")
    segs: List[str] = []
    offset = 25.0
    for sev in SEVERITY_LEVELS:
        n = int(summary.get(sev) or 0)
        if not n:
            continue
        pct = n * 100.0 / total
        segs.append(
            f"<circle cx='21' cy='21' r='15.9' fill='none' stroke='{SEV_CSS.get(sev, '#888')}' "
            f"stroke-width='6' stroke-dasharray='{pct:.3f} {100 - pct:.3f}' "
            f"stroke-dashoffset='{offset:.3f}'><title>{sev}: {n}</title></circle>")
        offset = (offset - pct) % 100
    return ("<svg viewBox='0 0 42 42' class='donut'>"
            "<circle cx='21' cy='21' r='15.9' fill='none' stroke='#2a2f36' stroke-width='6'/>"
            + "".join(segs) +
            f"<text x='21' y='20.4' class='donut-num'>{total}</text>"
            f"<text x='21' y='26' class='donut-lbl'>问题</text></svg>")


def _gauge(score: float, risk: str) -> str:
    """评分半环仪表"""
    score = max(0.0, min(100.0, float(score or 0)))
    color = {"危险": SEV_CSS["危险"], "高": SEV_CSS["高"],
             "中": SEV_CSS["中"], "低": SEV_CSS["低"]}.get(risk, "#46a758")
    dash = score * 2.513  # 半圆周长 ≈ 251.3 (r=40) * score/100 * ...
    return (
        "<svg viewBox='0 0 120 70' class='gauge'>"
        "<path d='M10 60 A50 50 0 0 1 110 60' fill='none' stroke='#2a2f36' stroke-width='10'/>"
        f"<path d='M10 60 A50 50 0 0 1 110 60' fill='none' stroke='{color}' stroke-width='10' "
        f"stroke-linecap='round' stroke-dasharray='{dash:.1f} 999'/>"
        f"<text x='60' y='52' class='gauge-num' fill='{color}'>{score:.1f}</text>"
        "<text x='60' y='66' class='gauge-lbl'>/ 100</text></svg>")


def _quality_section(analysis: Dict[str, Any]) -> str:
    """数据质量与判定覆盖率区块 — 让「没检查」显式可见。"""
    dq = analysis.get("data_quality") or {}
    coverage = analysis.get("coverage") or []
    if not dq and not coverage:
        return ""
    missed = dq.get("未判定规则") or []
    notes = dq.get("解析提示") or []
    trunc = dq.get("截断") or []
    fatal = dq.get("致命问题") or []
    geo = dq.get("几何统计") or {}

    cov_rows = "".join(
        "<tr><td>{c}</td><td class='c'>{j}</td><td class='c'>{m}</td>"
        "<td class='c'><span class='bar'><i style='width:{p:.0f}%'></i></span>{p:.0f}%</td></tr>".format(
            c=_esc(x.get("check", "")), j=x.get("judged_count", 0),
            m=x.get("missed_count", 0), p=float(x.get("ratio", 0)) * 100)
        for x in coverage)

    geo_rows = "".join(f"<tr><th>{_esc(k)}</th><td>{_esc(_fmt(v))}</td></tr>"
                       for k, v in geo.items() if v not in (None, ""))
    note_rows = "".join(f"<li>{_esc(n)}</li>" for n in notes[:30])
    miss_rows = "".join(
        f"<li><b>{_esc(m.get('rule', '') if isinstance(m, dict) else m)}</b>"
        + (f" — {_esc(m.get('reason', ''))}" if isinstance(m, dict) and m.get("reason") else "")
        + "</li>"
        for m in missed[:40])
    trunc_rows = "".join(f"<li>{_esc(t)}</li>" for t in trunc[:10])
    fatal_rows = "".join(f"<li class='bad'>{_esc(t)}</li>" for t in fatal[:10])

    warn = ""
    if missed:
        warn = ("<div class='qwarn'>⚠ 本次有 <b>%d</b> 项规则<b>未做判定</b>"
                "（能力库缺项或数据不足）。「未发现问题」不等于「已判定合格」。</div>"
                % len(missed))
    return f"""
<section class="card">
  <h3>数据质量与判定覆盖率 <small>解析是否有损、规则是否真的判过</small></h3>
  {warn}
  <div class="qgrid">
    <div>
      <table class="kv"><tr><th>规则判定覆盖率</th>
        <td><b>{_esc(dq.get('规则判定覆盖率', '—'))}</b></td></tr>
      <tr><th>数据源</th><td>{_esc(dq.get('数据源', '—'))}</td></tr>
      <tr><th>负片(clear)图元</th><td>{_esc(_fmt(dq.get('负片(clear)图元')))}</td></tr>
      <tr><th>被挖除铜箔 (完全/部分)</th><td>{_esc(dq.get('被挖除铜箔'))}</td></tr>
      <tr><th>解析提示</th><td>{_esc(len(notes))} 条</td></tr>
      <tr><th>截断</th><td>{_esc(len(trunc))} 处</td></tr>
      </table>
      {'<table class="kv">' + geo_rows + '</table>' if geo_rows else ''}
    </div>
    <div>
      {'<table class="issues"><thead><tr><th>检查</th><th>已判定</th><th>未判定</th><th>覆盖率</th></tr></thead><tbody>' + cov_rows + '</tbody></table>' if cov_rows else ''}
      {'<h4>未判定规则</h4><ul class="notes">' + miss_rows + '</ul>' if miss_rows else ''}
      {'<h4>解析提示</h4><ul class="notes">' + note_rows + '</ul>' if note_rows else ''}
      {'<h4>截断</h4><ul class="notes">' + trunc_rows + '</ul>' if trunc_rows else ''}
      {'<h4>致命问题</h4><ul class="notes">' + fatal_rows + '</ul>' if fatal_rows else ''}
    </div>
  </div>
</section>"""



def generate_dfm_html(analysis: Dict[str, Any], output_path: str,
                      board_meta: Optional[dict] = None,
                      embed_svg: bool = True) -> str:
    """生成单文件 HTML 报告, 返回输出路径。"""
    from dfm import __version__
    from dfm.branding import APP_NAME

    board_meta = dict(board_meta or analysis.get("board_meta") or {})
    issues: List[dict] = analysis.get("issues") or []
    summary: Dict[str, int] = analysis.get("issue_summary") or {}
    risk: Dict[str, Any] = analysis.get("risk") or {}
    info: Dict[str, Any] = analysis.get("info_stats") or {}

    markers = [dict(it) for it in issues if it.get("x") is not None]
    svg = ""
    if embed_svg and analysis.get("fab_data"):
        try:
            svg = render_board_svg(analysis["fab_data"], markers, width=1400,
                                   title=board_meta.get("board_name", ""))
        except Exception:
            svg = ""

    rows = []
    for i, it in enumerate(issues):
        sev = it.get("severity", "中")
        rows.append(
            "<tr class='issue-row' data-idx='{i}' data-sev='{sev}' data-mod='{mod}' "
            "data-txt='{txt}'>"
            "<td class='c'>{i1}</td>"
            "<td class='c'><span class='badge' style='--c:{color}'>{sev}</span></td>"
            "<td class='c mono'>{code}</td>"
            "<td class='c'>{mod}</td>"
            "<td class='c'>{cat}</td>"
            "<td>{rule}</td>"
            "<td class='c'>{cnt}</td>"
            "<td class='c mono'>{meas}</td>"
            "<td class='c mono'>{req}</td>"
            "<td class='c mono'>{xy}</td>"
            "<td class='desc'>{desc}<div class='sug'>建议: {sug}</div></td>"
            "</tr>".format(
                i=i, i1=i + 1, sev=_esc(sev), color=SEV_CSS.get(sev, "#888"),
                mod=_esc(it.get("module", "")), cat=_esc(it.get("category", "")),
                code=_esc(it.get("code", "")), rule=_esc(it.get("rule", "")),
                cnt=_esc(it.get("count", 1)),
                meas=_esc(_fmt(it.get("measured"))), req=_esc(_fmt(it.get("required"))),
                xy=(f"{it['x']:.2f}, {it['y']:.2f}"
                    if isinstance(it.get("x"), (int, float)) and it.get("y") is not None
                    else "—"),
                desc=_esc(it.get("description", "")), sug=_esc(it.get("suggestion", "")),
                txt=_esc((" ".join(str(it.get(k, "")) for k in
                                   ("code", "rule", "category", "description", "net")))
                         .lower()),
            ))

    modules = sorted({str(it.get("module") or "") for it in issues if it.get("module")})
    mod_options = "".join(f"<option value='{_esc(m)}'>{_esc(m)}</option>" for m in modules)
    sev_cards = "".join(
        f"<div class='stat' style='--c:{SEV_CSS.get(s, '#888')}'>"
        f"<div class='num'>{int(summary.get(s) or 0)}</div><div class='lbl'>{s}</div></div>"
        for s in SEVERITY_LEVELS if s != "关闭")

    checks_run = analysis.get("checks_run") or []
    checks_skipped = analysis.get("checks_skipped") or []
    checks_html = "".join(
        f"<li class='ok'>{_esc(c)}</li>" for c in checks_run) + "".join(
        f"<li class='skip'>{_esc(c.get('name') or c.get('id'))} "
        f"<span>({_esc(c.get('reason'))})</span></li>" for c in checks_skipped)

    cap_items = analysis.get("capability_items") or []
    cap_rows = "".join(
        "<tr class='cap-{st}'><td class='c'>{st}</td><td>{cat}</td><td>{item}</td>"
        "<td class='c mono'>{d}</td><td class='c mono'>{c}</td><td class='c'>{u}</td>"
        "<td>{msg}</td></tr>".format(
            st=_esc(it.get("status", "")), cat=_esc(it.get("category", "")),
            item=_esc(it.get("item", "")), d=_esc(_fmt(it.get("design"))),
            c=_esc(_fmt(it.get("capability"))), u=_esc(it.get("unit", "")),
            msg=_esc(it.get("message", "")))
        for it in cap_items)

    nc = analysis.get("net_classes") or {}
    nc_rows = "".join(f"<tr><td>{_esc(k)}</td><td class='c'>{v}</td></tr>"
                      for k, v in (nc.get("summary") or {}).items())
    nl = analysis.get("netlist_summary") or {}
    fn = analysis.get("fabnet_summary") or {}
    gm = analysis.get("gerber_meta") or {}
    fs = analysis.get("fab_stats") or {}
    gs = analysis.get("geometry_stats") or {}
    notes = analysis.get("notes") or []
    plugins = analysis.get("plugins") or []

    stack = analysis.get("layer_stack") or []
    stack_rows = "".join(
        f"<tr><td class='c'>{i + 1}</td><td>{_esc(s.get('layer', ''))}</td>"
        f"<td>{_esc(s.get('type', ''))}</td><td>{_esc(s.get('note', ''))}</td></tr>"
        for i, s in enumerate(stack) if isinstance(s, dict))

    js_data = json.dumps({
        "issues": [{"idx": i, "x": it.get("x"), "y": it.get("y"),
                    "sev": it.get("severity"), "rule": it.get("rule"),
                    "code": it.get("code"), "desc": it.get("description"),
                    "locations": (it.get("locations") or [])[:200]}
                   for i, it in enumerate(issues)],
    }, ensure_ascii=False).replace("</", "<\\/")     # 防止内容中的 </script> 提前闭合

    css = _CSS
    html_doc = f"""<!DOCTYPE html>
<html lang="zh-CN"><head><meta charset="utf-8">
<meta name="viewport" content="width=device-width,initial-scale=1">
<title>{_esc(board_meta.get('board_name', 'DFM'))} — DFM 报告</title>
<style>{css}</style></head>
<body>
<header class="top">
  <div class="brand">
    <div class="logo">E</div>
    <div><h1>{_esc(APP_NAME)} 可制造性分析报告</h1>
    <div class="sub">{_esc(board_meta.get('board_name', ''))}
      {_esc(board_meta.get('version', ''))}</div></div>
  </div>
  <div class="meta">
    <div><span>板厂</span>{_esc(analysis.get('factory_label', ''))}</div>
    <div><span>板型</span>{_esc(analysis.get('board_type_label', ''))}</div>
    <div><span>能力等级</span>{_esc(analysis.get('capability_label', ''))}</div>
    <div><span>铜厚</span>{_esc(_fmt(analysis.get('copper_um')))} um</div>
    <div><span>规则集</span>{_esc(board_meta.get('rule_set', ''))}</div>
    <div><span>生成时间</span>{_esc(board_meta.get('finish_time') or datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}</div>
  </div>
</header>

<section class="score-bar">
  <div class="card gauge-card">
    <h3>可制造性评分</h3>
    {_gauge(risk.get('score', 0), risk.get('risk_level', ''))}
    <div class="verdict">{_esc(risk.get('verdict', ''))}</div>
    <div class="yield">预估良率 {_esc(_fmt((risk.get('yield_estimate') or ['—'])[0]))}%
      ~ {_esc(_fmt((risk.get('yield_estimate') or ['—', '—'])[1]))}%</div>
  </div>
  <div class="card stats-card">
    <h3>问题分级</h3>
    <div class="stats">{sev_cards}
      <div class="stat total"><div class="num">{int(summary.get('总计') or 0)}</div>
        <div class="lbl">总计</div></div>
    </div>
  </div>
  <div class="card donut-card"><h3>占比</h3>{_donut(summary)}</div>
</section>

<section class="card board-card">
  <h3>版图与问题定位 <small>点击标记或表格行可双向定位；滚轮缩放，拖动平移</small></h3>
  <div id="board" class="board">{'<div class="empty">无几何数据 (需提供 BRD/JSON 或 Gerber/ODB++)</div>' if not svg else svg}</div>
  <div id="probe" class="probe">尚未选择问题</div>
</section>

<section class="card">
  <h3>问题清单</h3>
  <div class="toolbar">
    <select id="f-sev"><option value="">全部严重度</option>
      {''.join(f"<option value='{s}'>{s}</option>" for s in SEVERITY_LEVELS)}</select>
    <select id="f-mod"><option value="">全部检查</option>{mod_options}</select>
    <input id="f-txt" type="search" placeholder="搜索规则/描述/网络...">
    <span id="f-count" class="count"></span>
  </div>
  <div class="table-wrap"><table class="issues">
    <thead><tr><th>#</th><th>严重度</th><th>代码</th><th>检查</th><th>类别</th>
      <th>规则</th><th>数量</th><th>实测</th><th>要求</th><th>X,Y (mm)</th>
      <th>问题描述 / 专家建议</th></tr></thead>
    <tbody id="issues">{''.join(rows) or "<tr><td colspan='11' class='empty'>未发现问题</td></tr>"}</tbody>
  </table></div>
</section>

<section class="grid2">
  {_kv_table('制造工艺信息', info.get('manufacturing') or {})}
  {_kv_table('装配工艺信息', info.get('assembly') or {})}
  {_kv_table('制程能力备注', info.get('capability_notes') or {})}
  {_kv_table('制造数据统计', fs)}
  {_kv_table('几何统计', gs)}
  {_kv_table('开短路 / 网表一致性', nl)}
  {_kv_table('CAD vs FAB 网络比对', fn)}
  {_kv_table('Gerber 层识别', {k: v for k, v in gm.items() if k != 'layer_identification'} if gm else {})}
</section>

{_quality_section(analysis)}

{'<section class="card"><h3>制程能力逐项比对</h3><div class="table-wrap"><table class="issues cap"><thead><tr><th>结果</th><th>类别</th><th>项目</th><th>设计值</th><th>能力值</th><th>单位</th><th>说明</th></tr></thead><tbody>' + cap_rows + '</tbody></table></div></section>' if cap_rows else ''}

<section class="grid2">
  {'<section class="card"><h3>网络分类 / 阻抗需求</h3><table class="kv"><tr><th>网络总数</th><td>' + str(nc.get('total_nets', 0)) + '</td></tr><tr><th>差分对</th><td>' + str(nc.get('diff_pair_count', 0)) + '</td></tr><tr><th>阻抗网络</th><td>' + str(nc.get('impedance_net_count', 0)) + '</td></tr></table><table class="kv">' + nc_rows + '</table></section>' if nc else ''}
  {'<section class="card"><h3>叠层</h3><table class="kv"><thead><tr><th>#</th><th>层</th><th>类型</th><th>说明</th></tr></thead><tbody>' + stack_rows + '</tbody></table></section>' if stack_rows else ''}
</section>

<section class="grid2">
  <section class="card"><h3>检查执行情况</h3><ul class="checks">{checks_html or '<li>—</li>'}</ul></section>
  <section class="card"><h3>规则集与插件</h3>
    <table class="kv">
      <tr><th>规则集</th><td>{_esc(analysis.get('rules_source') or '(内置默认)')}</td></tr>
      <tr><th>数据源</th><td>{_esc((analysis.get('source') or {}).get('type', ''))}
        {_esc((analysis.get('source') or {}).get('path', ''))}</td></tr>
      <tr><th>插件</th><td>{_esc(', '.join(p.get('name', '') for p in plugins) or '(无)')}</td></tr>
    </table>
    {'<ul class="notes">' + ''.join('<li>' + _esc(n) + '</li>' for n in notes) + '</ul>' if notes else ''}
  </section>
</section>

<footer>
  {_esc(APP_NAME)} v{_esc(__version__)} · 生成于 {_esc(datetime.now().strftime('%Y-%m-%d %H:%M:%S'))}
  · 本报告由规则库驱动，判定依据见「规则集」
</footer>
<script id="dfm-data" type="application/json">{js_data}</script>
<script>{_JS}</script>
</body></html>"""

    os.makedirs(os.path.dirname(os.path.abspath(output_path)) or ".", exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(html_doc)
    return output_path


_CSS = """
:root{--bg:#0d1117;--panel:#151b23;--panel2:#1b222b;--line:#232c37;--fg:#e6edf3;
--mut:#9aa7b4;--acc:#4c8dff}
*{box-sizing:border-box}
body{margin:0;background:var(--bg);color:var(--fg);
font-family:"Segoe UI","Microsoft YaHei",system-ui,sans-serif;font-size:14px;line-height:1.55}
h1{font-size:19px;margin:0}h3{font-size:14px;margin:0 0 10px;color:#cdd9e5;
letter-spacing:.3px;text-transform:none}
h3 small{color:var(--mut);font-weight:400;text-transform:none;letter-spacing:0}
.top{display:flex;justify-content:space-between;gap:24px;flex-wrap:wrap;
padding:18px 24px;background:linear-gradient(180deg,#141b24,#0d1117);
border-bottom:1px solid var(--line)}
.brand{display:flex;gap:14px;align-items:center}
.logo{width:42px;height:42px;border-radius:11px;display:grid;place-items:center;
background:linear-gradient(135deg,#4c8dff,#7c5cff);font-weight:700;font-size:22px;color:#fff}
.sub{color:var(--mut);font-size:13px}
.meta{display:flex;gap:18px;flex-wrap:wrap;align-items:center}
.meta>div{font-size:13px}.meta span{display:block;color:var(--mut);font-size:11px}
.score-bar{display:grid;grid-template-columns:300px 1fr 200px;gap:16px;padding:18px 24px}
.card{background:var(--panel);border:1px solid var(--line);border-radius:12px;padding:16px}
.gauge-card{text-align:center}.gauge{width:190px;height:auto}
.gauge-num{font-size:22px;font-weight:700;text-anchor:middle}
.gauge-lbl{font-size:9px;fill:var(--mut);text-anchor:middle}
.verdict{margin-top:6px;color:#cdd9e5}.yield{color:var(--mut);font-size:12px}
.stats{display:flex;gap:12px;flex-wrap:wrap}
.stat{flex:1;min-width:96px;background:var(--panel2);border-radius:10px;padding:12px;
border-left:3px solid var(--c,#4c8dff);text-align:center}
.stat .num{font-size:26px;font-weight:700;color:var(--c,#e6edf3)}
.stat .lbl{color:var(--mut);font-size:12px}
.stat.total{border-left-color:#4c8dff}.stat.total .num{color:#4c8dff}
.donut-card{display:grid;place-items:center}
.donut{width:130px;height:130px;transform:rotate(0)}
.donut-num{font-size:9px;fill:var(--fg);text-anchor:middle;font-weight:700}
.donut-lbl{font-size:3.4px;fill:var(--mut);text-anchor:middle}
.board-card{margin:0 24px}
.board{background:#101418;border:1px solid var(--line);border-radius:10px;overflow:hidden;
max-height:640px;position:relative}
.board svg{display:block;width:100%;height:auto;cursor:grab}
.board svg.dragging{cursor:grabbing}
.empty{color:var(--mut);padding:28px;text-align:center}
.probe{margin-top:10px;color:var(--mut);font-size:13px;min-height:20px}
.probe b{color:var(--fg)}
.card,.grid2{margin:16px 24px}
.grid2{display:grid;grid-template-columns:repeat(auto-fit,minmax(340px,1fr));gap:16px}
.grid2 .card{margin:0}
.toolbar{display:flex;gap:10px;align-items:center;margin-bottom:10px;flex-wrap:wrap}
select,input[type=search]{background:var(--panel2);color:var(--fg);border:1px solid var(--line);
border-radius:8px;padding:7px 10px;font-size:13px;outline:none}
input[type=search]{min-width:260px}
.count{color:var(--mut);font-size:12px;margin-left:auto}
.table-wrap{max-height:520px;overflow:auto;border:1px solid var(--line);border-radius:10px}
table{border-collapse:collapse;width:100%;font-size:13px}
thead th{position:sticky;top:0;background:#1d2530;color:#cdd9e5;text-align:left;
padding:9px 10px;border-bottom:1px solid var(--line);font-weight:600;white-space:nowrap;z-index:2}
tbody td{padding:8px 10px;border-bottom:1px solid #1c242e;vertical-align:top}
tbody tr:hover{background:#1a222c}
tbody tr.sel{background:#1f2f45;box-shadow:inset 3px 0 0 var(--acc)}
td.c{text-align:center;white-space:nowrap}
.mono{font-family:Consolas,"Cascadia Mono",monospace;font-size:12px}
.desc{max-width:640px}.sug{color:var(--mut);font-size:12px;margin-top:3px}
.badge{display:inline-block;min-width:38px;padding:2px 8px;border-radius:20px;
font-size:12px;color:#111;background:var(--c,#888);font-weight:600}
table.kv th{text-align:left;color:var(--mut);font-weight:500;width:44%;
padding:6px 10px;border-bottom:1px solid #1c242e}
table.kv td{padding:6px 10px;border-bottom:1px solid #1c242e}
.cap-pass td:first-child{color:#46a758}.cap-warn_limit td:first-child{color:#ffc53d}
.cap-fail td:first-child{color:#e5484d}.cap-info td:first-child{color:#9aa7b4}
ul.checks{margin:0;padding-left:18px}ul.checks li{margin:3px 0}
ul.checks li.ok::marker{color:#46a758}ul.checks li.skip{color:var(--mut)}
ul.checks li.skip span{color:#7d8894}
ul.notes{margin:10px 0 0;padding-left:18px;color:var(--mut);font-size:12px}
.qgrid{display:grid;grid-template-columns:minmax(280px,1fr) minmax(320px,1.4fr);gap:18px}
.qwarn{background:#3a2a12;border:1px solid #7a5a1e;color:#ffd479;padding:9px 12px;
border-radius:8px;margin-bottom:12px;font-size:13px}
.qgrid h4{margin:14px 0 4px;font-size:12px;color:#cdd9e5}
.bar{display:inline-block;width:70px;height:6px;background:#2a2f36;border-radius:3px;
overflow:hidden;vertical-align:middle;margin-right:6px}
.bar>i{display:block;height:100%;background:var(--acc)}
ul.notes li.bad{color:#f0554d}
@media(max-width:1000px){.qgrid{grid-template-columns:1fr}}
footer{padding:20px 24px;color:var(--mut);font-size:12px;border-top:1px solid var(--line);
margin-top:20px;text-align:center}
.dfm-marker{cursor:pointer}
.dfm-marker.sel{stroke:#fff;stroke-width:2}
@media(max-width:1000px){.score-bar{grid-template-columns:1fr}}
"""

_JS = """
(function(){
  var data = JSON.parse(document.getElementById('dfm-data').textContent);
  var rows = Array.prototype.slice.call(document.querySelectorAll('#issues tr.issue-row'));
  var board = document.getElementById('board');
  var svg = board.querySelector('svg');
  var probe = document.getElementById('probe');
  var markers = svg ? Array.prototype.slice.call(svg.querySelectorAll('.dfm-marker')) : [];
  var byIdx = {};
  markers.forEach(function(m){
    byIdx[m.getAttribute('data-idx')] = m;
    m.setAttribute('data-r0', m.getAttribute('r'));     // 记录原始半径, 选中时放大
  });

  function select(i, scroll){
    rows.forEach(function(r){ r.classList.toggle('sel', r.getAttribute('data-idx') === String(i)); });
    markers.forEach(function(m){
      var on = m.getAttribute('data-idx') === String(i);
      m.classList.toggle('sel', on);
      var r0 = parseFloat(m.getAttribute('data-r0'));
      m.setAttribute('r', on ? r0 * 1.8 : r0);
    });
    var it = data.issues[i];
    if (it){
      probe.innerHTML = '问题 ' + (i+1) + ' · <b>[' + it.sev + '] ' + it.code + ' ' +
        it.rule + '</b> · ' + (it.x != null ? ('X=' + it.x.toFixed(3) + ' Y=' + it.y.toFixed(3) + ' mm') : '无坐标') +
        (it.locations && it.locations.length > 1 ? (' · 共 ' + it.locations.length + ' 处') : '') +
        '<br>' + (it.desc || '');
    }
    if (scroll){
      var r = rows[i];
      if (r) r.scrollIntoView({block:'nearest'});
    }
  }

  rows.forEach(function(r){
    r.addEventListener('click', function(){ select(parseInt(r.getAttribute('data-idx'),10), false); });
  });
  markers.forEach(function(m){
    m.addEventListener('click', function(ev){
      ev.stopPropagation();
      var i = parseInt(m.getAttribute('data-idx'),10);
      select(i, true);
    });
  });

  // ---- 过滤 ----
  var fSev = document.getElementById('f-sev');
  var fMod = document.getElementById('f-mod');
  var fTxt = document.getElementById('f-txt');
  var fCount = document.getElementById('f-count');
  function applyFilter(){
    var s = fSev.value, m = fMod.value, q = (fTxt.value||'').toLowerCase().trim();
    var n = 0;
    rows.forEach(function(r){
      var ok = (!s || r.getAttribute('data-sev') === s) &&
               (!m || r.getAttribute('data-mod') === m) &&
               (!q || r.getAttribute('data-txt').indexOf(q) >= 0);
      r.style.display = ok ? '' : 'none';
      if (ok) n++;
      var idx = r.getAttribute('data-idx');
      if (byIdx[idx]) byIdx[idx].style.display = ok ? '' : 'none';
    });
    fCount.textContent = '显示 ' + n + ' / ' + rows.length + ' 项';
  }
  [fSev, fMod].forEach(function(e){ e.addEventListener('change', applyFilter); });
  fTxt.addEventListener('input', applyFilter);
  applyFilter();

  // ---- 缩放 / 平移 ----
  if (svg){
    var vb = svg.viewBox.baseVal;
    var home = {x: vb.x, y: vb.y, w: vb.width, h: vb.height};
    var dragging = false, last = null;
    function setVB(x,y,w,h){ vb.x=x; vb.y=y; vb.width=w; vb.height=h; }
    svg.addEventListener('wheel', function(ev){
      ev.preventDefault();
      var k = ev.deltaY > 0 ? 1.15 : 1/1.15;
      var pt = svgPoint(ev);
      var w = Math.min(home.w*4, Math.max(home.w/200, vb.width*k));
      var h = w * (home.h/home.w);
      var rx = (pt.x - vb.x)/vb.width, ry = (pt.y - vb.y)/vb.height;
      setVB(pt.x - rx*w, pt.y - ry*h, w, h);
    }, {passive:false});
    svg.addEventListener('mousedown', function(ev){ dragging=true; last=svgPoint(ev); svg.classList.add('dragging'); });
    window.addEventListener('mouseup', function(){ dragging=false; svg.classList.remove('dragging'); });
    svg.addEventListener('mousemove', function(ev){
      if(!dragging) return;
      var p = svgPoint(ev);
      setVB(vb.x - (p.x-last.x), vb.y - (p.y-last.y), vb.width, vb.height);
      last = svgPoint(ev);
    });
    svg.addEventListener('dblclick', function(){ setVB(home.x, home.y, home.w, home.h); });
    function svgPoint(ev){
      var r = svg.getBoundingClientRect();
      return {x: vb.x + (ev.clientX-r.left)/r.width*vb.width,
              y: vb.y + (ev.clientY-r.top)/r.height*vb.height};
    }
  }
})();
"""
