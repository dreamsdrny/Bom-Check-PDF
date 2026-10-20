"""DFM 检查项集合。

导入本包即完成全部内置检查的注册 (见 :func:`dfm.core.registry.load_builtin_checks`)。

=========== ============================ ==========================
检查 id      名称                          依赖数据
=========== ============================ ==========================
fabdata     制造数据完整性                 fab
fabrication 几何制造 DFM (定位 XY)         design + capability
netlist     开短路 + 网表一致性            design
assembly    装配 / SMT DFM                design
panelization 拼板 / 工艺边 DFM             params + capability
capability  制程能力比对 (参数化)          params + capability
fabnet      CAD vs FAB 网络比对            cad + (fab 或 design)
netclass    网络分类与阻抗要求 (信息项)     design 或 params
=========== ============================ ==========================
"""
from dfm.core.registry import load_builtin_checks

load_builtin_checks()

__all__ = ["load_builtin_checks"]
