"""
BRD-AI: Allegro SKILL 数据导出脚本生成器
=============================================================
Cadence Allegro .brd 为二进制专有格式, 纯 Python 无法可靠解析几何坐标。
本模块提供一段可在 Allegro PCB Editor 内运行的 SKILL 脚本 (brd_dfm_export.il),
用于把「带 XY 坐标的几何 + 网表 + DRC」导出为 JSON, 供 Python 端做:
  - Valor 式几何 DFM (线宽/间距/孔环/孔径/纵横比/板边/阻焊桥/测试点) 定位到 XY
  - 开短路测试 (几何连通性派生) + 网表一致性对比
  - 输出 Valor 式 DFM 报告 (Excel/Text)

用法:
  1) 由本工具写出 brd_dfm_export.il (write_export_skill / GUI『导出SKILL脚本』)
  2) 在 Allegro PCB Editor 中打开目标 .brd
  3) Command 窗口执行:  skill load "brd_dfm_export.il"
     或:  (brdExportJSON "C:/temp/brd_export.json")
  4) 回到本工具『分析BRD-DFM』, 选择导出的 JSON 即可

说明: 不同 Allegro 版本 DB 访问器略有差异, 脚本对每个字段均用 errset 容错,
      失败字段自动跳过, 始终产出合法 JSON。坐标统一换算为 mm。
"""
import os

EXPORT_SKILL_FILENAME = "brd_dfm_export.il"
EXPORT_FORMAT_TAG = "brd-dfm-export"
EXPORT_FORMAT_VERSION = "1.0"

# ------------------------------------------------------------------
# SKILL 脚本正文 (raw string: 保留 SKILL 的 \n \" \\ 等转义原样)
# ------------------------------------------------------------------
EXPORT_SKILL = r"""; ============================================================
; BRD-AI: Allegro DFM 数据导出脚本 (brd_dfm_export.il)
; ------------------------------------------------------------
; 作用: 将当前 .brd 的网表/几何/DRC/叠层/板框导出为 JSON(坐标 mm),
;       供 BRD-AI 做 Valor 式 DFM 定位与开短路/网表测试。
; 用法:
;   1. 在 Allegro PCB Editor 打开 .brd
;   2. Command 窗口:  skill load "brd_dfm_export.il"
;   3. 执行:          brdExportJSON            ; 导出到 .brd 同目录
;      或:            (brdExportJSON "D:/out/brd_export.json")
; 兼容: Allegro 16.6 / 17.x (DB 访问器均 errset 容错)
; ============================================================

; ---------- 字符串转义 (JSON) ----------
(defun brdEsc (s)
  (let (out)
    (setq out "")
    (when (stringp s)
      (for i 1 length(s)
        (let (c)
          (setq c substring(s i i))
          (cond
            ((equal c "\"") (setq out strcat(out "\\\"")))
            ((equal c "\\") (setq out strcat(out "\\\\")))
            ((equal c "\n") (setq out strcat(out "\\n")))
            ((equal c "\r") out)
            ((equal c "\t") (setq out strcat(out "\\t")))
            (t             (setq out strcat(out c)))
          )
        )
      )
    )
    out
  )
)

; ---------- 安全取属性 ----------
(defun brdGet (obj prop)
  (errset (getprop obj prop) t)
)
(defun brdGet2 (obj prop)
  (let (r) (setq r (brdGet obj prop)) (if r (car r) nil))
)

; ---------- 单位 -> mm 比例 ----------
(defun brdUnitScale (units)
  (cond
    ((or (equal units "millimeter") (equal units "mm")) 1.0)
    ((or (equal units "micron") (equal units "um")) 0.001)
    ((or (equal units "centimeter") (equal units "cm")) 10.0)
    ((or (equal units "meter") (equal units "m")) 1000.0)
    ((or (equal units "mil") (equal units "mils") (equal units "thou")) 0.0254)
    ((or (equal units "inch") (equal units "in")) 25.4)
    (t 1.0)
  )
)

; ---------- 数值格式化 ----------
(defun brdN (v scale)
  (if (numberp v)
    (sprintf nil "%g" (v * scale))
    "null"
  )
)
(defun brdPt (xy scale f)
  (if (and xy (listp xy) (>= length(xy) 2))
    (fprintf f "[%s,%s]" (brdN (car xy) scale) (brdN (cadr xy) scale))
    (fprintf f "[]")
  )
)

; ---------- 板框 (OUTLINE 层轮廓点) ----------
(defun brdWriteOutline (design scale f)
  (let (shapes s layer fig pts first)
    (fprintf f "\"outline\": [")
    (setq first t)
    (setq shapes (brdGet2 design 'shapes))
    (foreach s (if (listp shapes) shapes nil)
      (setq layer (brdGet2 s 'layer))
      (when (and layer (stringp (brdGet2 layer 'name))
                 (or (equal (brdGet2 layer 'name) "BOARD GEOMETRY")
                     (equal (brdGet2 layer 'name) "OUTLINE")))
        (setq fig (brdGet2 s 'figure))
        (setq pts (if fig (brdGet2 fig 'vertices) nil))
        (foreach p (if (listp pts) pts nil)
          (unless first (fprintf f ","))
          (setq first nil)
          (brdPt p scale f)
        )
      )
    )
    (fprintf f "]")
  )
)

; ---------- 叠层 / 导体层 ----------
(defun brdWriteStackup (design scale f)
  (let (layers l name ltype first)
    (fprintf f "\"stackup\": [")
    (setq first t)
    (setq layers (errset (axlGetStackupLayers) t))
    (setq layers (if layers (car layers) nil))
    (when (null layers) (setq layers (brdGet2 design 'layers)))
    (foreach l (if (listp layers) layers nil)
      (setq name (if (stringp l) l (brdGet2 l 'name)))
      (when (and name (stringp name))
        (setq ltype (if (stringp l) "signal" (or (brdGet2 l 'type) "signal")))
        (unless first (fprintf f ","))
        (setq first nil)
        (fprintf f "{\"layer\":\"%s\",\"type\":\"%s\"}" (brdEsc name) (brdEsc (string ltype)))
      )
    )
    (fprintf f "]")
  )
)

; ---------- 网络 + 节点(含XY) ----------
(defun brdWriteNets (design scale f maxItems)
  (let (nets net pins pin comp pname first cnt)
    (fprintf f "\"nets\": [")
    (setq first t) (setq cnt 0)
    (setq nets (brdGet2 design 'nets))
    (foreach net (if (listp nets) nets nil)
      (when (< cnt maxItems)
        (setq pname (brdGet2 net 'name))
        (when (and pname (stringp pname))
          (setq cnt (cnt + 1))
          (unless first (fprintf f ","))
          (setq first nil)
          (fprintf f "\n  {\"name\":\"%s\",\"nodes\":[" (brdEsc pname))
          (setq pins (brdGet2 net 'pins))
          (let (pfirst)
            (setq pfirst t)
            (foreach pin (if (listp pins) pins nil)
              (setq comp (brdGet2 pin 'component))
              (let (refdes pinnum)
                (setq refdes (if comp (brdGet2 comp 'name) nil))
                (setq pinnum (brdGet2 pin 'number))
                (unless pfirst (fprintf f ","))
                (setq pfirst nil)
                (fprintf f "{\"refdes\":\"%s\",\"pin\":\"%s\",\"x\":%s,\"y\":%s,\"layer\":\"%s\"}"
                  (brdEsc (or refdes ""))
                  (brdEsc (if pinnum (string pinnum) ""))
                  (brdN (car (brdGet2 pin 'xy)) scale)
                  (brdN (cadr (brdGet2 pin 'xy)) scale)
                  (brdEsc (or (brdGet2 (brdGet2 pin 'layer) 'name) ""))
                )
              )
            )
          )
          (fprintf f "]}")
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- 器件 ----------
(defun brdWriteComponents (design scale f maxItems)
  (let (comps c name xy first cnt sym side)
    (fprintf f "\"components\": [")
    (setq first t) (setq cnt 0)
    (setq comps (brdGet2 design 'components))
    (foreach c (if (listp comps) comps nil)
      (when (< cnt maxItems)
        (setq name (brdGet2 c 'name))
        (when (and name (stringp name))
          (setq cnt (cnt + 1))
          (setq xy (brdGet2 c 'xy))
          (when (null xy) (setq xy (brdGet2 (brdGet2 c 'symbol) 'xy)))
          (setq sym (brdGet2 c 'symbol))
          (setq side (or (brdGet2 c 'side) (brdGet2 sym 'side) "TOP"))
          (unless first (fprintf f ","))
          (setq first nil)
          (fprintf f "\n  {\"refdes\":\"%s\",\"package\":\"%s\",\"x\":%s,\"y\":%s,\"rotation\":%s,\"side\":\"%s\",\"pins\":%s}"
            (brdEsc name)
            (brdEsc (or (brdGet2 (brdGet2 c 'symbol) 'name) ""))
            (brdN (car xy) scale) (brdN (cadr xy) scale)
            (brdN (or (brdGet2 c 'rotation) 0) 1.0)
            (brdEsc (string side))
            (if (numberp (brdGet2 c 'pinCount)) (sprintf nil "%d" (brdGet2 c 'pinCount)) "null")
          )
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- Clines (走线, 含线宽/网络/层/折线点) ----------
(defun brdWriteClines (design scale f maxItems)
  (let (clines cl net layer w pts first cnt)
    (fprintf f "\"clines\": [")
    (setq first t) (setq cnt 0)
    (setq clines (brdGet2 design 'clines))
    (foreach cl (if (listp clines) clines nil)
      (when (< cnt maxItems)
        (setq cnt (cnt + 1))
        (setq net (brdGet2 cl 'net))
        (setq layer (brdGet2 cl 'layer))
        (setq w (brdGet2 cl 'width))
        (setq pts (brdGet2 cl 'points))
        (unless first (fprintf f ","))
        (setq first nil)
        (fprintf f "\n  {\"net\":\"%s\",\"layer\":\"%s\",\"width\":%s,\"points\":["
          (brdEsc (if net (brdGet2 net 'name) ""))
          (brdEsc (if layer (brdGet2 layer 'name) ""))
          (brdN w scale))
        (let (pfirst)
          (setq pfirst t)
          (foreach p (if (listp pts) pts nil)
            (unless pfirst (fprintf f ","))
            (setq pfirst nil)
            (brdPt p scale f)
          )
        )
        (fprintf f "]}")
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- Vias (过孔, 含孔径/焊盘/起止层/类型) ----------
(defun brdWriteVias (design scale f maxItems)
  (let (vias v net xy ps first cnt dr pad sl el vt)
    (fprintf f "\"vias\": [")
    (setq first t) (setq cnt 0)
    (setq vias (brdGet2 design 'vias))
    (foreach v (if (listp vias) vias nil)
      (when (< cnt maxItems)
        (setq cnt (cnt + 1))
        (setq net (brdGet2 v 'net))
        (setq xy (brdGet2 v 'xy))
        (setq ps (brdGet2 v 'padstack))
        (setq dr (or (brdGet2 v 'drillDiameter) (brdGet2 ps 'drillDiameter)))
        (setq pad (or (brdGet2 v 'padDiameter) (brdGet2 ps 'padDiameter)))
        (setq sl (brdGet2 (brdGet2 v 'startLayer) 'name))
        (setq el (brdGet2 (brdGet2 v 'endLayer) 'name))
        (setq vt (or (brdGet2 v 'type) (brdGet2 ps 'type) "through"))
        (unless first (fprintf f ","))
        (setq first nil)
        (fprintf f "\n  {\"net\":\"%s\",\"x\":%s,\"y\":%s,\"drill_mm\":%s,\"pad_mm\":%s,\"padstack\":\"%s\",\"start_layer\":\"%s\",\"end_layer\":\"%s\",\"type\":\"%s\"}"
          (brdEsc (if net (brdGet2 net 'name) ""))
          (brdN (car xy) scale) (brdN (cadr xy) scale)
          (brdN dr scale) (brdN pad scale)
          (brdEsc (or (brdGet2 ps 'name) ""))
          (brdEsc (or sl "")) (brdEsc (or el ""))
          (brdEsc (string vt))
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- Pins/Pads (焊盘, 含XY/网络/层/尺寸) ----------
(defun brdWritePads (design scale f maxItems)
  (let (pins pin net xy comp ps fig first cnt)
    (fprintf f "\"pads\": [")
    (setq first t) (setq cnt 0)
    (setq pins (brdGet2 design 'pins))
    (foreach pin (if (listp pins) pins nil)
      (when (< cnt maxItems)
        (setq cnt (cnt + 1))
        (setq net (brdGet2 pin 'net))
        (setq xy (brdGet2 pin 'xy))
        (setq comp (brdGet2 pin 'component))
        (setq ps (brdGet2 pin 'padstack))
        (setq fig (brdGet2 pin 'figure))
        (unless first (fprintf f ","))
        (setq first nil)
        (fprintf f "\n  {\"refdes\":\"%s\",\"pin\":\"%s\",\"net\":\"%s\",\"x\":%s,\"y\":%s,\"layer\":\"%s\",\"padstack\":\"%s\",\"is_smd\":%s,\"pad_w\":%s,\"pad_h\":%s,\"drill_mm\":%s}"
          (brdEsc (if comp (brdGet2 comp 'name) ""))
          (brdEsc (if (brdGet2 pin 'number) (string (brdGet2 pin 'number)) ""))
          (brdEsc (if net (brdGet2 net 'name) ""))
          (brdN (car xy) scale) (brdN (cadr xy) scale)
          (brdEsc (or (brdGet2 (brdGet2 pin 'layer) 'name) ""))
          (brdEsc (or (brdGet2 ps 'name) ""))
          (if (brdGet2 pin 'isSmd) "true" "false")
          (brdN (brdGet2 fig 'width) scale)
          (brdN (brdGet2 fig 'height) scale)
          (brdN (or (brdGet2 ps 'drillDiameter) 0) scale)
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- Shapes (铺铜, 记录网络/层/包围盒) ----------
(defun brdWriteShapes (design scale f maxItems)
  (let (shapes s net layer bbox first cnt)
    (fprintf f "\"shapes\": [")
    (setq first t) (setq cnt 0)
    (setq shapes (brdGet2 design 'shapes))
    (foreach s (if (listp shapes) shapes nil)
      (when (< cnt maxItems)
        (setq net (brdGet2 s 'net))
        (setq layer (brdGet2 s 'layer))
        (setq bbox (brdGet2 s 'bbox))
        (when (and net bbox)
          (setq cnt (cnt + 1))
          (unless first (fprintf f ","))
          (setq first nil)
          (fprintf f "\n  {\"net\":\"%s\",\"layer\":\"%s\",\"bbox\":[%s,%s,%s,%s]}"
            (brdEsc (brdGet2 net 'name))
            (brdEsc (if layer (brdGet2 layer 'name) ""))
            (brdN (car bbox) scale) (brdN (cadr bbox) scale)
            (brdN (caddr bbox) scale) (brdN (cadddr bbox) scale)
          )
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- DRC 违规 (含XY) ----------
(defun brdWriteDrc (scale f maxItems)
  (let (drcs d xy first cnt)
    (fprintf f "\"drc\": [")
    (setq first t) (setq cnt 0)
    (setq drcs (errset (axlDBGetDrc) t))
    (setq drcs (if drcs (car drcs) nil))
    (foreach d (if (listp drcs) drcs nil)
      (when (< cnt maxItems)
        (setq cnt (cnt + 1))
        (setq xy (brdGet2 d 'xy))
        (unless first (fprintf f ","))
        (setq first nil)
        (fprintf f "\n  {\"type\":\"%s\",\"severity\":\"%s\",\"x\":%s,\"y\":%s,\"layer\":\"%s\",\"text\":\"%s\"}"
          (brdEsc (or (brdGet2 d 'errorType) ""))
          (brdEsc (or (brdGet2 d 'severity) "error"))
          (brdN (car xy) scale) (brdN (cadr xy) scale)
          (brdEsc (or (brdGet2 (brdGet2 d 'layer) 'name) ""))
          (brdEsc (or (brdGet2 d 'text) ""))
        )
      )
    )
    (fprintf f "\n  ]")
  )
)

; ---------- 主导出 ----------
(defun brdExportJSON (@optional (outPath nil) (maxItems 500000))
  (let (design f scale units bname rev thk)
    (setq design (axlDBGetDesign))
    (unless design (error "无法获取当前设计, 请先打开 .brd"))
    (setq units (or (brdGet2 design 'units) "millimeter"))
    (setq scale (brdUnitScale units))
    (setq bname (or (brdGet2 design 'name) "board"))
    (setq rev (or (brdGet2 design 'revision) ""))

    ; 输出路径: 未指定则与 .brd 同目录
    (when (null outPath)
      (setq outPath (strcat (or (brdGet2 design 'filePath) ".") "_dfm_export.json"))
    )
    (setq f (outfile outPath "w"))
    (unless f (error "无法写入文件: %s" outPath))

    (fprintf f "{\n")
    (fprintf f "\"format\":\"%s\",\n" "brd-dfm-export")
    (fprintf f "\"version\":\"%s\",\n" "1.0")
    (fprintf f "\"tool\":\"Cadence Allegro PCB Editor\",\n")
    (fprintf f "\"source\":\"%s\",\n" (brdEsc (string bname)))
    (fprintf f "\"units\":\"mm\",\n")
    (fprintf f "\"source_units\":\"%s\",\n" (brdEsc (string units)))
    (fprintf f "\"board\":{\"name\":\"%s\",\"revision\":\"%s\",\"thickness_mm\":%s,\"layer_count\":%s},\n"
      (brdEsc (string bname)) (brdEsc (string rev))
      (brdN (brdGet2 design 'thickness) scale)
      (if (numberp (brdGet2 design 'layerCount)) (sprintf nil "%d" (brdGet2 design 'layerCount)) "null"))

    (brdWriteOutline design scale f) (fprintf f ",\n")
    (brdWriteStackup design scale f) (fprintf f ",\n")
    (brdWriteNets design scale f maxItems) (fprintf f ",\n")
    (brdWriteComponents design scale f maxItems) (fprintf f ",\n")
    (brdWriteClines design scale f maxItems) (fprintf f ",\n")
    (brdWriteVias design scale f maxItems) (fprintf f ",\n")
    (brdWritePads design scale f maxItems) (fprintf f ",\n")
    (brdWriteShapes design scale f maxItems) (fprintf f ",\n")
    (brdWriteDrc scale f maxItems) (fprintf f "\n")
    (fprintf f "}\n")
    (close f)

    (printf "\n[BRD-AI] 导出完成: %s\n" outPath)
    (printf "[BRD-AI] 请把该 JSON 载入 BRD-AI『分析BRD-DFM』生成 Valor 式报告。\n\n")
    outPath
  )
)

(printf "\n[BRD-AI] brd_dfm_export.il 已加载。执行 brdExportJSON 或 (brdExportJSON \"D:/out/brd_export.json\") 导出数据。\n")
"""


def export_skill_text() -> str:
    """返回 SKILL 导出脚本正文"""
    return EXPORT_SKILL


def write_export_skill(output_path: str = None) -> str:
    """写出 brd_dfm_export.il, 返回文件路径"""
    if not output_path:
        base = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        output_path = os.path.join(base, EXPORT_SKILL_FILENAME)
    parent = os.path.dirname(os.path.abspath(output_path))
    if parent:
        os.makedirs(parent, exist_ok=True)
    with open(output_path, "w", encoding="utf-8") as f:
        f.write(EXPORT_SKILL)
    return output_path


def default_export_dir() -> str:
    return os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "output")
