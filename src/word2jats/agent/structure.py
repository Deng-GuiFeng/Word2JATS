"""Agent 闭环——LLM 主导的正文章节结构修正(质量核心之一)。

为什么需要它(第一性原理):
  正文的章节层级,在 docx 里由标题样式(Heading 1/2/3)与"仅加粗的 run-in 小标题"
  共同决定,而**章节编号往往是出版时才加的、源文本里并不存在**。纯启发式(classify)
  对"仅加粗、无样式"的 run-in 子标题无从判断层级,实测会把它们误升为顶级节
  (样例1:把隶属 Model validation 的 "Internal/External validation" 升成与 Results
  平级的顶级节,并丢失 "Model validation" 这一 Heading 3 标题)。

本模块不打补丁式地猜,而是把**源 docx 的标题样式序列**(权威层级证据)+ 当前 XML 的
章节大纲交给 LLM,由 LLM 判定每个标题的正确层级与角色(成节 / 仅是加粗 run-in 引导句),
再据此**移动已有内容块**重建 <body>。只移动、不重写,保证内容守恒;重建后即时校验 DTD。
这两条(DTD 合法、内容不丢)是仅有的、且明确披露的硬约束,其余判断全部由 LLM 决定。
"""
from __future__ import annotations

import copy
import re

from lxml import etree

from ..build.jats import E, sub
from ..model.blocks import Paragraph


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", (s or "")).strip().lower().strip(" .:：、,，;；")


# --------------------------- 源 docx 标题样式证据 --------------------------- #
_CAP_RE = re.compile(r"^\s*(table|figure|fig\.?|scheme|appendix|supplementary)\s*\d", re.I)


def _heading_signal(para: Paragraph):
    """返回该段的"标题样式信号":H1/H2/H3…(来自样式名) / bold / italic / None。

    题注(Table N/Fig N…)显式排除——它们是图表标题,不是章节标题。
    """
    t = para.text.strip()
    if not t or len(t) > 200 or "\t" in para.text:
        return None
    if _CAP_RE.match(t):
        return None
    sname = (para.style_name or "").lower()
    if "heading" in sname:
        m = re.search(r"(\d)", sname)
        return "H%s" % (m.group(1) if m else "1")
    if sname in ("title", "subtitle"):
        return "H1"
    words = t.split()
    if para.is_bold and len(words) <= 16 and not t.endswith("."):
        return "bold"
    if para.is_italic and len(words) <= 16 and not t.endswith("."):
        return "italic"
    return None


def _next_nonempty_block(blocks, i):
    for j in range(i + 1, len(blocks)):
        b = blocks[j]
        if isinstance(b, Paragraph):
            if b.text.strip():
                return b
        else:
            return b  # 原生 Table 等
    return None


def source_heading_seq(doc) -> list:
    """源 docx 按文档顺序的标题候选序列:[{text, signal}]。供 LLM 作层级证据。

    关键过滤:仅靠加粗/斜体识别的候选,若其**后紧跟制表符数据行**,则它是"表格分组标签"
    (如样例5 的 Demographic / Lab values / Adverse events),不是章节标题,予以排除——
    避免 LLM 据此幻觉出伪小节。带标题样式(Heading k)的不受此限。
    """
    out = []
    blocks = doc.blocks
    for i, b in enumerate(blocks):
        if not isinstance(b, Paragraph):
            continue
        sig = _heading_signal(b)
        if not sig:
            continue
        if sig in ("bold", "italic"):
            nxt = _next_nonempty_block(blocks, i)
            if isinstance(nxt, Paragraph) and "\t" in nxt.text:
                continue   # 表格分组标签,非章节标题
        out.append({"text": b.text.strip(), "signal": sig})
    return out


# --------------------------- 当前 XML 章节大纲 --------------------------- #
def _direct_content(sec):
    """一个 <sec> 的直接内容子元素(排除 title 与子 sec);这些是要被搬运的块。"""
    out = []
    for ch in sec:
        ln = etree.QName(ch).localname
        if ln in ("title", "sec"):
            continue
        out.append(ch)
    return out


def current_body_outline(body):
    """当前 <body> 的章节大纲(DFS,带深度)+ 标题→直接内容块映射 + 顶层散块。

    返回 (outline, content_map, leading_blocks):
      outline: [{title, depth}]   (文档顺序)
      content_map: {norm_title: [直接内容元素...]}
      leading_blocks: body 下、第一个 sec 之前的直接内容元素(隐式首节)
    """
    outline, content_map = [], {}
    leading = [ch for ch in body if etree.QName(ch).localname not in ("sec",)]

    def walk(parent, depth):
        for sec in parent.findall("sec"):
            title = sec.findtext("title") or ""
            outline.append({"title": title.strip(), "depth": depth})
            content_map[_norm(title)] = _direct_content(sec)
            walk(sec, depth + 1)

    walk(body, 0)
    return outline, content_map, leading


# --------------------------- LLM 提议正确层级 --------------------------- #
_SYS = "你是严谨的学术排版结构核对员。只依据给定证据判断章节层级,只输出 JSON。"


def _user_prompt(source_seq, outline) -> str:
    src_lines = []
    for h in source_seq:
        src_lines.append("[%s] %s" % (h["signal"], h["text"]))
    cur_lines = []
    for o in outline:
        cur_lines.append("%s%s" % ("  " * o["depth"], o["title"]))
    return (
        "下面是同一篇论文正文的两份证据,请判定**正文章节的正确层级树**。\n\n"
        "【证据 A:源 docx 标题样式序列(按文档顺序,权威层级信号)】\n"
        "样式含义:H1=一级标题、H2=二级、H3=三级…;bold/italic=源里仅用加粗/斜体呈现、"
        "无标题样式的行(可能是真子标题,也可能只是段首加粗引导词,需你据语义判断)。\n"
        + "\n".join(src_lines) + "\n\n"
        "【证据 B:当前程序生成的 XML 章节树(缩进=嵌套,可能有错)】\n"
        + "\n".join(cur_lines) + "\n\n"
        "任务:输出修正后的**正文**章节大纲(JSON)。规则:\n"
        "1. 层级**以证据 A 的样式为准**:Hk 标题的相对深度由 k 决定(H1 是顶级=level 1,"
        "其下 H2=level 2,H3=level 3…),与最近的更高级标题构成父子。\n"
        "2. bold/italic 行:若它在语义上是某节下的真正子标题,给它合适的 level 并 role=\"section\";"
        "若它只是段首加粗引导词(如 'Internal validation' 引导一段说明,隶属上面的 'Model validation'),"
        "则 role=\"runin\"(不单独成节,作为该节内的加粗引导句保留)。\n"
        "3. 只输出**正文**标题(到 Conclusions/Discussion 为止)。**不要**包含:图题/表题、"
        "致谢/资助/利益冲突/伦理/作者贡献等后置声明、References、Abstract/Keywords。\n"
        "4. title 必须**逐字照抄**证据里的标题原文(用于回填内容);不要加/去章节编号。\n"
        "5. status:该标题在证据 B(当前 XML)里已存在=\"existing\";仅在证据 A 里有、B 里缺失="
        "\"new\"(如被程序漏掉的 'Model validation')。\n\n"
        '严格输出 JSON:{"outline":[{"title":"原文","level":整数,'
        '"role":"section"|"runin","status":"existing"|"new"}]}'
    )


def propose_outline(llm, source_seq, outline):
    if llm is None or not getattr(llm, "enabled", False):
        return None
    data = llm.extract_json(_SYS, _user_prompt(source_seq, outline), max_tokens=3072)
    if not isinstance(data, dict):
        return None
    items = data.get("outline")
    if not isinstance(items, list) or not items:
        return None
    clean = []
    for it in items:
        if not isinstance(it, dict):
            continue
        title = str(it.get("title", "")).strip()
        if not title:
            continue
        try:
            level = int(it.get("level", 1))
        except Exception:
            level = 1
        role = it.get("role", "section")
        clean.append({"title": title, "level": max(1, level),
                      "role": "runin" if role == "runin" else "section",
                      "status": "new" if it.get("status") == "new" else "existing"})
    return clean or None


# --------------------------- 据提议重建 <body> --------------------------- #
def _texts(el) -> set:
    """收集元素下所有 <p>/<title>/<td>/<th> 的归一文本(非空),用于内容守恒校验。"""
    out = set()
    for node in el.iter():
        ln = etree.QName(node).localname
        if ln in ("p", "title", "td", "th", "label", "caption"):
            t = _norm("".join(node.itertext()))
            if t:
                out.add(t)
    return out


def rebuild_body(body, proposed):
    """据 LLM 提议的大纲重建 <body>(移动已有内容块)。返回新 body 或 None(放弃)。"""
    outline, content_map, leading = current_body_outline(body)
    cur_titles = {_norm(o["title"]) for o in outline}
    # 用"标题是否在当前章节里"判定覆盖(不信 LLM 的 status 字段——它常把已有节误标 new)。
    # 安全:每个当前章节都必须出现在提议里(否则其内容无处安放=丢内容),否则放弃。
    prop_titles = {_norm(p["title"]) for p in proposed}
    missing = cur_titles - prop_titles
    if missing:
        return None, "提议未覆盖当前章节(可能丢内容),放弃: %s" % list(missing)[:5]

    new_body = E("body")
    for ch in leading:                      # 隐式首节散块(标题前内容)原样保留在 body 顶
        new_body.append(copy.deepcopy(ch))

    stack = []  # [(level, sec_element)]

    def parent_for(level):
        while stack and stack[-1][0] >= level:
            stack.pop()
        return stack[-1][1] if stack else new_body

    for p in proposed:
        blocks = content_map.get(_norm(p["title"]), [])
        if p["role"] == "section":
            par = parent_for(p["level"])
            sec = E("sec")
            sub(sec, "title", p["title"])
            for b in blocks:
                sec.append(copy.deepcopy(b))
            par.append(sec)
            stack.append((p["level"], sec))
        else:  # runin:作为当前节内的加粗引导句 + 其内容
            host = stack[-1][1] if stack else new_body
            pe = sub(host, "p")
            sub(pe, "bold", p["title"])
            for b in blocks:
                host.append(copy.deepcopy(b))

    # 内容守恒:重建后必须涵盖原 body 的全部文本(只增不减)
    before, after = _texts(body), _texts(new_body)
    lost = before - after
    if lost:
        return None, "内容守恒校验失败,丢失 %d 段文本,放弃" % len(lost)
    return new_body, "ok"


def correct_structure(llm, article, doc, visual=None, validator=None) -> dict:
    """正文章节结构修正主入口。就地替换 article 的 <body>。返回 trace 记录。

    证据:源 docx 标题样式(H1/H2/H3 是权威层级信号;bold/italic 是仅加粗/斜体的待判行)
    + 当前 XML 章节树;由 LLM 综合判定正确层级。视觉清点对"章节标题"噪声大(会把表格
    分组标签误当标题),故结构层级**不采信视觉标题**,改以 docx 样式为准——视觉真值仍用于
    内容(图/表/公式)核对。"""
    rec = {"applied": False, "reason": ""}
    body = article.find("body")
    if body is None:
        rec["reason"] = "无 body"
        return rec
    source_seq = source_heading_seq(doc)
    if not source_seq:
        rec["reason"] = "源无标题样式信号"
        return rec
    outline, _, _ = current_body_outline(body)
    proposed = propose_outline(llm, source_seq, outline)
    if not proposed:
        rec["reason"] = "LLM 未给出有效提议"
        return rec

    # 安全护栏:LLM 提议"新增"的章节,其标题必须确为源 docx 的标题候选(source_heading_seq
    # 已剔除"后接制表符行"的表格分组标签)。即:可恢复源里真有、却被程序漏掉的标题
    # (如样例1 的 Heading3 'Model validation'、S3 的 'Discussion'),但不得把表格分组标签、
    # 段首强调词等噪声凭空提升为新章节(防样例5 式幻觉)。这是"判断交给 LLM、底线守住"。
    cur_titles = {_norm(o["title"]) for o in outline}
    cand_titles = {_norm(h["text"]) for h in source_seq}
    # "真正新增"=不在当前章节里的 section(不信 LLM 的 status,据标题判定)。这些必须是
    # 源标题候选(已剔除表格分组标签),否则视为噪声幻觉,放弃。
    bad_new = [p["title"] for p in proposed
               if p.get("role") == "section"
               and _norm(p["title"]) not in cur_titles
               and _norm(p["title"]) not in cand_titles]
    if bad_new:
        rec["reason"] = "提议新增章节非源标题候选(疑似噪声),放弃: %s" % bad_new[:5]
        return rec

    new_body, msg = rebuild_body(body, proposed)
    if new_body is None:
        rec["reason"] = msg
        return rec

    # 与原结构对比:若层级/角色无变化则不动(避免无谓改写)
    old_sig = [(o["title"], o["depth"]) for o in outline]
    new_outline, _, _ = current_body_outline(new_body)
    new_sig = [(o["title"], o["depth"]) for o in new_outline]
    if old_sig == new_sig:
        rec["reason"] = "结构无需调整"
        return rec

    # DTD 校验:把新 body 装入 article 临时校验,失败则回滚
    article.replace(body, new_body)
    if validator is not None:
        from ..build.jats import serialize
        res = validator.validate_bytes(serialize(article))
        if not (res and res.ok):
            article.replace(new_body, body)  # 回滚
            rec["reason"] = "重建后 DTD 不通过,回滚"
            return rec

    rec["applied"] = True
    rec["old_outline"] = old_sig
    rec["new_outline"] = new_sig
    rec["proposed"] = proposed
    return rec
