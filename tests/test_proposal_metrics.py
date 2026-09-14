from collections import Counter
from lxml import etree as E
from scripts.proposal_metrics import compare, facts, math_signature, page_value, compare_links


class Reader:
    def get(self, key):
        return {'a.png': b'image-a', 'b.png': b'image-b'}.get(key)


def xml(body):
    return E.fromstring(f'<article xmlns:xlink="http://www.w3.org/1999/xlink">{body}</article>')


def test_counts_do_not_hide_changed_values_or_duplicate_instances():
    result = compare(Counter({'a': 2, 'b': 1}), Counter({'a': 1, 'b': 1, 'c': 1}))
    assert result['correct'] == 2
    assert result['expected'] == result['actual'] == 3


def test_links_use_target_identity_and_location_not_ids():
    def data(first, second, reverse=False):
        targets = (second, first) if reverse else (first, second)
        return xml(f'<body><p>One <xref ref-type="bibr" rid="{targets[0]}">1</xref> '
                   f'two <xref ref-type="bibr" rid="{targets[1]}">2</xref>.</p></body>'
                   f'<back><ref-list><ref id="{first}"><label>1</label></ref>'
                   f'<ref id="{second}"><label>2</label></ref></ref-list></back>')
    original = facts(data('r1','r2'), Reader())['citations']
    renamed = facts(data('x','y'), Reader())['citations']
    wrong = facts(data('x','y', True), Reader())['citations']
    assert original == renamed
    assert compare(original, wrong)['correct'] == 0


def test_merged_grid_counts_positions_and_values():
    a = xml('<body><table-wrap><label>1</label><table><tr><td colspan="2">A</td></tr></table></table-wrap></body>')
    b = xml('<body><table-wrap><label>1</label><table><tr><td>A</td><td>B</td></tr></table></table-wrap></body>')
    result = compare(facts(a, Reader())['table_cells'], facts(b, Reader())['table_cells'])
    assert (result['correct'], result['expected'], result['actual']) == (1, 2, 2)


def test_math_comparison_preserves_case_and_operator_structure():
    def sig(text):
        return math_signature(E.fromstring(text))
    assert sig('<math><mi>A</mi></math>') != sig('<math><mi>a</mi></math>')
    assert sig('<math><mrow><mi>A</mi></mrow></math>') == sig('<math><mi>A</mi></math>')
    assert sig('<math><msup><mi>a</mi><mn>2</mn></msup></math>') != sig('<math><msub><mi>a</mi><mn>2</mn></msub></math>')


def test_printed_reference_label_is_not_lost_by_mixed_citation_packaging():
    a = xml('<back><ref-list><ref><label>[2]</label><mixed-citation>Article</mixed-citation></ref></ref-list></back>')
    b = xml('<back><ref-list><ref><mixed-citation>[2] Article</mixed-citation></ref></ref-list></back>')
    assert facts(a, Reader())['references'] == facts(b, Reader())['references']


def test_page_ranges_and_article_location_allow_equivalent_tags():
    assert page_value('240', '245') == page_value('240-5')
    assert page_value('1999', '2002') == page_value('1999–02')
    assert page_value('e123') == page_value('', elocation='e123')
    assert page_value('240', '245') != page_value('240', '246')


def test_author_group_packaging_does_not_hide_member_order_or_role():
    name = '<name><surname>Lee</surname><given-names>A.</given-names></name>'
    collab = '<collab>Study group</collab>'
    def entry(groups):
        return facts(xml('<back><ref-list><ref><label>1</label><element-citation>' + groups +
                         '</element-citation></ref></ref-list></back>'), Reader())['reference_fields']
    combined = entry('<person-group person-group-type="author">' + name + collab + '</person-group>')
    split = entry('<person-group person-group-type="author">' + name + '</person-group>' +
                  '<person-group person-group-type="author">' + collab + '</person-group>')
    swapped = entry('<person-group person-group-type="author">' + collab + name + '</person-group>')
    assert combined == split
    assert compare(combined, swapped)['correct'] == 0
    empty_etal = entry('<person-group>' + name + '<etal/></person-group>')
    text_etal = entry('<person-group>' + name + '<etal>et al.</etal></person-group>')
    assert empty_etal == text_etal


def test_one_unmarked_citation_does_not_invalidate_other_links_in_paragraph():
    refs = '<back><ref-list><ref id="r1"><label>1</label></ref><ref id="r2"><label>2</label></ref></ref-list></back>'
    a = xml('<body><p>First [<xref ref-type="bibr" rid="r1">1</xref>], second '
            '[<xref ref-type="bibr" rid="r2">2</xref>].</p></body>' + refs)
    b = xml('<body><p>First [1], second [<xref ref-type="bibr" rid="r2">2</xref>].</p></body>' + refs)
    result = compare_links(a, b, ('bibr',))
    assert (result['correct'], result['expected'], result['actual']) == (1, 2, 1)
    wrong = xml('<body><p>First [<xref ref-type="bibr" rid="r2">1</xref>], second '
                '[<xref ref-type="bibr" rid="r1">2</xref>].</p></body>' + refs)
    assert compare_links(a, wrong, ('bibr',))['correct'] == 0


def test_figure_prefix_inside_or_outside_link_is_equivalent():
    end = '<fig id="f1"><label>Fig. 1</label></fig></body>'
    a = xml('<body><p>See <xref ref-type="fig" rid="f1">Fig. 1</xref>.</p>' + end)
    b = xml('<body><p>See Fig. <xref ref-type="fig" rid="f1">1</xref>.</p>' + end)
    assert compare_links(a, b, ('fig',))['correct'] == 1
