"""图片处理：外部化 + 构建 ``<fig>``。

依据补充说明与实测：XML 不存图，需把图片外部化为 ``{article-id}/fig-0N.jpg`` 并在
XML 中以 ``<graphic xlink:href>`` 引用。图片来源优先用外部图片包（若提供），否则从
docx 内嵌媒体按文档顺序提取；统一转 JPG。
"""

from __future__ import annotations

import io
import os
import re
import zipfile
from typing import Optional

from PIL import Image

from ..model.blocks import ImageRun
from .jats import E, append_inline, sub

Image.MAX_IMAGE_PIXELS = None


class FigureSource:
    """图片来源：按图号（1-based）返回原始字节。"""

    def __init__(self):
        self._items = []      # list[(name, blob)]：全部图片（按名内数字排序）
        self._fig_items = None  # 图片包区分"图/图片表"时，仅"图"（fig-0N）按图号排序的子列表

    @classmethod
    def from_package(cls, path: str) -> "FigureSource":
        self = cls()
        items = []
        if os.path.isdir(path):
            for fn in sorted(os.listdir(path)):
                fp = os.path.join(path, fn)
                if os.path.isfile(fp) and _is_image_name(fn):
                    with open(fp, "rb") as f:
                        items.append((fn, f.read()))
        elif zipfile.is_zipfile(path):
            with zipfile.ZipFile(path) as z:
                for n in sorted(z.namelist()):
                    if _is_image_name(n) and not n.endswith("/"):
                        items.append((os.path.basename(n), z.read(n)))
        # 按文件名中的数字排序（fig-01, fig-02 ...）
        items.sort(key=lambda x: _num_in_name(x[0]))
        self._items = items
        # 图片包常同时含"图"(fig-0N)与"图片表"(table-0N)；二者各自从 1 编号。get(n) 服务的是
        # 图（figure），必须只在"图"子列表里取第 n 张——否则会因两类按编号混排而错位
        # （如 figure 2 取到 table-01 的字节，实测 02）。仅当能识别出"图"名时启用该子列表。
        figs = [it for it in items if _is_figure_name(it[0])]
        self._fig_items = figs if figs else None
        return self

    @classmethod
    def from_docx_media(cls, doc, body_image_order: list) -> "FigureSource":
        """按正文出现顺序取内嵌图片（已排除 fallback）。"""
        self = cls()
        seen = set()
        for img in body_image_order:
            if img.part_name in seen or img.is_fallback or not img.blob:
                continue
            seen.add(img.part_name)
            self._items.append((img.part_name, img.blob))
        return self

    def __len__(self):
        return len(self._items)

    def get(self, n: int):
        """返回第 n 张图（1-based）的 (name, blob)。图片包区分图/图片表时只在"图"子列表里取。"""
        pool = self._fig_items if self._fig_items is not None else self._items
        if 1 <= n <= len(pool):
            return pool[n - 1]
        return None


def _is_image_name(name: str) -> bool:
    return bool(re.search(r"\.(jpe?g|png|tiff?|gif|bmp|emf|wmf)$", name, re.I))


def _is_figure_name(name: str) -> bool:
    """图片包里"图"的命名判定：含 fig/figure、且不是 table/scheme（图片表另行按 docx 占位符导出）。"""
    base = os.path.basename(name).lower()
    return ("fig" in base) and ("table" not in base) and ("tab-" not in base)


def _num_in_name(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


def ext_for_blob(blob: bytes) -> str:
    """按字节魔数判图片格式,返回外部化文件的扩展名(保留原格式,不转码)。"""
    if not blob:
        return ".jpg"
    if blob[:3] == b"\xff\xd8\xff":
        return ".jpg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return ".png"
    if blob[:4] in (b"II*\x00", b"MM\x00*"):
        return ".tif"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return ".gif"
    if blob[:2] == b"BM":
        return ".bmp"
    return ".jpg"


def write_image_blob(out_dir: str, rel_noext: str, blob: bytes) -> str:
    """把图片原始字节写到 out_dir/rel_noext.<原格式扩展名>,返回相对路径。字节忠实、不重编码。"""
    rel = rel_noext + ext_for_blob(blob)
    dest = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(blob)
    return rel


class FigureBuilder:
    def __init__(self, source: Optional[FigureSource], article_id: str,
                 out_dir: str):
        self.source = source
        self.article_id = article_id or "article"
        self.out_dir = out_dir
        self.exported = []  # 已导出的相对路径
        self.numbers = []   # 已生成图的编号

    def _export(self, n: int) -> Optional[str]:
        if not self.source:
            return None
        item = self.source.get(n)
        if not item:
            return None
        _, blob = item
        rel = "%s/fig-%02d.jpg" % (self.article_id, n)
        dest = os.path.join(self.out_dir, rel)
        os.makedirs(os.path.dirname(dest), exist_ok=True)
        # 直写源图**原始字节**,不重编码——外部化只是搬运,改变字节会与金标准/figures.zip
        # 逐字节 md5 不符(实测:PIL 重存 JPEG 使所有图 md5 失配)。
        with open(dest, "wb") as f:
            f.write(blob)
        self.exported.append(rel)
        return rel

    def build_fig(self, number: int, caption_runs, inline_math=None, label=None,
                  image_blob=None) -> "etree._Element":
        self.numbers.append(number)
        fid = "F%03d" % number
        fig = E("fig", id=fid, position="float")
        sub(fig, "label", label or "Fig. %d." % number)   # 优先用 docx 原始 label 前缀
        if caption_runs:
            cap = sub(fig, "caption")
            p = sub(cap, "p")
            append_inline(p, caption_runs, inline_math)
        # 优先用 LLM 按位置关联的 docx 原图字节(image_ph 通道);无则退回 FigureSource(figures.zip)
        if image_blob:
            rel = write_image_blob(self.out_dir, "%s/fig-%02d" % (self.article_id, number), image_blob)
            self.exported.append(rel)
            href = rel
        else:
            href = self._export(number)
        if href:
            sub(fig, "graphic", **{"xlink_href": href})
            fig[-1].set("id", "%s.g1" % fid)
        return fig
