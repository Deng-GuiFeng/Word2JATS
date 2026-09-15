"""错误输入验收计划覆盖实际校验分支，不修改基准字段。"""
from copy import deepcopy
import pytest
from scripts.web_public_validation import invalid_inputs
from tests.test_web_public_metadata import XML
from webapp import editor


def test_validation_plan_covers_each_author_and_email_without_mutating_fields():
    fields = editor.extract(XML.encode())
    before = deepcopy(fields)
    cases = invalid_inputs(fields)
    assert fields == before
    names = [case['name'] for case in cases]
    assert len(set(names)) == len(names)
    for i in range(len(fields['authors'])):
        assert f'作者 {i+1} ORCID 格式' in names
        assert f'作者 {i+1} ORCID 校验位' in names
    assert '通讯 1 邮箱 1 格式' in names
    assert '通讯 1 邮箱 1 长度' in names


@pytest.mark.parametrize('case', invalid_inputs(editor.extract(XML.encode())), ids=lambda c: c['name'])
def test_invalid_input_reaches_expected_editor_validation(case):
    fields = editor.extract(XML.encode())
    for field, value in case['values'].items():
        node = fields
        parts = field.split('.')
        for key in parts[:-1]:
            node = node[int(key)] if isinstance(node, list) else node[key]
        node[parts[-1]] = value
    with pytest.raises(editor.EditError, match=case['message']):
        editor.apply(XML.encode(), fields)


def test_no_authors_affiliations_or_contacts_does_not_create_controls():
    fields = {'authors': [], 'affiliations': [], 'contacts': []}
    cases = invalid_inputs(fields)
    assert len(cases) == 11
    assert all(field == 'title' or field.startswith('publication.')
               for case in cases for field in case['values'])
