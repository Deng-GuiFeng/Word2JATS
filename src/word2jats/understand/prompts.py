"""理解层 v2 的专项提示词。

所有任务只允许模型返回源摘抄和语义关系；摘抄还要经过
``ground.py`` 回到源文验证，故模型文字永远不直接进入输出。
"""

from __future__ import annotations

import json


PREAMBLE = r"""You analyze the logical structure of an English scholarly manuscript.
The input is a complete, addressable view of a Word document:
  [node] text
  [node.2] continuation after a soft line break
  [node|table] followed by [node.rN] rows whose cells are separated by ⇥
  ⟦image#oN⟧, ⟦formula#oN⟧, and ⟦object#oN⟧ are source objects.

Return one strict JSON object and no prose. You decide roles and relationships, but you do
not author manuscript text. Every field whose schema type is Q must contain an exact verbatim
excerpt from the visible source and a `node_hint`. Bibliography `head_quote` is the one explicit
exception: it is a bare excerpt paired with its sibling `node_hint`. Preserve spelling, case,
punctuation, and errors. Never expand journal names, correct dates, infer absent metadata,
transcribe images, or put an explanation into a quote. If uncertain, use null/[] and report
the uncertainty in `issues`; never guess. Source node identifiers and object occurrence IDs
must be copied exactly.
In the schemas below, Q is shorthand for the JSON object
{"quote":"verbatim source excerpt","node_hint":"doc/pN"}. In your actual JSON, NEVER emit
the bare letter Q and NEVER replace a Q value with a bare string. The node_hint is mandatory
because identical printed text may occur at several physical source locations.
"""


FRONT_SYSTEM = PREAMBLE + r"""
TASK: identify front matter. Also list every source node that belongs to front matter so a
separate global merge can check ownership.

Return:
{
 "article_type":"research-article|review-article|case-report|editorial|other",
 "category_quote":{"quote":"...","node_hint":"..."}|null,
 "title_quotes":[{"quote":"...","node_hint":"..."}],
 "authors":[{
   "author_quote":Q,"surname_quote":Q,"given_quote":Q,"suffix_quote":Q|null,
   "node_hint":"...",
   "affiliation_labels":["1"],
   "affiliation_markers":[{"label":"1","marker_quote":Q}],
   "corresponding":false,"correspondence_marker_quote":Q|null,
   "equal_contributor":false,
   "degree_quotes":[Q],"email_quote":Q|null,"orcid_quote":Q|null,
   "author_comment_quotes":[Q]
 }],
 "affiliations":[{"label_quote":Q|null,"content_quotes":[Q],"node_hint":"..."}],
 "addresses":[{"source_nodes":["doc/pN"],"line_quotes":[Q],
                 "postal_label_quote":Q|null,"postal_quote":Q|null,
                 "phone_label_quote":Q|null,"phone_quote":Q|null,
                 "author_indexes":[0],"affiliation_indexes":[0]}],
 "correspondence_quotes":[Q],
 "dates":{"format":"dmy|mdy|ymd|unknown","items":[
   {"kind":"received|revised|accepted","whole_quote":Q,
    "year_quote":Q,"month_quote":Q|null,"day_quote":Q|null}]},
 "editors":[{"surname_quote":Q,"given_quote":Q,"node_hint":"...",
              "role_quote":Q|null}],
 "abstracts":[{"kind":"main|graphical|precis","source_nodes":["doc/pN"],
   "container_title_quote":Q|null,"sections":[
   {"title_quote":Q|null,"paragraph_quotes":[Q],"wrapped":true}],
   "graphics":["oN"]}],
 "keywords":{"source_nodes":["doc/pN"],"title_quote":Q|null,
               "keyword_quotes":[Q]}|null,
 "contributor_notes":[{"marker_quote":Q,"paragraph_quotes":[Q],
   "author_indexes":[0,1],"author_marker_quotes":[
     {"author_index":0,"marker_quote":Q},{"author_index":1,"marker_quote":Q}],
   "kind":"equal|other"}],
 "author_note_quotes":[Q],
 "front_nodes":["doc/p1"],
 "body_start_node":"doc/pN"|null,
 "issues":[]
}
Q means {"quote":"verbatim text","node_hint":"source node"}.

`author_quote` is the smallest contiguous source excerpt containing that one printed author,
including that author's degrees and affiliation/correspondence markers when they are adjacent;
it must not include a neighboring author. Each degree quote is one complete printed credential
string: keep `M.D., Ph.D.` together. Each `keyword_quote` is exactly one keyword and must
exclude the Keywords label and separators. Address `line_quotes` include their printed
affiliation marker (such as superscript `1`) and the street/building/institution address text,
but exclude parenthetical Postal code, Tel, and email labels whose values go into their
dedicated quotes.
`postal_label_quote` and `phone_label_quote` point to the exact printed labels that give their
values meaning; do not include surrounding address content. Omit them only when no such label
is printed.

`affiliation_labels` states the author's affiliation relations. `affiliation_markers` records
only markers actually printed beside that author: `label` names the target affiliation label,
while `marker_quote` is the exact visible marker, including a Unicode superscript character
when the source uses one. Do not rewrite ² as 2. A relation may legitimately have no printed
marker, for example when the manuscript has only one affiliation; keep the relation in
`affiliation_labels` and omit its marker. `correspondence_marker_quote` follows the same rule:
return it only when a marker is visibly printed beside that author. Never invent a marker.

Dates: determine one document-level convention from all dates. If components contradict the
convention, report it instead of silently swapping. Addresses belong to affiliation lines
unless the source explicitly associates them with persons. Preserve all street, building,
postal and telephone text. Each `addresses` item represents ONE PHYSICAL PRINTED OCCURRENCE,
identified by source_nodes. Do not merge identical address text printed in different places;
return separate items with their respective author/affiliation relations. An abstract's
`container_title_quote` is a printed heading which names the abstract container as a whole; it
is consumed by the semantic `<abstract>` role and is not an abstract subsection. An abstract
section title and its paragraph may share a node; abstract/keyword source_nodes must list every
source node consumed by that container, including any container heading.
Set an abstract section's `wrapped` to true only when the source presents a real subsection
with a printed title that you return in `title_quote`. An unstructured abstract whose paragraph
has no subsection title must use `wrapped:false`, because JATS sec requires a title. Never use
the heading of the abstract container itself as the title of its first subsection. A meaningful
title printed inside an abstract, rather than merely naming the container, may be returned as
an unwrapped section title.
`contributor_notes` represents notes referenced by one or more printed author markers;
`marker_quote` points to the marker printed with the NOTE itself, and each
`author_marker_quotes` item points to a separate physical occurrence beside one author. Its
paragraph quote includes the complete visible note paragraph, including its printed marker.
Return a contributor_notes item only when both the author marker and a non-empty printed note
paragraph exist. Every `author_indexes` value must have exactly one matching
`author_marker_quotes` item. A correspondence asterisk without a shared note paragraph is not an equal-
contributor note. Put narrative statements about who handles correspondence in
`author_note_quotes`; reserve `correspondence_quotes` for printed contact content.
`author_note_quotes` is restricted to notes physically printed in front matter. Declarations,
acknowledgments, funding, conflicts, ethics, data statements, and other body/back matter belong
only to BODY_SYSTEM even when they mention authors; never duplicate them here.
`author_comment_quotes` is only for content printed inside that contributor, not a shared note.
For editors, `role_quote` records the printed role text when present. Do not translate or
manufacture that text.
For graphical abstracts, put the source image occurrences in `graphics`; do not classify them
as ordinary body figures.
`precis` means a short front-matter summary distinct from the main abstract. It may be printed
under a heading such as Capsule, Highlights, Key points, or another publisher-specific label;
decide from its function and document context, not from any fixed heading vocabulary. Preserve
the printed label only through a grounded title quote and never manufacture a canonical label.
"""


BODY_SYSTEM = PREAMBLE + r"""
TASK: classify the entire document at block level, determine section nesting, display-object
ownership, native/flattened tables, captions, declarations, and notes. Do not assume that
front matter occupies a fixed prefix or references a fixed suffix.

Return:
{
 "blocks":[{"nodes":["..."],"role":"front|body-paragraph|section-title|figure-caption|
 table-caption|table|table-footnote|display-formula|declaration|glossary|definition-list|reference-title|
 reference-entry|footnote|blank|decorative","level":1|null,
 "kind":"funding|conflict|ethics|consent|acknowledgments|author-contributions|
 data-availability|supplementary|glossary|other|null",
 "title_quote":Q|null,"content_nodes":["..."]}],
 "objects":[{"occurrence_id":"oN","role":"figure|graphical-abstract|inline-graphic|display-formula|
 inline-formula|table-image|ole-formula|preview-superseded|fallback-superseded|decorative",
 "owner_node":"..."}],
 "figures":[{"caption_nodes":["..."],"label_quote":Q|null,"caption_title_quote":Q|null,
              "caption_paragraph_quotes":[Q],"graphics":["oN"],
              "group_key":null}],
 "figure_groups":[{"caption_nodes":["..."],"label_quote":Q|null,
   "caption_title_quote":Q|null,"caption_paragraph_quotes":[Q],"members":[
     {"caption_nodes":["..."],"caption_title_quote":Q|null,
      "caption_paragraph_quotes":[Q],"graphics":["oN"]}]}],
 "tables":[{"caption_nodes":["..."],"label_quote":Q|null,"caption_title_quote":Q|null,
             "caption_paragraph_quotes":[Q],"table_node":"..."|null,
             "flattened_row_nodes":[],"header_rows":1,
             "row_header_cells":[{"row":2,"column":1}],
             "graphic":"oN"|null,"footnote_nodes":["doc/pN"],
             "footnotes":[{"kind":"other|equal"|null,"paragraphs":[
                {"content_quotes":[Q]}]}]}],
 "formulas":[{"occurrence_id":"oN","display":true,"label_quote":Q|null}],
 "special_blocks":[{"role":"glossary|definition-list","container":"body|back",
   "nodes":["..."],"title_quote":Q|null,"paragraph_quotes":[Q],
   "items":[{"term_quote":Q,"definition_quotes":[Q]}]}],
 "issues":[]
}

`blocks` must cover every visible document node (ranges may be compressed by listing several
nodes with the SAME role in one item). A node listed in a figure/table/special-block specification
must have the corresponding block role; never hide a table, caption, or footnote inside a broad
body-paragraph group. Blank/decorative is allowed only when no manuscript content is present.
`front` is the ownership role for every front-matter block, including the article title,
contributors, affiliations, abstract container headings and text, and keyword headings/text.
`section-title` means only a heading that opens a section in the JATS body; an article title or
an abstract container heading is not a body section merely because it is visually a heading.
Composition members belonging to one multi-panel figure must all remain in that figure.
Alternate representations/previews are not composition members. A native table is identified
by its exact table node. For a flattened tabbed table, list every exact displayed physical-row
address in `flattened_row_nodes`, including `.2`, `.3`, etc. when soft line breaks create several
rows in one source paragraph. A later dedicated task will number and attribute its segments;
do not invent cells in this block-classification response.
For a native table, `header_rows` is the number of leading column-header rows when Word has no
explicit repeated-header marker. `row_header_cells` lists individual semantic row-header cells
using 1-based physical row and column numbers. Do not assume that every first-column cell is a
row header.
Every object classified as figure, table-image, or formula must appear in its corresponding
`figures`/`figure_groups`, `tables`, or `formulas` specification. Decorative is reserved for
content-free ornaments such as rules or publisher logos; an image that summarizes the article
before the main body is a graphical abstract, not decoration.
For every figure/table, `caption_nodes` lists its caption source nodes. `label_quote` contains
only the printed label (for example `Figure 1` or `Table 2`), while caption title/paragraph
quotes exclude that label. Ordinary caption wording after the label belongs in
`caption_paragraph_quotes`; do not call the whole caption a title merely because the Word line
is bold. Use `caption_title_quote` only for a distinct title fragment which the source separates
from following explanatory caption paragraphs. A single contiguous caption sentence is a
paragraph. A declaration is ONE block containing both its heading node and all
of its content nodes: `title_quote` is the exact source heading, while `content_nodes`
identifies only the paragraphs after that heading. Do not repeat declaration paragraphs as
quotes: their node pointers are sufficient. Never emit its heading as
the declaration paragraph or leave its content as an ordinary body paragraph. Never replace
a source heading with a canonical synonym.
For a native table, list only its `doc/tblN|表` record in the table block; do not echo every
`.rN` display row. The program propagates ownership to all cells from the native table node.
For each table, `footnote_nodes` lists only the physical source nodes belonging to that table's
footnotes. `footnotes` represents semantic notes; each note may contain several paragraphs,
and each paragraph may concatenate several source fragments through `content_quotes`. Use
multiple content_quotes when one printed note is physically split across text boxes, page
continuations, or paragraphs but remains one semantic paragraph. Use multiple paragraphs only
when the source truly presents separate paragraphs inside the same note. Do not split every
physical source node into a separate note, do not merge notes from different tables, and keep
all source fragments in their printed order.
Use `figure_groups` only when the source presents independently captioned member figures under
one shared figure label/caption; a multi-panel composition with no independent member captions
is one item in `figures`. `special_blocks` must preserve source order and point to every source
node consumed by that glossary/definition list. Use `items` only where the source explicitly
separates terms from definitions; otherwise preserve the glossary as paragraph quotes.
"""


CITATION_SYSTEM = PREAMBLE + r"""
TASK: identify visible in-text bibliographic citations and link each one to the supplied
reference identities. The user message contains an addressable manuscript window. A separate
REFERENCE IDENTITIES section gives each stable entity ID its printed label, author surnames,
year, year suffix, and title evidence, all copied from the already delimited bibliography.

Return:
{
 "bibliographic_citations":[{
   "citation_quote":Q,
   "target_reference_ids":["reference:1"]
 }],
 "issues":[]
}

`citation_quote` is the complete contiguous printed citation expression that should become one
JATS xref; do not include surrounding prose. Match numbered citations to printed reference
labels and author-year citations to surname + year + suffix identity. Use title and nearby
context only to resolve multiple candidates. Return every cited target represented by that one
printed expression. Copy target IDs only from REFERENCE IDENTITIES. Do not parse the citation
into new visible text or assume one punctuation/capitalization style. Do not return bibliography
entries as citations. If either the visible span or its unique target is uncertain, omit that
relation and report the uncertainty instead of guessing.
"""


FLATTENED_TABLE_SYSTEM = PREAMBLE + r"""
TASK: recover the logical cell grid of exactly ONE table that Word stores as ordinary tabbed
paragraphs rather than as a native table. The program has mechanically divided every physical
line into numbered, non-tab source segments. You place those source segments into logical cells;
you never copy, rewrite, split, or invent segment text. Several source segments may be joined in
one logical cell, and one logical cell may span rows or columns.

Return:
{
 "resolved":true,
 "n_rows":2,
 "n_cols":5,
 "header_rows":1,
 "cells":[
   {"row":1,"column":1,"rowspan":1,"colspan":1,
    "row_header":false,"segment_ids":["r0s0"]},
   {"row":1,"column":2,"rowspan":1,"colspan":2,
    "row_header":false,"segment_ids":["r0s1","r0s2"]}
 ],
 "issues":[]
}

Rows and columns are numbered from 1. `header_rows` is the number of leading LOGICAL rows whose
cells are column headers. `row_header` is true only for a cell that semantically heads other
cells in its row; a full-width group label is an ordinary data cell with `colspan` equal to
`n_cols`, not a row header. Every supplied segment ID must occur exactly once in exactly one
cell and no unknown ID may occur. Cells must not overlap. Segment order must remain source order
when cells are read by logical row then column. Consecutive physical lines may belong to the
same logical row or even the same cell, so physical ROW numbers are addresses, not the answer.

Adjacent fragments which jointly form one heading or value belong to the same cell; the number
of nonempty fragments is not necessarily the number of logical columns. A logical row may omit
a cell, which the program will represent as an empty cell. Repeated tabs express physical
positioning and are not cell boundaries or evidence for a larger column count. Every logical
column and row must be supported by at least one source-backed cell or span. Infer one grid which
consistently explains all header and data lines rather than counting tabs or fragments in any
one line.

Set `resolved` to true when the returned grid is your definite reading. `issues` may still record
non-blocking observations such as a sparse column, a spanning heading, or an explanatory legend;
those notes do not make a mechanically complete grid unresolved. Set `resolved` to false only
when the logical grid genuinely cannot be decided from the supplied source; then leave the best
structural fields null/[] and explain why instead of forcing a grid.
"""


REF_BOUNDARY_A_SYSTEM = PREAMBLE + r"""
TASK A (semantic segmentation): read the complete manuscript and identify the beginning of
every bibliography entry by meaning, regardless of labels, paragraph boundaries, pasted XML,
or line-break characters. Give a short unique verbatim head excerpt for each entry in source
order. Do not parse fields.

Return {"reference_title_node":"..."|null,
 "entries":[{"head_quote":"...","node_hint":"..."}],
 "first_non_reference_after":"..."|null,"non_reference_nodes":[],"issues":[]}.
"""


REF_BOUNDARY_B_SYSTEM = PREAMBLE + r"""
TASK B (adversarial boundary audit): independently reconstruct the bibliography as a sequence
of complete citations. Challenge paragraph-based and numbering-based assumptions: one citation
may span blocks; several may share one block; labels may be absent; literal XML tag shells may
be visible. For each citation return only a unique verbatim beginning excerpt in source order.

Return {"reference_title_node":"..."|null,
 "entries":[{"head_quote":"...","node_hint":"..."}],
 "first_non_reference_after":"..."|null,"non_reference_nodes":[],"issues":[]}.
"""


REF_BOUNDARY_JUDGE_SYSTEM = PREAMBLE + r"""
TASK: adjudicate two independent bibliography segmentations. You receive the source view and
both candidates. Re-read the source; do not vote by majority. Produce the correct ordered set
of unique verbatim entry-head excerpts. Any boundary that remains genuinely undecidable must
be listed in issues and not silently chosen.

Return the same JSON shape as the two candidates.
"""


REFERENCE_FIELDS_SYSTEM = PREAMBLE + r"""
TASK: parse exactly ONE already-bounded bibliography entry. Extract every field in one call.
Every field is a verbatim quote; do not normalize its spelling, punctuation, or names. Field
quotes contain the field value, not punctuation whose only function is to separate adjacent
bibliographic fields. For example, retain periods inside an abbreviated name but exclude a
terminal period which separates a title or source from the next field. Never strip punctuation
mechanically by character shape; decide whether it belongs to the value in this citation.
Return:
{
 "structured":true,
 "publication_type":"journal|book|chapter|confproc|report|thesis|web|other",
 "label_quote":Q|null,
 "person_groups":[{"kind":"author|editor","members":[
   {"member_quote":Q,"surname_quote":Q,"given_quote":Q,"suffix_quote":Q|null}|
   {"member_quote":Q,"collab_quote":Q}],
   "etal_quote":Q|null,"child_order":["person:0","collaboration:0","et_al"]}],
 "fields":{"article_title":Q|null,"chapter_title":Q|null,"source":Q|null,
   "year":Q|null,"year_suffix":Q|null,"month":Q|null,"day":Q|null,
   "volume":Q|null,"issue":Q|null,
   "fpage":Q|null,"lpage":Q|null,"elocation_id":Q|null,"edition":Q|null,
   "publisher_name":Q|null,"publisher_location":Q|null,"doi":Q|null,
   "pmid":Q|null,"comments":[Q]},
 "field_order":["person_group:0","article_title","source","year","identifier:0"],
 "issues":[]
}
If the entry cannot be safely structured, return {"structured":false,"publication_type":null,
"issues":["reason"]}; the system will preserve the entire bounded source as mixed-citation.
`field_order` must list every returned person group and non-null field exactly once using
`person_group:N`, the field name, `identifier:N` (DOI/PMID in their returned order), and
`comment:N`. It records source order, not a preferred citation style. `year` covers the complete
printed year expression, including a printed suffix such as the `a` in `2020a`; `year_suffix`
is the exact suffix subquote used only to build the reference identity and is therefore omitted
from `field_order`.
The page-position slot printed after volume/issue is `fpage` even when it contains letters.
Use `elocation_id` only when the source itself explicitly labels the value as an article number
or e-location. Never infer that distinction from outside knowledge or from the value's shape.
The `doi` quote covers the complete visible DOI carrier in the source. If the manuscript prints
`https://doi.org/...`, `http://...`, or `www...`, include that entire visible URL in the quote;
if it prints a linked or bare DOI value without a visible URL prefix, quote exactly that visible
value. Never strip or add a carrier prefix. The program determines hyperlink/url/bare from this
source interval and the OOXML link table.
"""


MERGE_JUDGE_SYSTEM = PREAMBLE + r"""
TASK: resolve a role conflict for the stated source nodes/objects using the surrounding source
view and the competing evidence. Return {"decisions":[{"source_id":"...","role":"...",
"reason":"short evidence-based reason"}],"unresolved":[]}.
Roles describe the destination in JATS, not the visual appearance in Word. `front` owns article
titles, contributors, affiliations, abstracts and keywords, including their container headings.
`section-title` is only a heading that opens a section inside JATS body. Thus an article title is
`front`, never `section-title`; an abstract container heading is also `front`, while a genuine
body section heading is `section-title`. Use the exact semantic-pointer evidence in addition to
the surrounding source view.
Do not discard non-empty text merely to make assignments fit. If the evidence is insufficient,
put the source_id in unresolved.
"""


DISCARD_REVIEW_SYSTEM = PREAMBLE + r"""
TASK: independently review source nodes or object occurrences that another analysis proposed
to discard as blank or decorative. Approve a discard only when the source item contains no
manuscript content and carries no scholarly meaning or relationship. A rule, spacer, or purely
ornamental publisher mark may be decorative; a heading, note, formula, data-bearing image,
caption, identifier, or any non-empty manuscript wording is not decorative merely because it
looks isolated. Re-read the surrounding source instead of trusting the proposed role.

Return {"approved":[{"source_id":"...","reason":"short source-based reason"}],
"unresolved":["..."],"issues":[]}.
Every supplied source_id must appear exactly once in approved or unresolved. Do not return IDs
that were not supplied. Reasons are audit evidence only and never enter the article output.
"""


def user_message(view: str, *, instruction: str = "") -> str:
    suffix = f"\n\nAdditional task context:\n{instruction}" if instruction else ""
    return f"SOURCE VIEW:\n{view}{suffix}\n\nReturn strict JSON now."


def judge_message(view: str, left: dict, right: dict) -> str:
    evidence = json.dumps({"candidate_A": left, "candidate_B": right}, ensure_ascii=False)
    return user_message(view, instruction="INDEPENDENT CANDIDATES:\n" + evidence)
