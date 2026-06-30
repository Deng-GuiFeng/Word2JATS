"""表格无损兜底(稳健性)单测——模型抖动致表格构建失败时,绝不静默丢表/串号。

覆盖审计确认的 Tier-1 问题修复:
- 图片表 VLM 重建失败 → 兜底为含 <graphic> 的 table-wrap(表计数不变、内容不丢、DTD 合规);
- 制表符表结构化失败 → 题注与各制表符行就地渲为 <p>(内容不丢、题注不误挂到下一张真实表)。

这两条路径仅在模型调用失败时触发,正常样例评测覆盖不到,故必须单测守门。
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


def test_tab_table_fallback_preserves_paragraphs(tmp_path, monkeypatch):
    """制表符表结构化失败时,题注与制表符行不丢、不误挂(渲为 <p>)。"""
    from word2jats.build.body import _render_blocks

    ctx = _ctx(tmp_path)
    # 让 vision_ok=True 且 build_text_table 必失败(模拟模型抖动)
    ctx.llm = type("StubLLM", (), {"enabled": True})()
    monkeypatch.setattr(ctx, "build_text_table", lambda *a, **k: None)

    blocks = [
        Paragraph(runs=[TextRun(text="Table 2. Outcomes.")]),
        Paragraph(runs=[TextRun(text="Group\tN\tRate")]),
        Paragraph(runs=[TextRun(text="A\t10\t0.5")]),
        Paragraph(runs=[TextRun(text="B\t12\t0.6")]),
    ]
    parent = E("sec")
    _render_blocks(parent, blocks, ctx, "s1")

    ps = parent.findall("p")
    assert len(ps) >= 4, "题注 + 3 行应全部保留为段落,实际 %d" % len(ps)
    # 不静默丢表为空、也不把题注挂成 pending(否则会串到下一张真实表)
    assert ctx._pending_table_caption is None
    assert parent.find("table-wrap") is None
    text_all = " ".join("".join(p.itertext()) for p in ps)
    assert "Outcomes" in text_all and "Group" in text_all and "0.6" in text_all
