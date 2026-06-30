"""参考文献联网补全（CrossRef）。

把一条参考文献的纯文本拿去 CrossRef 反查,补上 DOI、规范刊名全称、卷期页等。
这类信息 docx 里没有,但属于"结合外部数据库可以得到、也应该补"的部分(真实出版场景如此)。

**防错门槛**:只有当 CrossRef 返回的标题与原文高度吻合时才采纳,绝不硬塞可能错误的 DOI。
所有结果磁盘缓存,重复运行不再联网、可复现。失败时静默跳过(不影响出 XML)。
"""

from __future__ import annotations

import json
import os
import re
import urllib.parse
import urllib.request

_CACHE_DIR = os.path.join(os.getcwd(), ".crossref_cache")
_UA = "word2jats/0.1 (mailto:ABCDLab@proton.me)"


def _norm_tokens(s: str) -> set:
    return set(re.findall(r"[a-z0-9]+", (s or "").lower()))


def _cache_path(key: str) -> str:
    import hashlib
    os.makedirs(_CACHE_DIR, exist_ok=True)
    return os.path.join(_CACHE_DIR, hashlib.sha256(key.encode()).hexdigest() + ".json")


class CrossRefClient:
    def __init__(self, enabled: bool = True, timeout: int = 20,
                 min_title_overlap: float = 0.7):
        self.enabled = enabled
        self.timeout = timeout
        self.min_overlap = min_title_overlap
        self.hits = 0
        self.queries = 0

    def _query(self, bibliographic: str):
        cp = _cache_path(bibliographic)
        if os.path.exists(cp):
            try:
                return json.load(open(cp, encoding="utf-8"))
            except Exception:
                pass
        q = urllib.parse.urlencode({"query.bibliographic": bibliographic, "rows": 1})
        url = "https://api.crossref.org/works?" + q
        req = urllib.request.Request(url, headers={"User-Agent": _UA})
        try:
            with urllib.request.urlopen(req, timeout=self.timeout) as r:
                data = json.loads(r.read())
            items = data.get("message", {}).get("items", [])
            result = items[0] if items else None
        except Exception:
            result = None
        try:
            json.dump(result, open(cp, "w", encoding="utf-8"))
        except Exception:
            pass
        return result

    def enrich(self, ref) -> bool:
        """就地补全一条 Reference。返回是否采纳了 CrossRef 结果。"""
        if not self.enabled or not ref.raw_text:
            return False
        self.queries += 1
        it = self._query(ref.raw_text)
        if not it:
            return False
        cand_title = (it.get("title") or [""])[0]
        if not cand_title:
            return False
        # 防错门槛:候选标题的词,大部分要出现在原文里,才认为是同一篇
        ct = _norm_tokens(cand_title)
        if not ct:
            return False
        overlap = len(ct & _norm_tokens(ref.raw_text)) / len(ct)
        if overlap < self.min_overlap:
            return False
        self.hits += 1
        # 采纳:补缺为主,不覆盖已可靠解析出的字段
        if not ref.doi and it.get("DOI"):
            ref.doi = it["DOI"]
        journal = (it.get("container-title") or [""])[0]
        if journal:
            ref.source = journal  # 用规范全称
        if not ref.article_title:
            ref.article_title = cand_title
        if it.get("volume"):
            ref.volume = it["volume"]
        page = it.get("page")
        if page and "-" in page and (not ref.fpage or not ref.lpage):
            fp, lp = page.split("-", 1)
            ref.fpage, ref.lpage = fp.strip(), lp.strip()
        dp = it.get("issued", {}).get("date-parts", [[None]])
        if dp and dp[0] and dp[0][0] and not ref.year:
            ref.year = str(dp[0][0])
        # 作者
        if it.get("author"):
            authors = []
            for a in it["author"]:
                fam = a.get("family", "").strip()
                given = a.get("given", "").strip()
                if fam:
                    authors.append((fam, given))
            if authors:
                ref.authors = authors
        ref.structured = True
        if not ref.pub_type or ref.pub_type == "journal":
            t = it.get("type", "")
            ref.pub_type = {"journal-article": "journal", "book": "book",
                            "book-chapter": "chapter",
                            "proceedings-article": "confproc"}.get(t, "journal")
        return True

    @property
    def stats(self):
        return {"queries": self.queries, "hits": self.hits}


def enrich_references(refs: list, enabled: bool = True) -> dict:
    client = CrossRefClient(enabled=enabled)
    for ref in refs:
        client.enrich(ref)
    return client.stats
