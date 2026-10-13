"""
Evan_DFM: PDF 导入 (平板设计参考)
=============================================
使用 PyMuPDF(fitz) 读取 PDF 页面: 页面尺寸(mm) + 栅格图(PIL.Image) + 文本。
用于拼版时作为参考底图 (开料图/工作稿图背景)。

用法:
    d = read_pdf("drawing.pdf")
    d["page_w_mm"], d["page_h_mm"], d["image"] (PIL.Image), d["text"]
"""
import logging

logger = logging.getLogger(__name__)


def read_pdf(path: str, page: int = 0, dpi: int = 150) -> dict:
    try:
        try:
            import pymupdf as fitz  # 新版 PyMuPDF
        except ImportError:
            import fitz  # 旧版
    except ImportError:
        return {"error": "未安装 PyMuPDF (fitz), 无法读取 PDF", "image": None}
    try:
        from PIL import Image
    except ImportError:
        Image = None
    try:
        doc = fitz.open(path)
    except Exception as exc:  # noqa: BLE001
        return {"error": f"PDF 打开失败: {exc}", "image": None}

    try:
        if page < 0 or page >= doc.page_count:
            page = 0
        pg = doc.load_page(page)
        rect = pg.rect
        w_mm = rect.width * 25.4 / 72.0
        h_mm = rect.height * 25.4 / 72.0
        text = ""
        try:
            text = pg.get_text()
        except Exception as exc:  # noqa: BLE001
            logger.warning("PDF 文本提取失败: %s (%s)", path, exc)
        image = None
        if Image is not None:
            try:
                pix = pg.get_pixmap(dpi=dpi)
                image = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
            except Exception as exc:  # noqa: BLE001
                logger.warning("PDF 栅格化失败: %s (%s)", path, exc)
                image = None
        return {
            "page_w_mm": round(w_mm, 3),
            "page_h_mm": round(h_mm, 3),
            "page_count": doc.page_count,
            "image": image,
            "text": text,
            "name": path,
        }
    finally:
        try:
            doc.close()
        except Exception as exc:  # noqa: BLE001
            logger.debug("PDF 关闭失败: %s (%s)", path, exc)