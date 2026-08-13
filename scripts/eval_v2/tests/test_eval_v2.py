from __future__ import annotations

from pathlib import Path

from lxml import etree
import pytest

from scripts.eval_v2 import evaluate_sample
from scripts.eval_v2.artifacts import GoldMediaStore
from scripts.eval_v2.canonical import canonicalize
from scripts.eval_v2.policy import XLINK_NS
from scripts.eval_v2.provenance import _docx_paragraph_text
from scripts.eval_v2.report import json_text, markdown_text, write_reports
from scripts.eval_v2.samples import ALL_SAMPLES, get_sample


XLINK_HREF = f"{{{XLINK_NS}}}href"
MEDIA_TAGS = {"graphic", "inline-graphic", "media"}


def materialize(sample_key: str, root: Path) -> Path:
    """把金标准 zip 按 XML href 展开成转换器应交付的目录形态。"""
    sample = get_sample(sample_key)
    output = root / sample_key
    output.mkdir(parents=True)
    (output / "result.xml").write_bytes(sample.gold_xml.read_bytes())
    tree = etree.parse(str(sample.gold_xml))
    store = GoldMediaStore(sample.figures_zip)
    assert store.error is None
    for element in tree.getroot().iter():
        if not isinstance(element.tag, str) or etree.QName(element).localname not in MEDIA_TAGS:
            continue
        href = element.get(XLINK_HREF)
        if not href:
            continue
        blob = store.resolve(href)
        assert blob is not None, (sample_key, href)
        target = output / href
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(blob.data)
    return output


def load_candidate(output: Path):
    return etree.parse(str(output / "result.xml"))


def save_candidate(tree, output: Path) -> None:
    tree.write(
        str(output / "result.xml"),
        encoding="UTF-8",
        xml_declaration=True,
        doctype=tree.docinfo.doctype,
        pretty_print=False,
    )


def replace_text(root, needle: str, replacement: str) -> None:
    for element in root.iter():
        if element.text and needle in element.text:
            element.text = element.text.replace(needle, replacement, 1)
            return
        if element.tail and needle in element.tail:
            element.tail = element.tail.replace(needle, replacement, 1)
            return
    raise AssertionError(f"找不到待替换文本：{needle}")


@pytest.mark.parametrize("sample_key", [sample.key for sample in ALL_SAMPLES])
def test_all_gold_packages_are_fixed_points(sample_key, tmp_path):
    output = materialize(sample_key, tmp_path)
    result = evaluate_sample(sample_key, output)
    assert result.passed, [(issue.code, issue.message) for issue in result.issues]
    assert result.issues == []
    assert all(report.complete for report in result.coverage.values())
    assert result.provenance.referenced_media == result.provenance.media_from_docx
    assert all(
        row["exact"]
        for row in result.statistics["quality_vector"]["dimensions"].values()
    )
    assert result.statistics["strict_fallback_tags"]["gold"] == {}


def test_frozen_gold_vocabulary_census_is_explicit():
    tags = set()
    attributes = set()
    for sample in ALL_SAMPLES:
        root = etree.parse(str(sample.gold_xml)).getroot()
        for element in root.iter():
            if not isinstance(element.tag, str):
                continue
            tags.add(etree.QName(element).localname)
            attributes.update(etree.QName(name).localname for name in element.attrib)
    assert len(tags) == 119
    assert len(attributes) == 41


def test_unlabelled_references_do_not_collapse(tmp_path):
    output = materialize("X03", tmp_path)
    tree = load_candidate(output)
    refs = tree.xpath("//*[local-name()='ref']")
    assert len(refs) == 35
    assert all(not ref.xpath("./*[local-name()='label']") for ref in refs)
    refs[0].getparent().remove(refs[0])
    save_candidate(tree, output)

    result = evaluate_sample("X03", output)
    assert not result.passed
    assert any(
        issue.code == "REFERENCE_ELEMENT_MISSING" for issue in result.issues
    )


def test_reference_reorder_is_reported(tmp_path):
    output = materialize("X03", tmp_path)
    tree = load_candidate(output)
    refs = tree.xpath("//*[local-name()='ref']")
    parent = refs[0].getparent()
    parent.remove(refs[1])
    parent.insert(parent.index(refs[0]), refs[1])
    save_candidate(tree, output)

    result = evaluate_sample("X03", output)
    assert not result.passed
    assert any(issue.code == "REFERENCE_CHILD_ORDER_CHANGED" for issue in result.issues)


def test_affiliation_address_loss_is_reported_in_contributor_domain(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    replace_text(tree.getroot(), "No.167 North Lishi Road", "North Lishi Road")
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert not result.passed
    assert any(issue.domain == "contributors" for issue in result.issues)


def test_table_cell_text_change_is_reported_in_table_domain(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    cells = tree.xpath("//*[local-name()='td' or local-name()='th']")
    target = next(cell for cell in cells if "".join(cell.itertext()).strip())
    leaf = next(node for node in target.iter() if node.text and node.text.strip())
    leaf.text += "错误"
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert not result.passed
    assert any(issue.domain == "tables" and "TEXT_CHANGED" in issue.code for issue in result.issues)


def test_closed_but_wrong_xref_target_is_reported(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    ids = [element.get("id") for element in tree.getroot().iter() if element.get("id")]
    xref = next(element for element in tree.xpath("//*[local-name()='xref']") if element.get("rid"))
    old = xref.get("rid").split()[0]
    replacement = next(value for value in ids if value != old)
    xref.set("rid", replacement)
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert not result.passed
    assert any(issue.code.endswith("_TARGET_CHANGED") for issue in result.issues)


def test_id_presence_is_required_even_when_spelling_is_free(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    section = tree.xpath("//*[local-name()='sec' and @id]")[0]
    del section.attrib["id"]
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert any(issue.code.endswith("_ID_PRESENCE_CHANGED") for issue in result.issues)


def test_corrupt_media_is_detected_by_bytes_decode_and_source(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    href = tree.xpath("//*[local-name()='graphic']")[0].get(XLINK_HREF)
    (output / href).write_bytes(b"this is not an image")

    result = evaluate_sample("01", output)
    codes = {issue.code for issue in result.issues}
    assert not result.passed
    assert "MEDIA_DECODE_FAILED" in codes
    assert "MEDIA_NOT_FROM_DOCX" in codes
    assert "FIGURE_MEDIA_BYTES_CHANGED" in codes


def test_truncated_jpeg_pixel_stream_fails_strict_decode(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    href = tree.xpath("//*[local-name()='graphic']")[0].get(XLINK_HREF)
    path = output / href
    data = path.read_bytes()
    path.write_bytes(data[:-128])
    result = evaluate_sample("01", output)
    assert any(issue.code == "MEDIA_DECODE_FAILED" for issue in result.issues)


def test_unreferenced_output_file_is_detected(tmp_path):
    output = materialize("01", tmp_path)
    (output / "stale.png").write_bytes(b"stale")
    result = evaluate_sample("01", output)
    assert any(issue.code == "UNREFERENCED_OUTPUT_FILE" for issue in result.issues)


def test_direct_xml_mode_does_not_claim_unrelated_siblings(tmp_path):
    output = materialize("01", tmp_path)
    (output / "unrelated.txt").write_text("not part of the declared package", encoding="utf-8")
    result = evaluate_sample("01", output / "result.xml")
    assert result.passed


def test_directory_with_two_top_level_xml_files_is_rejected(tmp_path):
    output = materialize("01", tmp_path)
    (output / "second.xml").write_bytes((output / "result.xml").read_bytes())
    result = evaluate_sample("01", output)
    assert any(issue.code == "CANDIDATE_XML_AMBIGUOUS" for issue in result.issues)


def test_percent_encoded_parent_path_is_rejected(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    tree.xpath("//*[local-name()='graphic']")[0].set(XLINK_HREF, "%2e%2e/escape.jpg")
    save_candidate(tree, output)
    result = evaluate_sample("01", output)
    assert any(issue.code == "MEDIA_PATH_UNSAFE" for issue in result.issues)


def test_referenced_media_symlink_is_rejected(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    graphics = tree.xpath("//*[local-name()='graphic']")
    first = output / graphics[0].get(XLINK_HREF)
    second = output / graphics[1].get(XLINK_HREF)
    first.unlink()
    first.symlink_to(second.name)
    result = evaluate_sample("01", output)
    assert any(issue.code == "MEDIA_PATH_SYMLINK" for issue in result.issues)


def test_doctype_change_is_a_validity_failure(tmp_path):
    output = materialize("01", tmp_path)
    xml = (output / "result.xml").read_text(encoding="utf-8")
    xml = xml.replace(
        "https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd",
        "wrong.dtd",
    )
    (output / "result.xml").write_text(xml, encoding="utf-8")
    result = evaluate_sample("01", output)
    assert any(issue.code == "DOCTYPE_SYSTEM_INVALID" for issue in result.issues)


def test_internal_dtd_subset_is_forbidden(tmp_path):
    output = materialize("01", tmp_path)
    xml = (output / "result.xml").read_text(encoding="utf-8")
    marker = '"https://jats.nlm.nih.gov/publishing/1.3/JATS-journalpublishing1-3.dtd">'
    xml = xml.replace(marker, marker[:-1] + ' [<!ENTITY hidden "x">]>')
    (output / "result.xml").write_text(xml, encoding="utf-8")
    result = evaluate_sample("01", output)
    assert any(issue.code == "DOCTYPE_INTERNAL_SUBSET_FORBIDDEN" for issue in result.issues)


def test_processing_instruction_cannot_escape_coverage(tmp_path):
    output = materialize("01", tmp_path)
    xml = (output / "result.xml").read_text(encoding="utf-8")
    xml = xml.replace("<front>", "<front><?audit unexpected?></front><front>", 1)
    # 上述做法会先由 DTD 捕获多出的 front；覆盖层还必须独立登记 PI。
    (output / "result.xml").write_text(xml, encoding="utf-8")
    result = evaluate_sample("01", output)
    assert not result.coverage["candidate"].complete
    assert any(issue.code == "COVERAGE_INCOMPLETE" for issue in result.issues)


def test_id_spelling_media_filename_and_numeric_date_are_true_equivalences(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)

    id_map = {}
    for index, element in enumerate(
        (element for element in tree.getroot().iter() if element.get("id")), start=1
    ):
        old = element.get("id")
        new = f"v2id{index}"
        id_map[old] = new
        element.set("id", new)
    for xref in tree.xpath("//*[local-name()='xref']"):
        if xref.get("rid"):
            xref.set("rid", " ".join(id_map.get(value, value) for value in xref.get("rid").split()))

    for index, element in enumerate(
        (element for element in tree.getroot().iter()
         if isinstance(element.tag, str) and etree.QName(element).localname in MEDIA_TAGS),
        start=1,
    ):
        old_href = element.get(XLINK_HREF)
        if not old_href:
            continue
        old_path = output / old_href
        new_href = f"renamed/media-{index}{old_path.suffix}"
        new_path = output / new_href
        new_path.parent.mkdir(parents=True, exist_ok=True)
        old_path.replace(new_path)
        element.set(XLINK_HREF, new_href)

    for element in tree.xpath("//*[local-name()='day' or local-name()='month']"):
        if element.text and element.text.strip().isdigit():
            element.text = element.text.zfill(2)
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert result.passed, [(issue.code, issue.message) for issue in result.issues]


def test_xml_indentation_is_not_article_content(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    for element in tree.getroot().iter():
        if element.text is not None and not element.text.strip() and "\n" in element.text:
            element.text = None
        if element.tail is not None and not element.tail.strip() and "\n" in element.tail:
            element.tail = None
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert result.passed, [(issue.code, issue.message) for issue in result.issues]


def test_media_extension_must_match_real_format(tmp_path):
    output = materialize("01", tmp_path)
    tree = load_candidate(output)
    graphic = tree.xpath("//*[local-name()='graphic']")[0]
    old_href = graphic.get(XLINK_HREF)
    old_path = output / old_href
    new_href = str(Path(old_href).with_suffix(".png"))
    old_path.replace(output / new_href)
    graphic.set(XLINK_HREF, new_href)
    save_candidate(tree, output)

    result = evaluate_sample("01", output)
    assert any(issue.code == "MEDIA_EXTENSION_MISMATCH" for issue in result.issues)


def test_repeated_media_references_are_counted_by_occurrence(tmp_path):
    output = materialize("X02", tmp_path)
    result = evaluate_sample("X02", output)
    assert result.coverage["candidate"].media_links == 33
    assert result.provenance.referenced_media == 33
    assert result.provenance.media_from_docx == 33


def test_word_runs_are_joined_before_tokenization():
    paragraph = etree.fromstring(
        b'<w:p xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
        b'<w:r><w:t>statin</w:t></w:r><w:r><w:t>s</w:t></w:r></w:p>'
    )
    assert _docx_paragraph_text(paragraph) == "statins"


def test_unknown_element_is_strictly_consumed():
    root = etree.fromstring(b"<article><future-element answer='42'>content</future-element></article>")
    document = canonicalize(root, lambda href: None, side="candidate")
    assert document.coverage.complete
    assert document.coverage.elements == 2
    assert document.coverage.attributes == 1
    assert document.fallback_tags["future-element"] == 1


def test_ignored_comment_does_not_drop_following_mixed_text():
    with_comment = etree.fromstring(b"<article><p>A<!-- note -->B</p></article>")
    plain = etree.fromstring(b"<article><p>AB</p></article>")
    first = canonicalize(with_comment, lambda href: None, side="candidate")
    second = canonicalize(plain, lambda href: None, side="gold")
    assert first.root.digest() == second.root.digest()
    assert first.coverage.comments_ignored == 1


def test_reports_are_deterministic_and_self_contained(tmp_path):
    output = materialize("01", tmp_path)
    first = evaluate_sample("01", output)
    second = evaluate_sample("01", output)
    assert json_text(first) == json_text(second)
    report = markdown_text(first)
    assert "完整性覆盖" in report
    assert "质量向量" in report
    assert "docx 来源旁证" in report
    json_path, markdown_path = write_reports(first, tmp_path / "reports")
    assert json_path.read_text(encoding="utf-8") == json_text(first)
    assert markdown_path.read_text(encoding="utf-8") == report
