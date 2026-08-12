"""参考文献著录拆分：把一条原文按体例切成 element-citation 的字段。

只用确定性正则，每个字段值都是原文的连续片段（切出来的，不是重写的）。
拆不动的条目不硬拆，原样标记出来单独处理——宁可留白，不许猜。
"""
import re

NBSP = " "


def _clean(s):
    return s.replace(NBSP, " ").strip()


def _split_persons(s, style):
    """作者段 → [(surname, given), ...] + 是否 et al。
    style='initials-after'  Vancouver: "Fernandez-Yague MA, Abbah SA"
    style='full-name'       全名:      "Austen R. Anderson, Blaine J. Fowers"
    style='surname-first'   APA:       "Li, M., Liu, C., & Xiao, C."
    """
    s = _clean(s)
    etal = False
    m = re.search(r",?\s*(et\s+al\.?)\s*$", s, re.I)
    if m:
        etal = True
        s = s[:m.start()].rstrip(" ,")
    out = []
    if style == "surname-first":
        parts = re.split(r",\s*(?=[^,]*?,)|,?\s*&\s*", s)
        parts = [p for p in re.split(r"(?<=\.)\s*,\s*|\s*&\s*", s) if p.strip()]
        for p in parts:
            p = p.strip().rstrip(",.")
            if not p:
                continue
            bits = [b.strip() for b in p.split(",", 1)]
            if len(bits) == 2:
                out.append((bits[0], bits[1]))
            else:
                return None, etal
    elif style == "initials-after":
        for p in re.split(r",\s*|\s+and\s+|\s*&\s*", s):
            p = p.strip()
            if not p:
                continue
            m = re.match(r"^(.*?)\s+((?:[^\Wa-z\d_]\.?){1,4})$", p)
            if not m:
                return None, etal
            out.append((m.group(1).strip(), m.group(2)))
    else:  # full-name: "Austen R. Anderson" / "S. de Jong" / "Collins M.N."
        for p in re.split(r",\s*|\s+and\s+|\s*&\s*", s):
            p = p.strip()
            if not p:
                continue
            toks = p.split()
            if len(toks) < 2:
                if toks:
                    out.append((toks[0], ""))
                    continue
                return None, etal
            # 末尾是纯缩写（"Collins M.N."）→ 姓在前
            if re.fullmatch(r"(?:[^\Wa-z\d_]\.?){1,4}", toks[-1]):
                out.append((" ".join(toks[:-1]), toks[-1]))
                continue
            # 姓的起点：从右往左吃，直到遇上一个"缩写词"（形如 X. / X）为止。
            # 枚举介词白名单必然漏（aan het / ten / ter / op 等），改用缩写形态判据。
            # 姓的起点：从右往左吃到最近的缩写词（形如 X. / X）为止；整串都没有
            # 缩写词时（"Matteo Rota"）回退成"末词为姓"。
            k = len(toks) - 1
            while k > 0 and not re.fullmatch(r"(?:[^\Wa-z\d_]\.?){1,4}", toks[k - 1]):
                k -= 1
            if k == 0:
                k = len(toks) - 1
            out.append((" ".join(toks[k:]), " ".join(toks[:k])))
    return (out or None), etal


_URL = re.compile(r"(https?://\S+)\s*$")
# "2020;263:113263"  /  "2015; 84: 1–29"  /  "2021;147:233-267"
_YVP = re.compile(r"(?P<year>(?:19|20)\d{2})\s*;\s*(?P<vol>\d+)"
                  r"(?:\s*\(\s*(?P<issue>[^)]+?)\s*\))?"
                  r"\s*:\s*(?P<fp>(?:Article|Artigo|No\.?)\s*\d+|[A-Za-z]?\d+)"
                  r"(?:\s*[-–]\s*(?P<lp>\d+))?")
# APA: "(2023)."
_APA_YEAR = re.compile(r"\(\s*(?P<year>(?:19|20)\d{2}[a-z]?)\s*\)\s*\.")


# 作者段锚定：Vancouver 两种人名写法，各自贪婪匹配到作者列表结束的那个句点
_NAME_WORD = r"[^\Wa-z\d_][\w'’\-]*"
# 姓氏里的小写介词（de/van/von/del/da/den/der/dos）是姓的一部分，不是断句
_PARTICLE = r"(?:de|van|von|del|della|da|dos|den|der|di|du|el|al|bin|ter)"
_NAME_SEQ = r"(?:(?:%(p)s\s+){0,2}%(w)s)" % {"w": _NAME_WORD, "p": _PARTICLE}
_AUTH_INITIALS = (r"(?:%(n)s(?:[\s\-]%(n)s)*\s+[A-Z]{1,4}\.?)" % {"n": _NAME_SEQ})
# 全名式：缩写可带点（"Brett D. Thombs" / "Collins M.N."）
_AUTH_FULL = (r"(?:(?:[^\Wa-z\d_]\.\s*|%(n)s\s+)+%(n)s)" % {"n": _NAME_SEQ})


def _anchor_authors(head, person_style):
    """返回 (作者段, 其余)。锚定到作者列表末尾那个句点，不靠通用句子切分——
    Vancouver 作者段结尾本身就是姓名缩写（"Biggs MJ."），通用规则会误判成缩写不切。"""
    unit = _AUTH_INITIALS if person_style == "initials-after" else _AUTH_FULL
    sep = r"(?:\s*,\s*|\s+and\s+|\s*&\s*|\s*,\s*and\s+)"
    pat = re.compile(r"^\s*(%s(?:%s%s)*(?:\s*,?\s*et\s+al\.?)?)\s*\.\s+" % (unit, sep, unit))
    m = pat.match(head)
    if not m:
        return None, head
    return m.group(1), head[m.end():]


def parse_vancouver(text, person_style):
    """`作者. 篇名. 刊名. 年;卷:页. url`（X03/X04）。"""
    s = text.rstrip()
    doi = None
    m = _URL.search(s)
    if m:
        doi = m.group(1).rstrip(".")
        s = s[:m.start()].rstrip()
    m = _YVP.search(s)
    if not m:
        return None
    head = s[:m.start()].rstrip().rstrip(".").rstrip()
    tail = s[m.end():].strip().lstrip(".").strip()

    authors_raw, rest = _anchor_authors(head, person_style)
    if not authors_raw:
        return None
    # rest = "篇名. 刊名"，刊名是最后一个 ". " 之后的部分
    cut = _last_sentence_cut(rest)
    if cut is None:
        return None
    title = rest[:cut].rstrip("." + NBSP + " \t")
    source = rest[cut:].strip()
    persons, etal = _split_persons(authors_raw, person_style)
    if not persons:
        return None
    d = {"persons": persons, "etal": etal, "article-title": _clean(title),
         "source": _clean(source), "year": m.group("year"),
         "volume": m.group("vol"), "fpage": m.group("fp"),
         "lpage": m.group("lp"), "issue": m.group("issue"),
         "uri": doi, "publication-type": "journal"}
    if tail:
        d["_residue"] = tail
    return d


def _last_sentence_cut(s):
    """找"篇名. 刊名"之间那一刀：取最后一个 ". " 且其后不是期刊名缩写续写。
    刊名内部可能含缩写点（"Adv. Drug Deliv. Rev."），故从右往左找第一个
    "后接大写词且该词不是单字母缩写" 的分界。"""
    cands = [m.end() for m in re.finditer(r"[.?!][\s ]+", s)]
    if not cands:
        return None
    for pos in reversed(cands):
        nxt = s[pos:].strip()
        if not nxt:
            continue
        first = re.split(r"[\s ]", nxt)[0].rstrip(".")
        if len(first) <= 2 and first.isupper():
            continue          # "J. Med." 这类缩写，继续往左找
        return pos
    return cands[0]


def parse_apa(text, source_hint=None):
    """`作者 (年). 篇名. 刊名, 卷(期), 页. DOI`（X02）。"""
    s = _clean(text).rstrip()
    doi = None
    mu = _URL.search(s)
    if mu:
        doi = mu.group(1).rstrip(".")
        s = s[:mu.start()].rstrip()
    m = _APA_YEAR.search(s)
    if not m:
        return None
    authors_raw = s[:m.start()].rstrip()
    rest = s[m.end():].strip()
    persons, etal = _split_persons(authors_raw, "surname-first")
    if not persons:
        return None
    segs = _split_sentences(rest)
    if len(segs) < 2:
        return None
    title = segs[0]
    src_part = ". ".join(segs[1:])

    # 刊名边界优先用 docx 的斜体事实（刊名整段设了斜体），标点切分只作兜底——
    # "Mining, Metallurgy & Exploration" 这类含逗号的刊名，按逗号切必被截断。
    source = None
    tail = src_part
    if source_hint and source_hint in src_part:
        source = source_hint
        tail = src_part[src_part.index(source_hint) + len(source_hint):]
    bits = [b.strip() for b in tail.rstrip(".").split(",")]
    if source is None:
        source = bits[0] if bits else ""
        bits = bits[1:]
    else:
        bits = [b for b in bits if b]

    vol = iss = fp = lp = None
    nums = [b for b in bits if b]
    if nums:
        mv = re.fullmatch(r"(\d+)(?:\s*\(\s*([^)]+?)\s*\))?", nums[0])
        if mv:
            vol, iss = mv.group(1), mv.group(2)
            nums = nums[1:]
        if nums:
            mm = re.fullmatch(r"([A-Za-z]?\d+)(?:\s*[-–]\s*(\d+))?", nums[0])
            if mm:
                fp, lp = mm.group(1), mm.group(2)
    if vol and fp is None and lp is None and len(vol) >= 5:
        vol, fp = None, vol         # 只有一个长数字：那是文章编号，不是卷号
    d = {"persons": persons, "etal": etal, "article-title": _clean(title),
         "source": _clean(source), "year": m.group("year"),
         "volume": vol, "fpage": fp, "lpage": lp, "issue": iss,
         "uri": doi, "publication-type": "journal"}
    return d


def _split_sentences(s):
    """按". "切，但不切开缩写（"J. Med."）、小数点、以及 et al. 之类。"""
    out, buf = [], ""
    i = 0
    while i < len(s):
        ch = s[i]
        buf += ch
        if ch == "." and i + 1 < len(s) and s[i + 1] in "  ":
            prev = buf[:-1].rstrip()
            # 缩写：句点前是单个大写字母，或常见期刊缩写词
            last_word = re.split(r"[\s,;:]", prev)[-1] if prev else ""
            nxt = s[i + 1:].lstrip()
            is_abbrev = (len(last_word) <= 2 and last_word[:1].isupper()) or \
                        last_word in ("Vol", "No", "pp", "eds", "ed", "al", "St", "Dr")
            if not is_abbrev and nxt and (nxt[0].isupper() or nxt[0].isdigit()):
                out.append(buf.rstrip(". ").strip())
                buf = ""
                i += 1
                while i < len(s) and s[i] in "  ":
                    i += 1
                continue
        i += 1
    if buf.strip():
        out.append(buf.rstrip(". ").strip())
    return [x for x in out if x]


STYLES = {
    "X02": ("apa", None),
    "X03": ("vancouver", "full-name"),
    "X04": ("vancouver", "initials-after"),
}


def italic_source(block):
    """docx 里刊名/书名整段设了斜体——这是出版体例的硬信号，比标点更可靠。
    取该条里最长的一段连续斜体文本作刊名候选。"""
    if not block:
        return None
    best, cur = "", ""
    for r in block.get("runs") or []:
        if r.get("i"):
            cur += r.get("t") or ""
        else:
            if len(cur.strip()) > len(best):
                best = cur.strip()
            cur = ""
    if len(cur.strip()) > len(best):
        best = cur.strip()
    best = _clean(best).strip(" .,;:")
    return best if len(best) >= 4 else None


def parse(key, text, block=None):
    kind, pstyle = STYLES[key]
    hint = italic_source(block)
    try:
        if kind == "apa":
            return parse_apa(text, source_hint=hint)
        d = parse_vancouver(text, pstyle)
        if d and hint and d.get("source") and d["source"] != hint and hint in _clean(text):
            # 斜体给出的刊名与标点切出来的不一致时，以 docx 的斜体事实为准
            d["source"] = hint
        return d
    except Exception:
        return None


def verify(d, text):
    """每个字段值必须逐字出现在原文里（折叠空白与 nbsp 后）。"""
    hay = re.sub(r"\s+", " ", text.replace(NBSP, " "))
    bad = []
    for k in ("article-title", "source", "year", "volume", "fpage", "lpage", "issue", "uri"):
        v = d.get(k)
        if v and re.sub(r"\s+", " ", str(v)) not in hay:
            bad.append("%s=%r" % (k, v))
    for sn, gn in d["persons"]:
        if sn and sn not in hay:
            bad.append("surname=%r" % sn)
        if gn and gn.replace(".", "") not in hay.replace(".", ""):
            bad.append("given=%r" % gn)
    return bad
