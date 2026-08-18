"""把明示的出版工作流配置注入 SemanticDoc v2。"""

from __future__ import annotations

from . import model as sm
from ..config import PubConfig


CC_BY = "https://creativecommons.org/licenses/by/4.0/"


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
    document.journal = journal
    document.article_identifiers = article_ids
    document.permissions = permissions
    document.notes = tuple(notes)
    document.validate()
    return document
