"""路径解析: 开发环境 / PyInstaller 打包 / 用户可写目录 三种形态统一处理.

优先级 (先找到先用, 用户目录可覆盖随包发布的规则库)::

    规则库  EVAN_DFM_RULES 环境变量 > <程序目录>/rules > %APPDATA%/EvanDFM/rules
    插件    <程序目录>/plugins + %APPDATA%/EvanDFM/plugins
    用户数据 %APPDATA%/EvanDFM (settings.json / logs / 导出报告)

二次开发时只需把 YAML 规则库或插件目录放到上述任一位置即可被自动发现。
"""
from __future__ import annotations

import os
import sys
from typing import List, Optional

APP_DIR_NAME = "EvanDFM"
ENV_RULES = "EVAN_DFM_RULES"
ENV_PLUGINS = "EVAN_DFM_PLUGINS"


def is_frozen() -> bool:
    """是否运行于 PyInstaller 打包后的 exe。"""
    return bool(getattr(sys, "frozen", False))


def app_root() -> str:
    """程序根目录: 打包后为 exe 所在目录, 开发环境为项目根目录。"""
    if is_frozen():
        return os.path.dirname(os.path.abspath(sys.executable))
    return os.path.dirname(os.path.dirname(os.path.abspath(__file__)))


def bundle_dir() -> str:
    """只读资源目录: 打包后为 PyInstaller 解包目录 (_MEIPASS)。"""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return str(meipass)
    return os.path.dirname(os.path.abspath(__file__))


def user_data_dir(create: bool = True) -> str:
    """用户可写数据目录 (%APPDATA%/EvanDFM)。"""
    base = os.environ.get("APPDATA") or os.path.expanduser("~")
    path = os.path.join(base, APP_DIR_NAME)
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def rules_dirs() -> List[str]:
    """规则库搜索路径 (优先级从高到低)。"""
    dirs: List[str] = []
    env = os.environ.get(ENV_RULES)
    if env:
        dirs.append(env)
    dirs.append(os.path.join(user_data_dir(create=False), "rules"))
    for base in (app_root(), bundle_dir()):
        dirs.append(os.path.join(base, "rules"))
    out: List[str] = []
    for d in dirs:
        if d and os.path.isdir(d) and d not in out:
            out.append(d)
    return out


def capability_dirs() -> List[str]:
    """制程能力库 (YAML) 搜索路径。"""
    return [os.path.join(d, "capability") for d in rules_dirs()]


def plugins_dirs() -> List[str]:
    """插件搜索路径。"""
    dirs: List[str] = []
    env = os.environ.get(ENV_PLUGINS)
    if env:
        dirs.append(env)
    dirs.append(os.path.join(user_data_dir(create=False), "plugins"))
    for base in (app_root(), bundle_dir()):
        dirs.append(os.path.join(base, "plugins"))
    out: List[str] = []
    for d in dirs:
        if d and d not in out:
            out.append(d)
    return out


def find_rule_file(filename: str, subdir: str = "capability") -> Optional[str]:
    """在规则库搜索路径中查找文件, 返回优先级最高的那个。"""
    for d in rules_dirs():
        path = os.path.join(d, subdir, filename) if subdir else os.path.join(d, filename)
        if os.path.isfile(path):
            return path
    return None


def resource_path(*parts: str) -> str:
    """只读资源 (图标/模板) 绝对路径。"""
    return os.path.join(bundle_dir(), *parts)


def asset_path(name: str) -> str:
    """assets 目录下的资源路径。"""
    for base in (app_root(), bundle_dir()):
        p = os.path.join(base, "assets", name)
        if os.path.isfile(p):
            return p
    return os.path.join(bundle_dir(), "assets", name)


def default_output_dir(create: bool = True) -> str:
    """默认报告输出目录。"""
    path = os.path.join(user_data_dir(create=create), "reports")
    if create:
        os.makedirs(path, exist_ok=True)
    return path


def settings_path() -> str:
    return os.path.join(user_data_dir(), "settings.json")


def log_path() -> str:
    d = os.path.join(user_data_dir(), "logs")
    os.makedirs(d, exist_ok=True)
    return os.path.join(d, "evan_dfm.log")
