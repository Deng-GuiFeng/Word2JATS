"""表格无损兜底(稳健性)单测——模型抖动致表格构建失败时,绝不静默丢表/串号。

覆盖:
- 图片表 → 确定性外部化为含 <graphic> 的 table-wrap(图片就是图片,只加结构、不 OCR 造字;
  表计数不变、内容不丢、DTD 合规);
- 制表符表 → 确定性还原为 table-wrap(文字在 docx 里,切分归属结构标签,不依赖 LLM)。

均为确定性行为(--llm off 也生效),但列切分/兜底路径正常样例评测覆盖不全,故单测守门。
"""
import io
import os

from PIL import Image

from word2jats.build.context import BuildContext
from word2jats.build.figures import FigureBuilder
from word2jats.build.formulas import FormulaBuilder
from word2jats.build.jats import E
from word2jats.build.tables import TableBuilder
from word2jats.model.blocks import ImageRun, Paragraph, TextRun


def _png_bytes():
    im = Image.new("RGB", (12, 9), (200, 30, 30))
    b = io.BytesIO()
    im.save(b, format="PNG")
    return b.getvalue()


def _ctx(tmp_path):
    return BuildContext(
        FormulaBuilder(), FigureBuilder(None, "ART", str(tmp_path)),
        TableBuilder(), llm=None, out_dir=str(tmp_path), article_id="ART")


def test_image_table_fallback_produces_graphic(tmp_path):
    ctx = _ctx(tmp_path)
    cap = Paragraph(runs=[TextRun(text="Table 1. Patient demographics.")])
    img = ImageRun(part_name="word/media/t.png", blob=_png_bytes(), fmt="PNG")
    wrap = ctx.build_image_table_fallback(cap, img)
    assert wrap is not None
    assert wrap.tag == "table-wrap"
    assert wrap.findtext("label") == "Table 1."
    assert wrap.find("caption/p") is not None
    g = wrap.find("graphic")
    assert g is not None
    href = g.get("{http://www.w3.org/1999/xlink}href")
    assert href and href.endswith("table-01.jpg")
    # 表图确实外部化落盘了
    assert os.path.exists(os.path.join(str(tmp_path), href))
    # 表计数推进了(不丢表)
    assert ctx.tables._n == 1
    assert 1 in ctx.tables.numbers


def test_image_table_fallback_no_blob_returns_none(tmp_path):
    ctx = _ctx(tmp_path)
    cap = Paragraph(runs=[TextRun(text="Table 1. X.")])
    img = ImageRun(part_name="x", blob=None, fmt="PNG")
    assert ctx.build_image_table_fallback(cap, img) is None


def test_image_table_fallback_is_dtd_valid_in_context(tmp_path):
    """把兜底 table-wrap 注入一篇已知合法的最小 article,确认 DTD 仍通过
    (验证 graphic 确实是 table-wrap 的合法子元素,而非仅凭实体阅读推断)。"""
    from word2jats.build.jats import make_article, serialize, sub
    from word2jats.validate.validator import Validator

    ctx = _ctx(tmp_path)
    cap = Paragraph(runs=[TextRun(text="Table 1. Demographics.")])
    img = ImageRun(part_name="t.png", blob=_png_bytes(), fmt="PNG")
    wrap = ctx.build_image_table_fallback(cap, img)

    art = make_article("research-article", "en")
    front = sub(art, "front")
    jm = sub(front, "journal-meta")
    sub(jm, "journal-id", "T", **{"journal-id-type": "publisher"})
    jt = sub(jm, "journal-title-group")
    sub(jt, "journal-title", "T")
    sub(jm, "issn", "1234-5678", **{"pub-type": "epub"})
    pub = sub(jm, "publisher")
    sub(pub, "publisher-name", "P")
    am = sub(front, "article-meta")
    tg = sub(am, "title-group")
    sub(tg, "article-title", "A")
    body = sub(art, "body")
    sec = sub(body, "sec")
    sub(sec, "title", "S")
    sec.append(wrap)  # table-wrap(label+caption+graphic)放进 sec 的 prose 区

    res = Validator().validate_bytes(serialize(art))
    assert res.ok, "兜底 table-wrap 应通过 DTD: %r" % getattr(res, "errors", None)


def test_tab_table_deterministic_builds_table(tmp_path):
    """制表符表(文字在 docx 里)→ **确定性**还原为 table-wrap,不依赖 LLM、不丢字、不误挂。

    这是被纠正后的正确行为:制表符表的内容本就是 docx 文本,只需切分归属结构标签,
    不再退化成一堆 <p>,也不再交给 LLM 结构化(--llm off 也生效)。
    """
    from word2jats.build.body import _render_blocks

    ctx = _ctx(tmp_path)  # llm=None(确定性档)
    blocks = [
        Paragraph(runs=[TextRun(text="Table 2. Outcomes.")]),
        Paragraph(runs=[TextRun(text="Group\tN\tRate")]),
        Paragraph(runs=[TextRun(text="A\t10\t0.5")]),
        Paragraph(runs=[TextRun(text="B\t12\t0.6")]),
    ]
    parent = E("sec")
    _render_blocks(parent, blocks, ctx, "s1")

    wrap = parent.find("table-wrap")
    assert wrap is not None, "制表符表应被确定性还原为 table-wrap"
    assert wrap.findtext("label") == "Table 2."
    assert len(wrap.findall("table/thead/tr/th")) == 3      # 表头 3 列
    assert len(wrap.findall("table/tbody/tr")) == 2         # 2 个数据行
    # 不把题注挂成 pending(否则会串到下一张真实表)、内容全在
    assert ctx._pending_table_caption is None
    text_all = "".join(wrap.itertext())
    assert "Outcomes" in text_all and "Group" in text_all and "0.6" in text_all
