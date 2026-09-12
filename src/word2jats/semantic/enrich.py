"""把明示的出版工作流配置注入 SemanticDoc v2。"""

from __future__ import annotations

from dataclasses import replace

from . import model as sm
from ..config import PubConfig


CC_BY = "https://creativecommons.org/licenses/by/4.0/"

# 无印刷标题的声明节按出版体例补模板标题（B 档模板文字，逐键记账）。
# 标题字符串与金标准体例一致；源稿印了标题时以源稿为准，这里不覆盖。
DECLARATION_TITLES = {
    "author-contributions": "Author Contributions",
    "ethics": "Ethics Approval and Consent to Participate",
    "funding": "Funding",
    "ack": "Acknowledgment",
    "conflict": "Conflicts of Interest",
    "data-availability": "Data Availability Statement",
}


def _config(key: str, value) -> sm.RichText | None:
    if value is None or str(value) == "":
        return None
    return sm.RichText((sm.ConfigText(key, str(value)),))


def apply_publication_config(document: sm.SemanticDoc, registry, config: PubConfig):
    info = registry.get(config.journal_id) or {}
    journal_id = info.get("journal-id") or config.journal_id
    identifiers = (() if not journal_id else (
        sm.JournalIdentifier("publisher-id", _config(
            "journal.journal-id", journal_id
        )),
    ))
    abbreviated = tuple(
        sm.TypedText(kind, _config(f"journal.{key}", info[key]))
        for kind, key in (("publisher", "abbrev-publisher"),
                          ("pubmed", "abbrev-pubmed"))
        if info.get(key)
    )
    issns = tuple(
        sm.Issn(kind, _config(f"journal.{key}", info[key]))
        for kind, key in (("ppub", "issn-ppub"), ("epub", "issn-epub"))
        if info.get(key)
    )
    journal = sm.JournalMeta(
        identifiers=identifiers,
        title=_config("journal.journal-title", info.get("journal-title")),
        abbreviated_titles=abbreviated, issns=issns,
        publisher_name=_config("journal.publisher", registry.publisher),
    )
    article_ids = ()
    if config.doi:
        value = _config("PubConfig.doi", config.doi)
        article_ids = (
            sm.ArticleIdentifier("doi", value),
            sm.ArticleIdentifier("publisher-id", value),
        )

    permissions = None
    if config.publication_year:
        year = config.publication_year
        statement = f"Copyright: © {year} The Author(s). Published by {registry.publisher}."
        lead = "This is an open access article under the "
        link_text = "CC BY 4.0 license"
        license_text = sm.RichText((
            sm.ConfigText("license.lead", lead),
            sm.ExternalLink("uri", CC_BY, sm.RichText((
                sm.ConfigText("license.label", link_text),
            )), "license.href"),
            sm.ConfigText("license.period", "."),
        ))
        permissions = sm.Permissions(
            copyright_statement=_config("template.copyright-statement", statement),
            copyright_year=_config("PubConfig.publication_year", year),
            license=sm.License("open-access", CC_BY, (license_text,), "license.href"),
        )

    notes = list(document.notes)
    if config.include_publisher_note:
        label = sm.RichText((sm.Styled("bold", sm.RichText((
            sm.ConfigText("template.publisher-note-label", "Publisher’s Note: "),
        ))), sm.ConfigText(
            "template.publisher-note-body",
            f"{registry.publisher} stays neutral with regard to jurisdictional claims "
            "in published maps and institutional affiliations.",
        )))
        notes.append(sm.Note(
            "publisher-note", None, None, (label,), "article", emit_id=False,
        ))
    back_sections = []
    for section in document.back_sections:
        template = DECLARATION_TITLES.get(section.kind)
        if section.title is None and template:
            back_sections.append(replace(section, title=sm.RichText((
                sm.ConfigText(
                    f"template.declaration-title.{section.kind}", template
                ),
            ))))
        else:
            back_sections.append(section)

    document.journal = journal
    document.article_identifiers = article_ids
    document.permissions = permissions
    document.notes = tuple(notes)
    document.back_sections = tuple(back_sections)
    document.validate()
    return document
