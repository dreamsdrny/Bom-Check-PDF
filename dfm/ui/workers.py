"""后台任务 (QThread) — 分析过程不阻塞界面。"""
from __future__ import annotations

import traceback
from typing import Any, Dict, Optional

from PySide6.QtCore import QThread, Signal


class AnalysisWorker(QThread):
    """执行 :func:`dfm.core.pipeline.analyze`。

    信号::

        progress(str)      进度消息 (可直接进日志与状态栏)
        finished_(dict)    分析结果 (analysis)
        failed(str)        错误信息 (含堆栈)
    """

    progress = Signal(str)
    finished_ = Signal(dict)
    failed = Signal(str)

    def __init__(self, cfg: Dict[str, Any], parent: Optional[Any] = None):
        super().__init__(parent)
        self.cfg = dict(cfg or {})

    def run(self) -> None:  # noqa: D102
        try:
            from dfm.core.pipeline import analyze

            result = analyze(self.cfg, progress=lambda m: self.progress.emit(str(m)))
            self.finished_.emit(result)
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")


class TaskWorker(QThread):
    """通用后台任务: 执行 fn(*args) 并回传结果, 用于导出报告等耗时操作。"""

    progress = Signal(str)
    finished_ = Signal(object)
    failed = Signal(str)

    def __init__(self, fn, *args, parent: Optional[Any] = None, **kwargs):
        super().__init__(parent)
        self.fn = fn
        self.args = args
        self.kwargs = kwargs

    def run(self) -> None:  # noqa: D102
        try:
            self.finished_.emit(self.fn(*self.args, **self.kwargs))
        except Exception as exc:  # noqa: BLE001
            self.failed.emit(f"{exc}\n\n{traceback.format_exc()}")
