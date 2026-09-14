"""模型排列提示只辅助消歧，不否决原稿中唯一的字段位置。"""
from word2jats.model.source import SourceDocument, SourcePart, SourceNode, SourceText
from word2jats.understand.assemble import _Assembler
from word2jats.understand.merge import DocumentAssignment, ReferenceSpan
from word2jats.understand.serialize import SerializedDocument


def assemble(text, fields, order):
    node = SourceNode('doc/p1', 'document', 'para', None, 0, text)
    source = SourceDocument([
        SourcePart('document', 'document', '/word/document.xml', node_ids=(node.node_id,))
    ], [node])
    span = ReferenceSpan(1, (node.node_id, 0, len(text)),
                         SourceText(((node.node_id, 0, len(text)),)))
    raw = {'structured': True, 'publication_type': 'journal', 'person_groups': [],
           'fields': {k: {'quote': v, 'node_hint': node.node_id} for k, v in fields.items()},
           'field_order': order}
    worker = _Assembler(source, SerializedDocument(source, ()), {}, {}, (span,), [raw],
                        DocumentAssignment((), (span,), ()))
    return source, worker._structured_reference(span, raw)


def test_unique_source_positions_override_incorrect_model_order():
    source, result = assemble('Journal, 26 (2019) 1191-1204.',
                             {'source': 'Journal', 'volume': '26', 'year': '2019',
                              'fpage': '1191', 'lpage': '1204'},
                             ['source', 'year', 'volume', 'fpage', 'lpage'])
    assert result is not None
    assert result[1].field_order == ('source', 'volume', 'year', 'fpage', 'lpage')
    assert result[1].volume.plain_text(source) == '26'


def test_repeated_field_values_still_use_valid_order_to_disambiguate():
    source, result = assemble('Journal 12 (12).',
                             {'source': 'Journal', 'volume': '12', 'issue': '12'},
                             ['source', 'volume', 'issue'])
    assert result is not None
    assert result[1].field_order == ('source', 'volume', 'issue')


def test_ambiguous_positions_without_order_are_not_guessed():
    _, result = assemble('Journal 12 (12).',
                         {'source': 'Journal', 'volume': '12', 'issue': '12'}, [])
    assert result is None


def test_overlap_is_not_resolved_by_ignoring_order():
    _, result = assemble('Journal 2019.',
                         {'source': 'Journal', 'year': '2019', 'volume': '2019'},
                         ['source', 'year', 'volume'])
    assert result is None
