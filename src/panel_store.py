"""
拼版数据记录与持久化 (参数模板 / 板材库 / 方案管理 / 操作记录)
=============================================================
对应《排版方式.docx》:
  📋 参数模板 —— 每种产品类型存一套参数, 支持增删改查
  🗄️ 板材库   —— 品牌/型号/长宽/厚度/铜厚, 勾选后分别计算并对比利用率
  📊 方案管理 —— 草稿 → 确认 → 发布 → 归档, 可标记"最终采用"

存储: 单一 JSON 文件 (默认 <程序目录>/output/panel_store.json), 结构:
  {"templates": [...], "materials": [...], "schemes": [...], "history": [...]}
"""
import json
import os
import time
from typing import Any, Dict, List, Optional

SCHEME_STATUS = ["草稿", "确认", "发布", "归档"]

_DEFAULT: Dict[str, List[dict]] = {
    "templates": [], "materials": [], "schemes": [], "history": [],
}


def default_store_path() -> str:
    import sys
    if getattr(sys, "frozen", False):        # 打包后写到 exe 同级 output 目录
        base = os.path.dirname(os.path.abspath(sys.executable))
    else:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
    return os.path.join(base, "output", "panel_store.json")


class PanelStore:
    def __init__(self, path: Optional[str] = None):
        self.path = path or default_store_path()
        self.data: Dict[str, List[dict]] = {k: [] for k in _DEFAULT}
        self.load()

    # ---------- 基础 ----------
    def load(self) -> dict:
        try:
            if os.path.exists(self.path):
                with open(self.path, "r", encoding="utf-8") as f:
                    raw = json.load(f) or {}
                for k in self.data:
                    v = raw.get(k)
                    if isinstance(v, list):
                        self.data[k] = v
        except Exception:
            pass
        return self.data

    def save(self) -> str:
        try:
            os.makedirs(os.path.dirname(os.path.abspath(self.path)), exist_ok=True)
            with open(self.path, "w", encoding="utf-8") as f:
                json.dump(self.data, f, indent=2, ensure_ascii=False, default=str)
        except Exception:
            pass
        return self.path

    def log(self, action: str, detail: str = "") -> None:
        """操作记录 (最多保留 500 条)"""
        self.data["history"].insert(0, {
            "time": time.strftime("%Y-%m-%d %H:%M:%S"),
            "action": action, "detail": detail,
        })
        del self.data["history"][500:]
        self.save()

    def history(self, limit: int = 200) -> List[dict]:
        return self.data["history"][:limit]

    # ---------- 参数模板 ----------
    def templates(self) -> List[dict]:
        return self.data["templates"]

    def save_template(self, name: str, params: dict) -> dict:
        name = (name or "").strip() or time.strftime("模板_%Y%m%d_%H%M%S")
        rec = {"name": name, "params": params, "updated": time.strftime("%Y-%m-%d %H:%M:%S")}
        for i, t in enumerate(self.data["templates"]):
            if t.get("name") == name:
                self.data["templates"][i] = rec
                self.log("模板更新", name)
                self.save()
                return rec
        self.data["templates"].append(rec)
        self.log("模板新增", name)
        self.save()
        return rec

    def get_template(self, name: str) -> Optional[dict]:
        for t in self.data["templates"]:
            if t.get("name") == name:
                return t
        return None

    def delete_template(self, name: str) -> bool:
        n = len(self.data["templates"])
        self.data["templates"] = [t for t in self.data["templates"] if t.get("name") != name]
        ok = len(self.data["templates"]) != n
        if ok:
            self.log("模板删除", name)
            self.save()
        return ok

    # ---------- 板材库 ----------
    def materials(self) -> List[dict]:
        return self.data["materials"]

    def add_material(self, brand: str, model: str, w: float, h: float,
                     thickness: Optional[float] = None, copper: Optional[str] = None,
                     enabled: bool = True) -> dict:
        rec = {"brand": brand or "", "model": model or "", "w": w, "h": h,
               "thickness": thickness, "copper": copper or "", "enabled": bool(enabled)}
        self.data["materials"].append(rec)
        self.log("板材新增", f"{brand} {model} {w}x{h}")
        self.save()
        return rec

    def update_material(self, index: int, **kw) -> bool:
        if 0 <= index < len(self.data["materials"]):
            self.data["materials"][index].update(kw)
            self.log("板材修改", str(self.data["materials"][index]))
            self.save()
            return True
        return False

    def delete_material(self, index: int) -> bool:
        if 0 <= index < len(self.data["materials"]):
            rec = self.data["materials"].pop(index)
            self.log("板材删除", f"{rec.get('brand')} {rec.get('model')}")
            self.save()
            return True
        return False

    def enabled_materials(self) -> List[dict]:
        return [m for m in self.data["materials"] if m.get("enabled")]

    # ---------- 方案管理 ----------
    def schemes(self) -> List[dict]:
        return self.data["schemes"]

    def save_scheme(self, name: str, params: dict, metrics: dict,
                    status: str = "草稿", scheme_id: Optional[str] = None) -> dict:
        now = time.strftime("%Y-%m-%d %H:%M:%S")
        sid = scheme_id or f"S{int(time.time() * 1000)}"
        rec = {"id": sid, "name": (name or "").strip() or sid, "params": params,
               "metrics": metrics, "status": status if status in SCHEME_STATUS else "草稿",
               "final": False, "created": now, "updated": now}
        for i, s in enumerate(self.data["schemes"]):
            if s.get("id") == sid:
                rec["created"] = s.get("created", now)
                rec["final"] = s.get("final", False)
                self.data["schemes"][i] = rec
                self.log("方案更新", rec["name"])
                self.save()
                return rec
        self.data["schemes"].insert(0, rec)
        self.log("方案保存", f"{rec['name']} ({rec['status']})")
        self.save()
        return rec

    def set_scheme_status(self, scheme_id: str, status: str) -> bool:
        for s in self.data["schemes"]:
            if s.get("id") == scheme_id and status in SCHEME_STATUS:
                s["status"] = status
                s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.log("方案状态", f"{s.get('name')} → {status}")
                self.save()
                return True
        return False

    def set_scheme_final(self, scheme_id: str, final: bool = True) -> bool:
        for s in self.data["schemes"]:
            if s.get("id") == scheme_id:
                if final:
                    for o in self.data["schemes"]:
                        o["final"] = False
                s["final"] = final
                s["updated"] = time.strftime("%Y-%m-%d %H:%M:%S")
                self.log("最终采用", f"{s.get('name')} = {final}")
                self.save()
                return True
        return False

    def delete_scheme(self, scheme_id: str) -> bool:
        n = len(self.data["schemes"])
        self.data["schemes"] = [s for s in self.data["schemes"] if s.get("id") != scheme_id]
        ok = len(self.data["schemes"]) != n
        if ok:
            self.log("方案删除", scheme_id)
            self.save()
        return ok

    def get_scheme(self, scheme_id: str) -> Optional[dict]:
        for s in self.data["schemes"]:
            if s.get("id") == scheme_id:
                return s
        return None


def compare_materials(base_params: dict, materials: List[dict], design_fn) -> List[dict]:
    """对板材库中勾选的每种板材分别计算最优方案, 按利用率排序对比"""
    out = []
    for m in materials or []:
        p = dict(base_params)
        p["wpnl_w"] = float(m.get("w") or 0) or p.get("wpnl_w")
        p["wpnl_h"] = float(m.get("h") or 0) or p.get("wpnl_h")
        p["wpnl_sizes"] = []
        p["wp_nl_option"] = ""
        try:
            plan = design_fn(p)
        except Exception as exc:  # noqa: BLE001
            out.append({"material": m, "error": str(exc)})
            continue
        out.append({
            "material": m,
            "ok": plan.get("ok"),
            "utilization": (plan.get("utilization") or {}).get("panel",
                                                               (plan.get("utilization") or {}).get("wpnl", 0)),
            "overall": (plan.get("utilization") or {}).get("overall", 0),
            "counts": plan.get("counts"),
            "cost_index": (plan.get("cost") or {}).get("cost_index"),
            "recommendation": plan.get("recommendation"),
        })
    out.sort(key=lambda r: (r.get("utilization") or 0), reverse=True)
    return out