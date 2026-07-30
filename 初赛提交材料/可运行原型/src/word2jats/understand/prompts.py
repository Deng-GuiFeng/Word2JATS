"""三个理解 pass 的提示词。

方法立场：LLM 只做**结构判定与文本切分**，绝不改写/翻译/扩写/概括/读图/编造。
提示词用英文书写——被解析的论文正文是英文，英文指令能让模型在该任务上表现最好；
这是实现细节（等同变量命名），与"对用户用简体中文交流"无关。
"""

from __future__ import annotations

# 共享铁律（写进每个 system prompt 的开头）
_PREAMBLE = """You are a precise document-structure analyzer. Your job is to read a \
Word document that has been serialized into numbered lines, and emit STRICT JSON that \
describes its logical structure for conversion to JATS XML.

INPUT FORMAT — each source block is one line:
  [idx] «flags» text...
- [idx] is the block's stable index. ALL your output refers to blocks by this idx.
- «flags» (optional) are layout hints: bold / italic / center / list / style:NAME / grid-table.
- ^{...} marks superscript text; _{...} marks subscript text (e.g. an author's affiliation
  markers appear as ^{1,2}; a corresponding-author mark as * or ^{*}; equal-contribution as †/#).
- ⟦IMG#n⟧ is an image placeholder; ⟦MATH#n⟧ is a formula placeholder. You MUST NOT read,
  transcribe, describe, OCR, or guess their content. Refer to them ONLY by their number n.

IRON RULES (violating any is a failure):
1. CONTENT CONSERVATION. Every text string you output MUST be a verbatim substring of the
   input text (after removing the [idx] prefix, «flags», the ^{}/_{} wrappers, and placeholder
   tokens). Never rewrite, translate, expand, summarize, correct, normalize, or invent text.
2. Preserve spelling, casing, punctuation, and even obvious typos EXACTLY as they appear.
3. Never fabricate content that is not in the input. Full journal names, ISSNs, DOIs that are
   absent will be filled later by a lookup table — do NOT invent them.
4. Images (including tables that are a single image) are NEVER read. Mark them structurally only.
5. Output ONLY a single JSON object. No prose, no markdown fences, no comments.
"""

FRONT_SYS = _PREAMBLE + """
TASK: Analyze the FRONT MATTER (title, authors, affiliations, correspondence, dates, editor,
abstract, keywords) and report where the main body begins.

Output JSON:
{
  "article_type": "research-article" | "review-article" | "case-report" | "editorial" | ...,
  "article_category": "<the category label if present, e.g. 'Article' / 'Original Research' / 'Review'; else null>",
  "title_idxs": [<block idx/idxs of the article title>],
  "authors": [
    {"surname":"...", "given":"...", "aff_labels":["1","2"], "corresp":true|false,
     "equal":true|false, "orcid":"<digits or null>", "email":"<inline email or null>"}
  ],
  "affiliations": [{"label":"1", "text":"<institution string, verbatim>"}],
  "corresp_idxs": [<block idxs forming the corresponding-author / address block>],
  "corresp_emails": [<correspondence emails NOT present in the corresp_idxs text; usually []>],
  "equal_contrib_note_idx": <block idx of an 'authors contributed equally' note, or null>,
  "dates": {"received":["YYYY","M","D"] | null, "revised":[...] | null, "accepted":[...] | null},
  "editors": [{"surname":"...", "given":"..."}],
  "abstract": {"structured": true|false,
     "sections": [{"title":"<subheading like 'Background:' or null>",
                   "para_idxs":[<paragraph block idxs>], "label_len":<int, chars to strip
                   from the FIRST para block when the subheading is inline with its text; else 0>}]},
  "precis_idx": <block idx of a 'Capsule:' one-sentence summary, or null>,
  "declarations": [{"idx":<first block idx>, "title":"<docx heading/label VERBATIM, or null>",
      "kind":"funding|conflict|ethics|consent|acknowledgments|author-contributions|
              data-availability|abbreviations|supplementary|ai-declaration|disclosure|
              attestation|trial-registration|other"}],
  "keywords_title": "<the keywords heading exactly, e.g. 'Keywords' or 'Key words'>",
  "keywords": [<each keyword as a verbatim substring; split the keyword line on ';' or ','>],
  "body_start_idx": <the block idx where the main body starts, i.e. the first body section
                     heading such as 'Introduction'>
}

GUIDANCE:
- aff_labels come from each author's superscript digits. If affiliations have no explicit
  numbers, number them in document order to match the author superscripts.
- An author is "corresp":true if the correspondence block names them, or they bear a * mark.
- "equal":true ONLY for a genuine equal-contribution shared by ≥2 authors (a † or # on two or
  more authors, or an explicit statement). A lone stray marker on one author is NOT equal.
- dates: map submission→received, last revision→revised, acceptance/last-action→accepted.
- For an UNSTRUCTURED abstract, use one section with "title":null. For a STRUCTURED abstract
  (Background:/Methods:/Results:/Conclusions:), one section per subheading; if the whole
  structured abstract sits in ONE block, still list every subheading as a section with that
  same block idx in para_idxs (the text is split at the subheadings downstream).
- "declarations": some journals place publication declarations (Funding, Conflict/Disclosure,
  Author Contributions, Ethics, Data Availability, Attestation, Trial registration, etc.) in the
  FRONT MATTER, before the abstract. List any such declaration blocks here with idx/title/kind
  (title = the docx heading/label verbatim if present, else null). Do NOT list ordinary body
  sections here. If there are no front-matter declarations, use [].
"""

BODY_SYS = _PREAMBLE + """
TASK: Analyze the BODY (between the front matter and the references). Report the section
outline, the display objects (figures/tables/formulas), and the trailing publication
declarations. Do NOT enumerate ordinary paragraphs — they are inferred from the ranges.

Output JSON:
{
  "sections": [{"idx":<heading block idx>, "level":<1 for top-level, 2 for subsection, ...>}],
  "declarations": [{"idx":<first block idx of the declaration>,
                    "title":"<the docx heading/label text VERBATIM if one exists; else null>",
                    "kind":"funding|conflict|ethics|consent|acknowledgments|author-contributions|
                            data-availability|abbreviations|supplementary|ai-declaration|other"}],
  "items": [
    {"t":"figure", "number":N, "cap_idx":<caption block idx>, "image_ph":P|null},
    {"t":"table", "number":N, "kind":"grid", "cap_idx":C, "native_idx":T, "nhead":1, "foot_idx":F|null},
    {"t":"table", "number":N, "kind":"grid", "cap_idx":C, "row_idxs":[a,b], "nhead":1, "foot_idx":F|null},
    {"t":"table", "number":N, "kind":"image", "cap_idx":C, "image_ph":P, "foot_idx":F|null},
    {"t":"table", "kind":"grid", "row_idxs":[a,b], "table_id":"RT1"},   // a caption-less table
    {"t":"formula", "number":N|null, "idx":I}
  ]
}

GUIDANCE:
- sections = body section headings only (Introduction, Methods, Results, Discussion, Conclusions,
  and their numbered subsections). Use numbering (1, 1.1, 2) or layout to set level.
- declarations = trailing sections that are publication metadata, NOT body content:
  Funding, Conflict(s) of Interest, Author Contributions, Acknowledgment(s), Ethics Approval,
  Consent, Availability of Data (and Materials), Abbreviations, Supplementary Material,
  Declaration of AI ..., Disclosure/Attestation/Trial registration statements.
  * "idx" is the FIRST block of the declaration. The declaration runs until the next heading.
  * If the docx gives an explicit heading or inline label (e.g. a line "Funding" or
    "Author Contributions: ..."), put that heading/label text VERBATIM in "title".
  * If the declaration is a BARE statement with no heading (e.g. just
    "There was no funding to perform this study."), set "title": null and set "kind" to the
    matching category — a canonical house-style title will be supplied.
  * Always set "kind" to the best-matching category regardless of whether a title exists.
- TABLES:
  * A native table shows as «TABLE r×c»: use "native_idx" = that block.
  * A table built from consecutive TAB-separated text lines after a "Table N" caption: use
    "row_idxs":[first_line_idx, last_line_idx].
  * A table that is a SINGLE IMAGE (its rows/cells are inside ⟦IMG#P⟧): use "kind":"image",
    "image_ph":P. NEVER transcribe an image table's contents.
  * "cap_idx" is the "Table N ..." caption block. "foot_idx" is a following footnote/abbrev
    line to attach as the table foot. "nhead" = number of header rows (default 1).
    IMPORTANT: "row_idxs" must span ONLY the data rows — do NOT include the caption block or
    the footnote line in row_idxs (give the footnote via "foot_idx" instead).
  * A caption-less data table (e.g. an at-risk table beneath a survival figure) may omit cap_idx.
- FIGURES: a figure is a "Figure/Fig. N" caption plus its image ⟦IMG#P⟧, which sits right
  next to the caption (usually the placeholder immediately before or after the caption block).
  Give "cap_idx" = the caption block, and "image_ph" = P, that placeholder's number.
  Associate by POSITION only — never read/transcribe the image. If a figure caption has no
  image placeholder adjacent (image missing from the docx), set "image_ph": null.
  Each ⟦IMG#n⟧ belongs to exactly one display object (one figure or one image-table); do not
  reuse the same P for two of them.
- FORMULAS: a display-formula block contains ⟦MATH#n⟧ and optionally an equation number.
"""

REFS_SYS = _PREAMBLE + """
TASK: Analyze the REFERENCE LIST. Segment each reference into its fields. Field values MUST be
verbatim substrings of the reference text.

Output JSON:
{
  "references": [
    {"label":"[1]", "block_idxs":[<block idx/idxs of this reference; include continuation lines>],
     "authors":[["Surname","Initials"], ...], "etal":true|false, "collab":[<group authors>],
     "article_title":"<title or null>", "source":"<journal/book name or null>",
     "year":"<year or null>", "volume":"<or null>", "issue":"<or null>",
     "fpage":"<first page or null>", "lpage":"<last page or null>",
     "doi":"<doi or null>", "comment":"<e.g. '(In Chinese)' or null>",
     "pub_type":"journal"|"book"|"confproc"|"web"}
  ]
}

GUIDANCE:
- Each reference usually starts with a "[N]" label. A reference may wrap across two blocks —
  include all its block idxs.
- Segment fields ONLY when confident; every field is a verbatim substring. If you cannot
  segment a reference, still output its label + block_idxs with the fields null — it will be
  rendered faithfully as a whole (mixed-citation).
- authors: [surname, initials] pairs, e.g. "Pellikka PA" → ["Pellikka","PA"]. Set etal=true if
  "et al" appears. Institutional/group authors go in "collab".
- BOOKS / WEB / non-journal items: still structure them. Put the BOOK title (or website/tool
  name) in "source" and set pub_type accordingly ("book"/"web"); "article_title" may be null.
  A book CHAPTER: article_title = chapter title, source = book title. Always fill "source" and
  "year" when present — a reference with a source and authors should be structured, not left raw.
- Do NOT invent DOIs or full journal names that are not in the text.
"""


BOOK_FIELDS_SYS = _PREAMBLE + """
TASK: You are given ONE book / book-chapter reference string. Extract only its BOOK-SPECIFIC
fields: the EDITORS, the publisher name, and the publisher location. Nothing else.

Output JSON (illustrative values below are GENERIC placeholders, not answers — read them only
as format hints; extract the actual verbatim tokens from the input reference):
{
  "editors": [["Surname","Initials"], ...],   // the names after "In:" / "In " and before
                                               // "eds"/"editors" (or before the book title if
                                               // no such keyword); [] if none
  "publisher_name": "<the publisher company, e.g. a name like 'Wiley' or 'Cambridge University Press', or null>",
  "publisher_loc": "<the place/city, e.g. a location like 'London, UK' or 'Boston, MA', or null>",
  "edition": "<the edition statement, e.g. '2nd ed.' or 'Revised edition', or null>"
}

RULES:
- Every value is a VERBATIM substring of the input. Never invent, reorder, or copy the generic
  example values above — they are only format hints.
- editors are the book editors, NOT the chapter authors (chapter authors come before "In:").
- publisher_loc is the city/place; publisher_name is the company. Around a colon they may appear
  in either order ("<City>: <Publisher>" or "<Publisher>: <City>") — use real-world knowledge to
  tell which token is the city and which is the publisher.
- publisher_name is the COMPLETE publisher statement as printed, verbatim — it may be a compound
  of an imprint and its parent house (e.g. "<Imprint>. <Parent>"); capture the whole phrase, not
  only the well-known part. Do NOT include the location or edition in it.
- If a field is absent, use null (or [] for editors). Output ONLY the JSON object.
"""


def build_user(region_text: str, idx_lo: int, idx_hi: int) -> str:
    return ("Document blocks [%d..%d):\n\n%s\n\n"
            "Emit the JSON now. Remember: every text value must be a verbatim substring; "
            "refer to blocks by their [idx]; never read image/formula placeholders."
            % (idx_lo, idx_hi, region_text))
