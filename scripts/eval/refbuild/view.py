"""把块清单渲染成人/模型可读的定长视图：每行一块，idx + 样式 + 载体标记 + 逐字文本。
判定阶段只读这个视图，输出 idx→元素 的决策，绝不重打文字。
"""
import json
import sys


def mark(b):
    m = []
    if b["kind"] == "tbl":
        rows = b["table"]["rows"]
        ncols = max((sum(c["span"] for c in r["cells"]) for r in rows), default=0)
        nhdr = sum(1 for r in rows if r["header"])
        m.append("表格 %d行×%d列 表头行%d" % (len(rows), ncols, nhdr))
    if b["media"]:
        m.append("图×%d(%s)" % (len(b["media"]),
                                ",".join(x["target"].rsplit("/", 1)[-1] for x in b["media"])))
    if b["omml"]:
        m.append("OMML×%d" % len(b["omml"]))
    if b["ole"]:
        m.append("OLE(%s)" % ",".join((o["progId"] or "?") for o in b["ole"]))
    sup = [r["t"] for r in b["runs"] if r.get("va") == "superscript"]
    if sup:
        m.append("上标:%s" % "|".join(sup))
    it = [r["t"] for r in b["runs"] if r.get("i")]
    if it:
        m.append("斜体:%s" % "|".join(t[:24] for t in it[:4]))
    return " ".join(m)


def render(key, max_text=None, skip_style=None):
    d = json.load(open("tmp/refbuild/%s.blocks.json" % key, encoding="utf-8"))
    out = []
    for b in d["blocks"]:
        if b["in_table"] is not None:
            continue                      # 表内段落不单列，随表块一起
        if skip_style and b["style"] == skip_style:
            continue
        t = b["text"].replace("\n", "⏎").replace("\t", "→")
        if max_text and len(t) > max_text:
            t = t[:max_text] + "…〔共%d字〕" % len(b["text"])
        mk = mark(b)
        out.append("[%d] {%s}%s %s" % (b["idx"], b["style"] or "-",
                                       (" ⟨%s⟩" % mk) if mk else "", t))
    return "\n".join(out)


if __name__ == "__main__":
    key = sys.argv[1]
    mt = int(sys.argv[2]) if len(sys.argv) > 2 else None
    sk = sys.argv[3] if len(sys.argv) > 3 else None
    print(render(key, mt, sk))
