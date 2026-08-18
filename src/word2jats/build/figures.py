"""图片处理：从 docx 内嵌媒体外部化 + 构建 ``<fig>``。

JATS 不把图片存进 XML：需把图片外部化为 ``{article-id}/fig-0N.<原格式>`` 并在 XML 中
以 ``<graphic xlink:href>`` 引用。图片一律从 docx 内嵌媒体（按正文出现顺序）提取，保留
原始格式字节、不重编码。主通道由 LLM 按位置把图注关联到相邻的内嵌图（image_ph）；本
模块的 FigureSource 只在个别图未被关联时，按文档顺序兜底取第 n 张内嵌图。
"""

from __future__ import annotations

import os
import re
import hashlib
from typing import Optional

from .jats import E, append_inline, sub


class FigureSource:
    """图片来源：按正文出现顺序（1-based）返回 docx 内嵌图的原始字节，
    供未被 LLM 按位置关联的图兜底取用。"""

    def __init__(self):
        self._items = []  # list[(name, blob)]：正文出现顺序的内嵌图（已排除 fallback 占位图）

    @classmethod
    def from_docx_media(cls, doc, body_image_order: list) -> "FigureSource":
        """按正文出现顺序取内嵌图片（已排除 fallback 占位图）。"""
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
        """返回第 n 张图（1-based）的 (name, blob)，越界返回 None。"""
        if 1 <= n <= len(self._items):
            return self._items[n - 1]
        return None


def media_format(blob: bytes) -> Optional[str]:
    """只根据字节特征识别格式；未知就返回 None，绝不伪装成 JPEG。"""
    if not blob:
        return None
    if blob[:3] == b"\xff\xd8\xff":
        return "jpeg"
    if blob[:8] == b"\x89PNG\r\n\x1a\n":
        return "png"
    if blob[:4] in (b"II*\x00", b"MM\x00*"):
        return "tiff"
    if blob[:6] in (b"GIF87a", b"GIF89a"):
        return "gif"
    if blob[:2] == b"BM":
        return "bmp"
    if blob[:4] == b"\xd7\xcd\xc6\x9a":
        return "wmf"
    if len(blob) >= 44 and blob[:4] == b"\x01\x00\x00\x00" and blob[40:44] == b" EMF":
        return "emf"
    head = blob[:512].lstrip(b"\xef\xbb\xbf\x00\t\r\n ")
    if re.search(br"<(?:[A-Za-z_][\w.-]*:)?svg(?:\s|>)", head, re.I):
        return "svg"
    return None


_EXTENSION = {
    "jpeg": ".jpg", "png": ".png", "tiff": ".tif", "gif": ".gif",
    "bmp": ".bmp", "wmf": ".wmf", "emf": ".emf", "svg": ".svg",
}


def ext_for_blob(blob: bytes) -> str:
    """按真实字节格式取扩展名；未知格式显式使用 .bin，交由出口门拦截。"""
    return _EXTENSION.get(media_format(blob), ".bin")


def write_image_blob(out_dir: str, rel_noext: str, blob: bytes) -> str:
    """把图片原始字节写到 out_dir/rel_noext.<原格式扩展名>，返回相对路径。字节忠实、不重编码。"""
    rel = rel_noext + ext_for_blob(blob)
    dest = os.path.join(out_dir, rel)
    os.makedirs(os.path.dirname(dest), exist_ok=True)
    with open(dest, "wb") as f:
        f.write(blob)
    return rel


class FigureBuilder:
    def __init__(self, source: Optional[FigureSource], article_id: str,
                 out_dir: str, ids=None):
        self.source = source
        self.article_id = article_id or "article"
        self.out_dir = out_dir
        self.ids = ids
        self.exported = []  # 已导出的相对路径
        self.hashes = {}    # 相对路径 → docx 源字节 SHA-256
        self.numbers = []   # 已生成图的编号
        self.number_to_id = {}

    def _export(self, n: int) -> Optional[str]:
        """兜底通道：按文档顺序取第 n 张内嵌图，原格式字节外部化。"""
        if not self.source:
            return None
        item = self.source.get(n)
        if not item:
            return None
        _, blob = item
        rel = write_image_blob(self.out_dir, "%s/fig-%02d" % (self.article_id, n), blob)
        self.exported.append(rel)
        self.hashes[rel] = hashlib.sha256(blob).hexdigest()
        return rel

    def build_fig(self, number: int, caption_runs, inline_math=None, label=None,
                  image_blob=None):
        self.numbers.append(number)
        fid = self.ids.take("figure") if self.ids else "F%03d" % number
        self.number_to_id.setdefault(number, fid)
        fig = E("fig", id=fid, position="float")
        sub(fig, "label", label or "Fig. %d." % number)   # 优先用 docx 原始 label 前缀
        if caption_runs:
            cap = sub(fig, "caption")
            p = sub(cap, "p")
            append_inline(p, caption_runs, inline_math)
        # 优先用 LLM 按位置关联的 docx 原图字节（image_ph 通道）；无则按文档顺序兜底取第 n 张内嵌图
        if image_blob:
            rel = write_image_blob(self.out_dir, "%s/fig-%02d" % (self.article_id, number), image_blob)
            self.exported.append(rel)
            self.hashes[rel] = hashlib.sha256(image_blob).hexdigest()
            href = rel
        else:
            href = self._export(number)
        if href:
            sub(fig, "graphic", **{"xlink_href": href})
            fig[-1].set("id", self.ids.take("graphic") if self.ids else "%s.g1" % fid)
        return fig
