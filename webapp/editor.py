"""网页中的受控字段编辑；不调用模型，不覆盖自动生成文件。"""
from __future__ import annotations

from copy import deepcopy
from difflib import SequenceMatcher
import hashlib
import re

from lxml import etree

from word2jats.build.jats import DOCTYPE
from word2jats.validate.validator import Validator
from word2jats.verify.audit import audit_structure


class EditError(ValueError):
    pass


def parse(xml):
    root = etree.fromstring(xml, etree.XMLParser(resolve_entities=False, load_dtd=False,
                                                no_network=True))
    if root.tag != "article" or any(isinstance(n, etree._Entity) for n in root.iter()):
        raise EditError("文件不是可编辑的 JATS 文章。")
    return root


def fingerprint(xml):
    return hashlib.sha256(xml).hexdigest()


def slots(element, excluded=()):
    if element is None:
        return []
    result = []
    def walk(node):
        if node.text:
            result.append((node, "text", node.text))
        for child in node:
            if isinstance(child.tag, str) and child.tag not in excluded:
                walk(child)
            if child.tail:
                result.append((child, "tail", child.tail))
    walk(element)
    return result


def text(element, excluded=()):
    return "".join(s for _, _, s in slots(element, excluded))


def replace_text(element, value, excluded=()):
    """按字符差异修改 text/tail，未编辑的行内格式和子元素不被重建。"""
    pieces = slots(element, excluded)
    before = "".join(s for _, _, s in pieces)
    if before == value:
        return
    if not pieces:
        element.text = value
        return
    offsets, cursor = [], 0
    for node, slot, old in pieces:
        offsets.append((node, slot, cursor, cursor + len(old)))
        cursor += len(old)
    replacement = [""] * len(pieces)
    for action, start, end, a, b in SequenceMatcher(None, before, value, autojunk=False).get_opcodes():
        if action == "equal":
            for i, (_, _, lo, hi) in enumerate(offsets):
                replacement[i] += before[max(start, lo):min(end, hi)] if max(start, lo) < min(end, hi) else ""
        elif action in {"insert", "replace"}:
            i = next((i for i, (_, _, lo, hi) in enumerate(offsets) if lo <= start < hi), len(offsets) - 1)
            replacement[i] += value[a:b]
    for (node, slot, _, _), new in zip(offsets, replacement):
        setattr(node, slot, new or None)


def _key(root, element):
    return root.getroottree().getpath(element)


def extract(xml):
    root = parse(xml)
    meta = root.find("front/article-meta")
    if meta is None:
        raise EditError("这份结果缺少文章信息，暂时无法编辑。")
    journal = root.find("front/journal-meta")
    def find(parent, path):
        return text(parent.find(path)) if parent is not None else ""
    publication = {
        "doi": find(meta, 'article-id[@pub-id-type="doi"]'),
        "journal_id": find(journal, "journal-id"),
        "title": find(journal, "journal-title-group/journal-title"),
        "issn_print": find(journal, 'issn[@pub-type="ppub"]'),
        "issn_electronic": find(journal, 'issn[@pub-type="epub"]'),
        "publisher": find(journal, "publisher/publisher-name"),
    }
    # 一些合法文档未标 pub-type，将首个 ISSN 展示为电子刊号供修改。
    if journal is not None and not (publication["issn_print"] or publication["issn_electronic"]):
        publication["issn_electronic"] = find(journal, "issn")
    authors = []
    for author in meta.xpath('./contrib-group/contrib[not(@contrib-type) or @contrib-type="author"]'):
        name = author.find("name")
        kind = "name"
        if name is None:
            kind = "string-name" if author.find("string-name") is not None else "collab"
            name = author.find(kind)
        authors.append({
            "key": _key(root, author), "group": _key(root, author.getparent()), "kind": kind,
            "surname": find(name, "surname") if kind == "name" else "",
            "given_names": find(name, "given-names") if kind == "name" else "",
            "name": text(name) if kind != "name" else "",
            "affiliations": [rid for x in author.findall('xref[@ref-type="aff"]') for rid in (x.get("rid") or "").split()],
            "corresponding": author.get("corresp") == "yes" or bool(author.findall('xref[@ref-type="corresp"]')),
            "orcid": find(author, 'contrib-id[@contrib-id-type="orcid"]'),
        })
    affiliations = [{"key": _key(root, n), "id": n.get("id", ""),
                     "label": text(n.find("label")), "text": text(n, ("label",))}
                    for n in meta.xpath(".//aff")]
    contacts = [{"key": _key(root, n), "text": text(n, ("label", "email")),
                 "emails": [{"key": _key(root, e), "value": text(e)} for e in n.findall(".//email")]}
                for n in meta.findall("author-notes/corresp")]
    return {"publication": publication, "title": find(meta, "title-group/article-title"),
            "authors": authors, "affiliations": affiliations, "contacts": contacts}


def _string(value, label, limit=10000):
    if not isinstance(value, str) or len(value) > limit or any(ord(c) < 32 and c not in "\n\t\r" for c in value):
        raise EditError(f"{label}的内容无效或过长。")
    return value


def _issn(value):
    digits = value.replace("-", "").upper()
    return bool(re.fullmatch(r"\d{7}[\dX]", digits)) and sum(int(c) * (8-i) for i, c in enumerate(digits[:7])) % 11 == (0 if digits[-1] == "0" else 11 - (10 if digits[-1] == "X" else int(digits[-1])))


def _orcid(value):
    digits = re.sub(r"^https?://orcid.org/", "", value).replace("-", "").upper()
    if not re.fullmatch(r"\d{15}[\dX]", digits):
        return False
    total = 0
    for char in digits[:-1]:
        total = (total + int(char)) * 2
    check = (12 - total % 11) % 11
    return digits[-1] == ("X" if check == 10 else str(check))


def _email(value):
    return not value or bool(re.fullmatch(r"[^\s@]+@[^\s@]+\.[^\s@]+", value))


def _rows(new, old, label):
    if not isinstance(new, list) or len(new) != len(old) or {r.get("key") for r in new if isinstance(r, dict)} != {r["key"] for r in old}:
        raise EditError(f"{label}已变化，请重新打开编辑。")
    if len({r["key"] for r in new}) != len(new):
        raise EditError(f"{label}不能重复。")
    originals = {r["key"]: r for r in old}
    for row in new:
        if set(row) != set(originals[row["key"]]):
            raise EditError(f"{label}包含不支持的字段。")
    return originals


def _child(parent, tag):
    node = parent.find(tag)
    if node is None:
        node = etree.SubElement(parent, tag)
    return node


def _insert_meta(meta, element):
    order = ['article-id', 'article-version', 'article-version-alternatives', 'article-categories',
             'title-group', 'contrib-group', 'aff', 'aff-alternatives', 'x', 'author-notes',
             'pub-date', 'pub-date-not-available', 'volume', 'issue', 'issue-id', 'issue-title',
             'issue-sponsor', 'issue-part', 'isbn', 'supplement', 'fpage', 'lpage', 'page-range',
             'elocation-id', 'email', 'ext-link', 'uri', 'product', 'history', 'pub-history',
             'permissions', 'self-uri', 'related-article', 'related-object', 'abstract', 'trans-abstract',
             'kwd-group', 'funding-group', 'support-group', 'conference', 'counts', 'custom-meta-group']
    rank = order.index(element.tag)
    at = next((i for i, n in enumerate(meta) if n.tag in order and order.index(n.tag) > rank), len(meta))
    meta.insert(at, element)


def apply(xml, payload):
    old = extract(xml)
    if not isinstance(payload, dict) or set(payload) != set(old):
        raise EditError("编辑内容的格式不正确。")
    if payload == old:
        return xml
    root = parse(xml)
    paths = {_key(root, n): n for n in root.iter() if isinstance(n.tag, str)}
    meta = root.find("front/article-meta")
    pub = payload["publication"]
    if not isinstance(pub, dict) or set(pub) != set(old["publication"]):
        raise EditError("出版信息的格式不正确。")
    for key, value in pub.items():
        _string(value, "出版信息", 1000)
    if pub != old["publication"]:
        pub = {k: v.strip() for k, v in pub.items()}
        pub["doi"] = re.sub(r"^https?://(?:dx\.)?doi.org/", "", pub["doi"], flags=re.I)
        if pub["doi"] and not re.fullmatch(r"10\.\d{4,9}/\S+", pub["doi"]):
            raise EditError("DOI 格式不正确，例如 10.31083/JIN49347。")
        for key in ("issn_print", "issn_electronic"):
            if pub[key] != old["publication"][key] and pub[key] and not _issn(pub[key]):
                raise EditError("ISSN 格式或校验位不正确，请核对刊号。")
        journal_changed = any(pub[k] != old["publication"][k] for k in pub if k != "doi")
        if journal_changed and (not pub["title"] or not (pub["issn_print"] or pub["issn_electronic"])):
            raise EditError("请填写期刊名称和至少一个有效的 ISSN。")
        previous = root.find("front/journal-meta")
        journal = deepcopy(previous) if previous is not None else etree.Element("journal-meta")
        identifier = journal.find("journal-id")
        if journal_changed and (identifier is None or pub["journal_id"] != old["publication"]["journal_id"]):
            if identifier is None:
                identifier = etree.Element("journal-id", {"journal-id-type": "publisher-id"}); journal.insert(0, identifier)
            replace_text(identifier, pub["journal_id"] or pub["issn_electronic"] or pub["issn_print"])
        if pub["title"] != old["publication"]["title"]:
            group = journal.find("journal-title-group")
            if group is None:
                group = etree.Element("journal-title-group")
                journal.insert(len(journal.findall("journal-id")), group)
            replace_text(_child(group, "journal-title"), pub["title"])
        for key, kind in (("issn_print", "ppub"), ("issn_electronic", "epub")):
            if pub[key] == old["publication"][key]:
                continue
            node = journal.find(f'issn[@pub-type="{kind}"]')
            if node is None and key == "issn_electronic":
                node = next((n for n in journal.findall("issn") if not n.get("pub-type")), None)
            if pub[key]:
                if node is None:
                    node = etree.Element("issn", {"pub-type": kind})
                    at = next((i for i, n in enumerate(journal) if n.tag not in {"journal-id", "journal-title-group", "contrib-group", "aff", "aff-alternatives", "issn"}), len(journal))
                    journal.insert(at, node)
                replace_text(node, pub[key])
            elif node is not None:
                journal.remove(node)
        if pub["publisher"] != old["publication"]["publisher"]:
            publisher = journal.find("publisher")
            if pub["publisher"]:
                if publisher is None:
                    publisher = etree.Element("publisher")
                    at = next((i for i, n in enumerate(journal) if n.tag in {"notes", "self-uri"}), len(journal))
                    journal.insert(at, publisher)
                replace_text(_child(publisher, "publisher-name"), pub["publisher"])
            elif publisher is not None:
                name = publisher.find("publisher-name")
                if name is not None:
                    publisher.remove(name)
                if not len(publisher):
                    journal.remove(publisher)
        front = root.find("front")
        if journal_changed:
            if previous is not None:
                front.replace(previous, journal)
            else:
                front.insert(0, journal)
        if pub["doi"] != old["publication"]["doi"]:
            ids = meta.findall('article-id[@pub-id-type="doi"]')
            if pub["doi"]:
                node = ids[0] if ids else etree.Element("article-id", {"pub-id-type": "doi"})
                node.text = pub["doi"]
                if not ids:
                    _insert_meta(meta, node)
            else:
                for node in ids:
                    meta.remove(node)
    title = _string(payload["title"], "题名")
    if not title.strip():
        raise EditError("题名不能为空。")
    if title != old["title"]:
        replace_text(_child(_child(meta, "title-group"), "article-title"), title)
    affiliations = _rows(payload["affiliations"], old["affiliations"], "单位")
    for row in payload["affiliations"]:
        original = affiliations[row["key"]]
        if row["id"] != original["id"] or row["label"] != original["label"]:
            raise EditError("单位标识不能直接修改。")
        replace_text(paths[row["key"]], _string(row["text"], "单位"), ("label",))
    authors = _rows(payload["authors"], old["authors"], "作者")
    aff_ids = {r["id"] for r in old["affiliations"] if r["id"]}
    for row in payload["authors"]:
        original = authors[row["key"]]
        if row["group"] != original["group"] or row["kind"] != original["kind"]:
            raise EditError("作者分组或姓名类型不能直接修改。")
        node = paths[row["key"]]
        for field, tag in (("surname", "surname"), ("given_names", "given-names"), ("name", row["kind"])):
            value = _string(row[field], "姓名", 1000)
            if value != original[field]:
                parent = _child(node, "name") if row["kind"] == "name" else node
                target = _child(parent, tag)
                replace_text(target, value)
                if tag == "surname" and parent.index(target) != 0:
                    parent.remove(target); parent.insert(0, target)
        if not isinstance(row["affiliations"], list) or any(not isinstance(x, str) for x in row["affiliations"]) or not set(row["affiliations"]) <= aff_ids:
            raise EditError("请选择文章中已有的单位。")
        if len(set(row["affiliations"])) != len(row["affiliations"]):
            raise EditError("同一作者的单位不能重复。")
        if row["affiliations"] != original["affiliations"]:
            for x in node.findall('xref[@ref-type="aff"]'):
                node.remove(x)
            for rid in row["affiliations"]:
                x = etree.SubElement(node, "xref", {"ref-type": "aff", "rid": rid})
                x.text = next((r["label"].strip() or str(i+1) for i, r in enumerate(old["affiliations"]) if r["id"] == rid))
        if type(row["corresponding"]) is not bool:
            raise EditError("通讯作者选项无效。")
        if row["corresponding"] != original["corresponding"]:
            node.set("corresp", "yes" if row["corresponding"] else "no")
            if not row["corresponding"]:
                for x in node.findall('xref[@ref-type="corresp"]'):
                    node.remove(x)
            elif not node.findall('xref[@ref-type="corresp"]'):
                available = meta.findall('author-notes/corresp[@id]')
                # 多组通讯信息时，不能擅自把新通讯作者关联到第一组。
                contact = available[0] if len(available) == 1 else None
                if contact is not None:
                    x = etree.SubElement(node, "xref", {"ref-type": "corresp", "rid": contact.get("id")})
                    x.text = text(contact.find("label")) or "*"
        orcid = _string(row["orcid"], "ORCID", 100)
        if orcid != original["orcid"]:
            if orcid and not _orcid(orcid):
                raise EditError("ORCID 格式或校验位不正确。")
            target = node.find('contrib-id[@contrib-id-type="orcid"]')
            if orcid:
                if target is None:
                    target = etree.Element("contrib-id", {"contrib-id-type": "orcid"}); node.insert(0, target)
                target.text = orcid
            elif target is not None:
                node.remove(target)
    for group in {r["group"] for r in old["authors"]}:
        parent = paths[group]
        indices = [i for i, n in enumerate(parent) if _key(root, n) in authors]
        ordered = [paths[r["key"]] for r in payload["authors"] if r["group"] == group]
        # 先取旧节点列表，避免移动时 XPath 随兄弟次序改变。
        children = list(parent)
        for i, n in zip(indices, ordered):
            children[i] = n
        parent[:] = children
    contacts = _rows(payload["contacts"], old["contacts"], "通讯信息")
    for row in payload["contacts"]:
        original = contacts[row["key"]]
        replace_text(paths[row["key"]], _string(row["text"], "通讯信息"), ("label", "email"))
        _rows(row["emails"], original["emails"], "通讯邮箱")
        for email in row["emails"]:
            value = _string(email["value"], "邮箱", 320)
            if value != text(paths[email["key"]]) and not _email(value):
                raise EditError("通讯邮箱格式不正确。")
            replace_text(paths[email["key"]], value)
    updated = etree.tostring(root, encoding="UTF-8", xml_declaration=True, doctype=DOCTYPE)
    before, after = Validator().validate_bytes(xml), Validator().validate_bytes(updated)
    if set(after.errors) - set(before.errors):
        raise EditError("修改会产生新的 XML 格式问题，尚未保存。请检查所改字段。")
    from collections import Counter
    if Counter(i.code for i in audit_structure(updated).issues) - Counter(i.code for i in audit_structure(xml).issues):
        raise EditError("修改会破坏内容关联，尚未保存。")
    return updated
