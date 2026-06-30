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
        self._items = []  # list[(name, blob)]

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
        """返回第 n 张（1-based）的 (name, blob)。"""
        if 1 <= n <= len(self._items):
            return self._items[n - 1]
        return None


def _is_image_name(name: str) -> bool:
    return bool(re.search(r"\.(jpe?g|png|tiff?|gif|bmp|emf|wmf)$", name, re.I))


def _num_in_name(name: str) -> int:
    m = re.search(r"(\d+)", name)
    return int(m.group(1)) if m else 0


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
        try:
            im = Image.open(io.BytesIO(blob))
            if im.mode in ("CMYK", "P", "RGBA", "LA"):
                im = im.convert("RGB")
            elif im.mode not in ("RGB", "L"):
                im = im.convert("RGB")
            im.save(dest, "JPEG", quality=90)
        except Exception:
            # 无法转换则原样写出（保留扩展名信息）
            with open(dest, "wb") as f:
                f.write(blob)
        self.exported.append(rel)
        return rel

    def build_fig(self, number: int, caption_runs, inline_math=None) -> "etree._Element":
        self.numbers.append(number)
        fid = "F%03d" % number
        fig = E("fig", id=fid, position="float")
        sub(fig, "label", "Fig. %d." % number)
        if caption_runs:
            cap = sub(fig, "caption")
            p = sub(cap, "p")
            append_inline(p, caption_runs, inline_math)
        href = self._export(number)
        if href:
            sub(fig, "graphic", **{"xlink_href": href})
            fig[-1].set("id", "%s.g1" % fid)
        return fig
