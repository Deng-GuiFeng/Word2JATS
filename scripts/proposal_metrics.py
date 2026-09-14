"""说明书实验：逐内容维度的语义事实比对与接口用量汇总。

内容事实以可读字段、逻辑网格、语义目标为单位，不使用 XML 节点数或内部缺陷总数。
结构参考用于对齐；原生公式与全文内容保留另从 DOCX 独立读取。
"""
from __future__ import annotations
import argparse
from collections import Counter, defaultdict
from difflib import SequenceMatcher
import hashlib
import json
from pathlib import Path
import re
import statistics
import zipfile

from lxml import etree as E
from scripts.eval_v1 import structure as S, fidelity as F
from scripts.eval_v1.normalize import norm_text, norm_value, norm_key, itertext
from scripts.eval_v1.samples import SAMPLES
from scripts.output_manifest import OutputLocation, resolve_output

ROOT = Path(__file__).resolve().parents[1]
PARSER = E.XMLParser(load_dtd=False, no_network=True, resolve_entities=False)
MATH = 'http://www.w3.org/1998/Math/MathML'
OMML = 'http://schemas.openxmlformats.org/officeDocument/2006/math'


def value(el):
    return norm_value(itertext(el))


def reference_key(ref):
    """编号可能在 label 中，也可能仍在完整著录开头；二者身份相同。"""
    label = norm_key(ref.findtext('label') or '')
    if label:
        number = re.fullmatch(r'[\[(]?(\d+)[\]).]?', label)
        return number.group(1) if number else label
    match = re.match(r'^\s*(?:\[(\d+)\]|(\d+)[.)])\s*', itertext(ref))
    if match:
        return match.group(1) or match.group(2)
    return S._ref_align_key(ref)


def page_value(first, last='', elocation=''):
    """统一起止页拆分、缩写尾页及文章位置字段的等价表示。"""
    text = (first + ('-' + last if last else '')).strip() or elocation
    text = re.sub(r'\s+', '', text).replace('–', '-').replace('—', '-')
    match = re.fullmatch(r'(\d+)-(\d+)', text)
    if match:
        start, end = match.groups()
        if len(end) < len(start):
            expanded = int(start[:-len(end)] + end)
            if expanded < int(start):
                expanded += 10 ** len(end)
            text = start + '-' + str(expanded)
    return text


def reference_fields(ref):
    result = S._ref_fields(ref)
    citation = ref.find('element-citation')
    if citation is not None:
        # 相邻同角色 person-group 的分拆不改变名单，人员先后与角色仍保留。
        result['authors'] = tuple(
            (g.get('person-group-type', 'author'), n.tag,
             value(n.find('surname')),
             re.sub(r'[\s.]', '', value(n.find('given-names'))))
            if n.tag == 'name' else
            (g.get('person-group-type', 'author'), n.tag, '' if n.tag == 'etal' else value(n))
            for g in citation.findall('person-group')
            for n in g if n.tag in ('name', 'collab', 'etal')
        )
    result['pages'] = page_value(result.pop('fpage'), result.pop('lpage'),
                                 result.pop('elocation-id'))
    # 字段之间的句点不属于题名或刊名内容；内部缩写标点不删。
    for field in ('title', 'source'):
        result[field] = result[field].rstrip('.')
    result.pop('raw', None)
    result.pop('container', None)
    return result


def facts(root, media_reader):
    """一项事实包含位置/身份和取值；重复项计次数，错位置不算正确匹配。"""
    out = defaultdict(Counter)

    def add(category, *parts):
        out[category][tuple(parts)] += 1

    am = root.find('front/article-meta')
    if am is not None:
        title = am.find('title-group/article-title')
        if title is not None:
            add('title', value(title))
        for date in am.findall('history/date'):
            parts = tuple((k, (date.findtext(k) or '').lstrip('0')) for k in ('year', 'month', 'day'))
            if any(v for _, v in parts):
                add('dates', date.get('date-type'), parts)
    authors = S._authors(root)
    ids = S._id_semantics(root)
    for ref in S._refs(root):
        if ref.get('id'):
            ids[ref.get('id')] = ('ref', reference_key(ref))
    emails = S._corresp_emails(root)
    for author in authors:
        key = S._author_key(author)
        add('authors', key)
        for oid in author.findall('contrib-id[@contrib-id-type="orcid"]'):
            match = re.search(r'\d{4}-\d{4}-\d{4}-\d{3}[\dX]', itertext(oid))
            if match:
                add('orcid', key, match.group())
        for x in author.findall('xref[@ref-type="aff"]'):
            for target in S._rid_targets(x, ids):
                add('author_affiliations', key, target)
        for email in S._author_emails(author, emails):
            add('correspondence', key, email)
    for pair, n in S._adjacent([S._author_key(a) for a in authors]).items():
        out['author_order'][pair] += n
    for aff in root.findall('front/article-meta//aff'):
        add('affiliations', S._aff_key(aff))
    for ab in root.findall('front/article-meta/abstract'):
        # 摘要标题/内联标记的包装不改变文字内容。
        add('abstract_text', value(ab))
    out['abstract_structure'].update(S._abstracts(root))
    for kw in root.findall('front/article-meta//kwd'):
        add('keywords', value(kw))
    sections = S._sections(root)
    out['sections'].update(sections)
    out['section_order'].update(S._adjacent(sections))
    for item in root.findall('body//list-item'):
        add('list_items', value(item))
    for item in root.findall('.//def-item'):
        add('definition_items', value(item.find('term')), value(item.find('def')))
    for fig in S._figs(root):
        key = S._cap_key(fig)
        add('figures', key)
        add('figure_captions', key, value(fig.find('caption')))
    for table in S._tables(root):
        key = S._cap_key(table)
        add('tables', key)
        add('table_captions', key, value(table.find('caption')))
        foot = table.find('table-wrap-foot')
        if foot is not None:
            add('table_notes', key, value(foot))
        grid = table.find('.//table')
        if grid is not None:
            for (r, c), (_, text) in S._grid(grid).items():
                add('table_cells', key, r, c, re.sub(r'\s+', '', text))
    for el in root.xpath('//graphic|//inline-graphic'):
        if any(p.tag in ('inline-formula', 'disp-formula') for p in el.iterancestors()):
            continue  # 公式载体另从 DOCX 核对，避免与参考中的 MathML 重复比较。
        blob = media_reader.get(el.get(S.XLINK_HREF))
        digest = hashlib.sha256(blob).hexdigest() if blob else 'MISSING_RESOURCE'
        parent = next((p for p in el.iterancestors() if p.tag in ('fig', 'table-wrap')), None)
        slot = (parent.tag, S._cap_key(parent)) if parent is not None else ('inline', '')
        add('media', slot, digest)
    for ref in S._refs(root):
        key = reference_key(ref)
        add('references', key)
        fields = reference_fields(ref)
        for field, v in fields.items():
            if v:
                add('reference_fields', key, field, v)
    # 保留所在段落的非引文文字，捕捉“链接闭合但接错位置”。
    # 区间引文与逐条引文统一展开为语义目标边。
    for xref in root.findall('.//xref'):
        kind = xref.get('ref-type')
        if kind not in ('bibr', 'fig', 'table'):
            continue
        parent = next((p for p in xref.iterancestors() if p.tag in ('p', 'td', 'th', 'title')), None)
        context = (without_xrefs(parent), without_xrefs(parent, stop_at=xref)) if parent is not None else ('', '')
        for target in S._rid_targets(xref, ids):
            add('citations' if kind == 'bibr' else 'figure_table_links', context, target)
    for fmts, words in S._fmt_buckets(root).items():
        for word, n in words.items():
            out['inline_format'][(tuple(sorted(fmts)), word)] += n
    out['back_sections'].update(S._back_decls(root))
    return dict(out)


def without_xrefs(el, stop_at=None):
    """引文链接以外的段落上下文；忽略引文括号与分隔符的包装差异。"""
    parts = []

    def walk(node):
        if node is stop_at:
            return True
        if node.tag != 'xref':
            if node.text:
                parts.append(node.text)
            for child in node:
                if walk(child):
                    return True
                if child.tail:
                    parts.append(child.tail)
    if el is not None:
        walk(el)
    return re.sub(r'[\s\[\](),;–—-]+', '', norm_value(''.join(parts)))


def link_blocks(root, kind):
    """记录可见段落中的链接区间；链接内外包装不改变文字坐标。"""
    ids = S._id_semantics(root)
    for ref in S._refs(root):
        if ref.get('id'):
            ids[ref.get('id')] = ('ref', reference_key(ref))
    blocks = []
    for parent in root.xpath('//p|//td|//th|//title'):
        parts, edges = [], []
        length = 0
        def emit(text):
            nonlocal length
            normalized = re.sub(r'\W+', '', norm_value(text or ''))
            parts.append(normalized)
            length += len(normalized)
        def walk(node):
            start = length
            emit(node.text)
            for child in node:
                walk(child)
                emit(child.tail)
            if node.tag == 'xref' and node.get('ref-type') in kind:
                owner = next((p for p in node.iterancestors() if p.tag in ('p','td','th','title')), None)
                if owner is parent:
                    edges.extend((start, length, target) for target in S._rid_targets(node, ids))
        walk(parent)
        text = ''.join(parts)
        if text:
            blocks.append((text, edges))
    return blocks


def compare_links(reference, candidate, kinds):
    """对齐可见段落和字符位置，再比较引用目标；一处漏链不污染全段其余链接。"""
    expected, actual = link_blocks(reference, kinds), link_blocks(candidate, kinds)
    used = set()
    hits, missing, extra = 0, [], []
    for ri, (text, edges) in enumerate(expected):
        if not edges:
            continue
        available = [j for j in range(len(actual)) if j not in used]
        exact = [j for j in available if actual[j][0] == text]
        if len(exact) == 1:
            target = exact[0]
        elif exact:
            # 重复完全相同段落按出现顺序计实例，不压成字典的一条。
            target = exact[0]
        else:
            scores = []
            for j in available:
                matcher = SequenceMatcher(None, text, actual[j][0], autojunk=False)
                if matcher.real_quick_ratio() >= .9 and matcher.quick_ratio() >= .9:
                    scores.append((matcher.ratio(), j))
            ranked = sorted(scores, reverse=True)
            target = ranked[0][1] if ranked and ranked[0][0] >= .9 and (
                len(ranked) == 1 or ranked[0][0] > ranked[1][0]) else None
        if target is None:
            missing.extend((str((ri, edge)), 1) for edge in edges)
            continue
        used.add(target)
        out_text, out_edges = actual[target]
        matcher = SequenceMatcher(None, text, out_text, autojunk=False)
        opcodes = matcher.get_opcodes()
        def mapped_range(start, end):
            spans = []
            for tag, a, b, c, d in opcodes:
                if a >= end or b <= start:
                    continue
                if tag == 'equal':
                    spans.append((c + max(start, a) - a, c + min(end, b) - a))
                elif tag == 'replace':
                    spans.append((c, d))
            return (min(s for s, e in spans), max(e for s, e in spans)) if spans else None
        matched = set()
        for edge in edges:
            start, end, identity = edge
            position = mapped_range(start, end)
            choices = [j for j, (a, b, entity) in enumerate(out_edges)
                       if j not in matched and entity == identity and position is not None
                       and a < position[1] and position[0] < b]
            if len(choices) == 1:
                matched.add(choices[0]); hits += 1
            else:
                missing.append((str((ri, edge)), 1))
        extra.extend((str((target, edge)), 1) for j, edge in enumerate(out_edges) if j not in matched)
    for j, (_, edges) in enumerate(actual):
        if j not in used:
            extra.extend((str((j, edge)), 1) for edge in edges)
    exp = sum(len(edges) for _, edges in expected)
    got = sum(len(edges) for _, edges in actual)
    return {'correct': hits, 'expected': exp, 'actual': got,
            'recall': hits / exp if exp else None, 'precision': hits / got if got else None,
            'f1': 2 * hits / (exp + got) if exp + got else None,
            'missing': missing, 'extra': extra}


def math_signature(node):
    """忽略冗余单子 mrow，保留变量大小写、运算结构及有效属性。"""
    name = E.QName(node).localname
    children = [math_signature(c) for c in node if isinstance(c.tag, str)]
    if name == 'mrow' and len(children) == 1:
        return children[0]
    if name == 'math' and len(children) == 1 and children[0][0] == 'mrow':
        children = list(children[0][-1])
    attrs = tuple(sorted((E.QName(k).localname, v) for k, v in node.attrib.items()
                         if E.QName(k).localname not in ('display', 'id')))
    return name, norm_text(node.text), attrs, tuple(children)


def compare(expected, actual):
    hit = sum((expected & actual).values())
    exp, got = sum(expected.values()), sum(actual.values())
    return {'correct': hit, 'expected': exp, 'actual': got,
            'recall': hit / exp if exp else None,
            'precision': hit / got if got else None,
            'f1': 2 * hit / (exp + got) if exp + got else None,
            'missing': [(str(k), n) for k, n in (expected - actual).items()],
            'extra': [(str(k), n) for k, n in (actual - expected).items()]}


def one(sample, output_root):
    timing = json.loads((output_root / f'{sample.key}.timing.json').read_text())
    entry = timing.get('manifest')
    if entry:
        loc = OutputLocation(output_root / entry['candidate_dir'], output_root / entry['candidate_xml'],
                             entry.get('delivered'), entry.get('article_id'))
    else:
        loc = resolve_output(output_root, sample.key)
    reference = E.parse(sample.ref_xml, PARSER).getroot()
    candidate = E.parse(str(loc.candidate_xml), PARSER).getroot()
    r = facts(reference, S._ZipReader(sample.figures_zip))
    o = facts(candidate, S._DirReader(str(loc.candidate_dir)))
    metrics = {k: compare(r.get(k, Counter()), o.get(k, Counter())) for k in sorted(set(r) | set(o))}
    metrics['citations'] = compare_links(reference, candidate, ('bibr',))
    metrics['figure_table_links'] = compare_links(reference, candidate, ('fig', 'table'))
    with zipfile.ZipFile(sample.docx) as z:
        original = E.fromstring(z.read('word/document.xml'))
        source_media = {hashlib.sha256(z.read(n)).hexdigest() for n in z.namelist()
                        if n.startswith('word/media/') and not n.endswith('/')}
        rels = {e.get('Id'): e.get('Target') for e in E.fromstring(z.read('word/_rels/document.xml.rels'))}
        equation_images = Counter()
        for ole in original.xpath('//*[local-name()="OLEObject"]'):
            if not (ole.get('ProgID') or '').startswith('Equation.'):
                continue
            parent = ole.getparent()
            for image in parent.xpath('.//*[local-name()="imagedata"]'):
                rid = image.get('{http://schemas.openxmlformats.org/officeDocument/2006/relationships}id')
                target = rels.get(rid)
                if target:
                    equation_images[hashlib.sha256(z.read('word/' + target)).hexdigest()] += 1
        # 原稿对照确认：01 的同一行公式由两个无 OLE 包装的 WMF 片段承载。
        # 这是评价标注，不影响转换器的任何识别规则。
        if sample.key == '01':
            for name in ('word/media/image1.wmf', 'word/media/image2.wmf'):
                equation_images[hashlib.sha256(z.read(name)).hexdigest()] += 1
    transform = E.XSLT(E.parse(str(ROOT / 'src/word2jats/resources/OMML2MML.XSL')))
    expected_math = Counter()
    for eq in original.iter(f'{{{OMML}}}oMath'):
        roots = transform(eq).xpath('/*')
        container = E.Element(f'{{{MATH}}}math')
        for child in roots:
            container.append(child)
        expected_math[math_signature(container)] += 1
    actual_math = Counter(math_signature(n) for n in candidate.iter(f'{{{MATH}}}math'))
    metrics['native_math'] = compare(expected_math, actual_math)
    src_main, _, _ = F._docx_tokens(sample.docx)
    out_main, out_identifiers, _ = F._xml_tokens(candidate)
    # DOI、ORCID 等在 XML 中进入专门标签，仍是原稿内容，不能从输出侧排除。
    metrics['text_retention'] = compare(src_main, out_main + out_identifiers)
    hrefs = candidate.xpath('//graphic/@xlink:href|//inline-graphic/@xlink:href',
                             namespaces={'xlink':'http://www.w3.org/1999/xlink'})
    output_equation_images = Counter()
    for href in candidate.xpath('//inline-formula//graphic/@xlink:href|//inline-formula//inline-graphic/@xlink:href|//disp-formula//graphic/@xlink:href', namespaces={'xlink':'http://www.w3.org/1999/xlink'}):
        path = loc.candidate_dir / href
        if path.is_file():
            output_equation_images[hashlib.sha256(path.read_bytes()).hexdigest()] += 1
    metrics['equation_image_resources'] = compare(equation_images, output_equation_images)
    media_from_source = sum(hashlib.sha256((loc.candidate_dir / h).read_bytes()).hexdigest()
                            in source_media for h in hrefs if (loc.candidate_dir / h).is_file())
    report = json.loads((loc.candidate_dir.parent / 'report.json').read_text())
    return {'sample': sample.key, 'metrics': metrics,
            'dtd_valid': report['validation']['dtd_valid'],
            'media_from_source': media_from_source, 'media_references': len(hrefs),
            'formula_inventory': {'native_math': sum(expected_math.values()),
                'reference_formula_containers': len(S._formulas(reference)),
                'output_formula_containers': len(S._formulas(candidate)),
                'image_formula_components': len(candidate.xpath('//inline-formula//inline-graphic|//inline-formula//graphic|//disp-formula//graphic'))},
            'wall_seconds': timing['wall_seconds'],
            'peak_rss_mib': timing.get('peak_rss_mib'), 'cpu_seconds': timing.get('cpu_seconds'),
            'usage': timing['llm'].get('usage'),
            'model_breakdown': [{k:v for k,v in row.items() if k not in ('usage_records',)}
                                for row in timing['llm'].get('by_model', [])],
            'calls': timing['llm']['calls'],
            'requests': timing['llm'].get('usage', {}).get('requests', timing['llm']['calls']),
            'rate_limit_events': timing['rate_limit_events'],
            'xml_sha256': hashlib.sha256(loc.candidate_xml.read_bytes()).hexdigest(),
            'input_sha256': hashlib.sha256(Path(sample.docx).read_bytes()).hexdigest()}


def aggregate(rows):
    metrics = {}
    for key in sorted({k for row in rows for k in row['metrics']}):
        values = [row['metrics'].get(key, {}) for row in rows]
        hit, exp, got = (sum(v.get(k, 0) for v in values) for k in ('correct', 'expected', 'actual'))
        metrics[key] = {'correct': hit, 'expected': exp, 'actual': got,
                        'recall': hit / exp if exp else None,
                        'precision': hit / got if got else None,
                        'f1': 2 * hit / (exp + got) if exp + got else None}
    elapsed = [r['wall_seconds'] for r in rows]
    result = {'samples': len(rows), 'metrics': metrics,
              'time_seconds': {'mean': statistics.mean(elapsed), 'median': statistics.median(elapsed),
                               'min': min(elapsed), 'max': max(elapsed)},
              'dtd_valid': sum(r['dtd_valid'] for r in rows)}
    usages = [r['usage'] for r in rows if r['usage'] is not None]
    if usages:
        result['usage'] = {k: sum(u[k] for u in usages) for k in (
            'input_tokens','output_tokens','total_tokens','cache_hit_tokens','cache_miss_tokens')}
        result['usage_complete'] = len(usages) == len(rows) and all(u['complete'] for u in usages)
    return result


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    args = parser.parse_args()
    root = ROOT / 'reports/outputs' / args.tag
    rows = [one(s, root) for s in SAMPLES if (root / f'{s.key}.timing.json').exists()]
    result = {'tag': args.tag, 'samples': rows, 'all': aggregate(rows),
              'submission': aggregate([r for r in rows if not r['sample'].startswith('X')])}
    dest = ROOT / 'reports/proposal-experiments' / f'{args.tag}.json'
    dest.parent.mkdir(exist_ok=True, parents=True)
    dest.write_text(json.dumps(result, ensure_ascii=False, indent=2) + '\n')
    print(dest)
    for k, v in result['submission']['metrics'].items():
        print(k, v['correct'], '/', v['expected'], 'actual', v['actual'])


if __name__ == '__main__':
    main()
