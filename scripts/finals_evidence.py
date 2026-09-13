"""独立核对决赛候选文件，不读转换器的自报计数推定内容完整。"""
from __future__ import annotations
import argparse
from collections import Counter
import hashlib
import json
from pathlib import Path
import re
import zipfile
from lxml import etree
from scripts.eval_v1.samples import SAMPLES
from scripts.output_manifest import resolve_output
from word2jats.validate.dtd import validate_bytes

ROOT = Path(__file__).resolve().parents[1]
EXPECTED_REFS = [39, 69, 56, 145, 27, 30, 50, 22, 41, 46, 30, 32, 35, 70]
M = "http://schemas.openxmlformats.org/officeDocument/2006/math"
W = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"


def flat(element):
    return re.sub(r"\s+", "", "".join(element.itertext()))


def collect(tag):
    root = ROOT / "reports/outputs" / tag
    transform = etree.XSLT(etree.parse(str(ROOT / "src/word2jats/resources/OMML2MML.XSL")))
    rows = []
    for sample, ref_count in zip(SAMPLES, EXPECTED_REFS):
        location = resolve_output(root, sample.key)
        xml_bytes = location.candidate_xml.read_bytes()
        doc = etree.fromstring(xml_bytes).getroottree()
        dtd_check = validate_bytes(xml_bytes)
        report = json.loads((location.candidate_dir.parent / "report.json").read_text())
        with zipfile.ZipFile(sample.docx) as archive:
            original = etree.fromstring(archive.read("word/document.xml"))
            source_media = {hashlib.sha256(archive.read(n)).hexdigest() for n in archive.namelist() if n.startswith("word/media/")}
        expected = Counter()
        for equation in original.iter(f"{{{M}}}oMath"):
            converted = transform(equation)
            expected["".join(flat(node) for node in converted.xpath("/*"))] += 1
        actual = Counter(flat(node) for node in doc.xpath("//*[local-name()='math']"))
        orcid_pattern = r"\d{4}-\d{4}-\d{4}-\d{3}[\dX]"
        source_orcid = set(re.findall(orcid_pattern, "".join(original.itertext())))
        output_orcid = set(re.findall(orcid_pattern, "".join(doc.xpath("//contrib-id[@contrib-id-type='orcid']//text()"))))
        hrefs = doc.xpath("//*[local-name()='graphic' or local-name()='inline-graphic']/@*[local-name()='href']")
        media_ok = all((location.candidate_dir / href).is_file() and
                       hashlib.sha256((location.candidate_dir / href).read_bytes()).hexdigest() in source_media for href in hrefs)
        ids = set(doc.xpath("//@id"))
        broken = [r for r in doc.xpath("//xref/@rid") if any(x not in ids for x in r.split())]
        timing_path = root / f"{sample.key}.timing.json"
        timing = json.loads(timing_path.read_text()) if timing_path.exists() else {}
        row = {"sample": sample.key, "group": sample.group, "delivered": location.delivered,
               "dtd_valid": dtd_check.well_formed and dtd_check.valid,
               "wall_seconds": timing.get("wall_seconds"),
               "calls": (timing.get("llm") or {}).get("calls"), "tokens": (timing.get("llm") or {}).get("tokens"),
               "rate_limit_events": (timing.get("llm") or {}).get("rate_limit_retries"),
               "references": len(doc.xpath("//ref-list/ref")), "expected_references": ref_count,
               "structured_references": len(doc.xpath("//ref-list/ref/element-citation")),
               "figures": len(doc.xpath("//fig")), "tables": len(doc.xpath("//table-wrap")),
               "bibr_links": len(doc.xpath("//xref[@ref-type='bibr']")), "broken_rids": broken,
               "omml_source": sum(expected.values()), "mathml_output": sum(actual.values()),
               "missing_formula_texts": list((expected - actual).elements()),
               "orcids_source": sorted(source_orcid), "orcids_output": sorted(output_orcid),
               "missing_orcids": sorted(source_orcid - output_orcid),
               "media_source_bytes": media_ok, "media_references": len(hrefs),
               "review_issue_codes": dict(Counter(i["code"] for i in report["understanding"]["issues"])),
               "xml_sha256": hashlib.sha256(location.candidate_xml.read_bytes()).hexdigest()}
        rows.append(row)
    return {"output_tag": tag, "samples": rows}


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--tag", required=True)
    args = parser.parse_args()
    summary = collect(args.tag)
    target = ROOT / "reports/finals-closeout" / f"{args.tag}.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n")
    for row in summary["samples"]:
        print(row["sample"], "refs", row["references"], "OMML", row["omml_source"],
              "missing-formula", row["missing_formula_texts"], "missing-ORCID", row["missing_orcids"],
              "media", row["media_source_bytes"], "delivered", row["delivered"])


if __name__ == "__main__":
    main()
