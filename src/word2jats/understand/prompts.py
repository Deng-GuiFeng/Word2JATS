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
{"quote":"verbatim source excerpt","node_hint":"doc/pN","left_context":"","right_context":""}.
In your actual JSON, NEVER emit the bare letter Q and NEVER replace a Q value with a bare string.
The node_hint is mandatory because identical printed text may occur at several physical source
locations. If the same quote occurs more than once inside that node, copy enough immediately
adjacent source text into `left_context` and/or `right_context` to identify exactly the intended
occurrence. Context is positioning evidence only and is not output. Use empty strings only when
the quote is already unique in its node. Never paraphrase or overlap the quote itself in context.
Context may extend beyond a smaller semantic owner such as one author's `author_quote`, but it
must remain inside the SAME underlying source node named by `node_hint`. Never copy text from a
different bracketed base node, never cross from `[doc/pN]` to `[doc/pN+1]`, and never put a
printed record address or an artificial newline into context. A numbered continuation such as
`[doc/pN.2]` belongs to the same underlying node as `[doc/pN]`; no other record does.
"""


FRONT_SYSTEM = PREAMBLE + r"""
TASK: identify front matter. Also list every source node that belongs to front matter so a
separate global merge can check ownership.

Return:
{
 "article_type":"research-article|review-article|case-report|editorial|other"|null,
 "category_quote":{"quote":"...","node_hint":"..."}|null,
 "title_quotes":[{"quote":"...","node_hint":"..."}],
 "authors":[{
   "entity_id":"author:1",
   "author_quote":Q,"surname_quote":Q,"given_quote":Q,"suffix_quote":Q|null,
   "degree_quotes":[Q],"email_quotes":[Q],"orcid_quote":Q|null,
   "author_comment_quotes":[Q]
 }],
 "affiliations":[{"entity_id":"affiliation:1","label_quote":Q|null,
                    "content_quotes":[Q]}],
 "addresses":[{"entity_id":"address:1","source_nodes":["doc/pN"],"line_quotes":[Q],
                 "postal_label_quote":Q|null,"postal_quote":Q|null,
                 "phone_label_quote":Q|null,"phone_quote":Q|null}],
 "correspondences":[{"entity_id":"correspondence:1","content_quotes":[Q]}],
 "dates":{"format":"dmy|mdy|ymd|unknown","items":[
   {"kind":"received|revised|accepted","whole_quote":Q,
    "year_quote":Q,"month_quote":Q|null,"day_quote":Q|null}]},
 "editors":[{"surname_quote":Q,"given_quote":Q,"role_quote":Q|null}],
 "abstracts":[{"kind":"main|graphical|precis","source_nodes":["doc/pN"],
   "container_title_quote":Q|null,"sections":[
   {"title_quote":Q|null,"paragraph_quotes":[Q],"wrapped":true}],
   "graphics":["oN"]}],
 "keywords":{"source_nodes":["doc/pN"],"title_quote":Q|null,
               "keyword_quotes":[Q]}|null,
 "contributor_notes":[{"entity_id":"note:1","marker_quote":Q,
   "paragraph_quotes":[Q],"kind":"equal|other"}],
 "relations":[{"kind":"author-affiliation|author-correspondence|author-address|
   affiliation-address|author-note","source_id":"author:1",
   "target_id":"affiliation:1","marker_quote":Q|null}],
 "author_note_quotes":[Q],
 "front_nodes":["doc/p1"],
 "body_start_node":"doc/pN"|null,
 "issues":[]
}
Q means {"quote":"verbatim text","node_hint":"source node",
`left_context`:"verbatim adjacent text or empty",`right_context`:"verbatim adjacent text or empty"}.

One Q always points to one contiguous substring inside ONE underlying Word source node. A
logical title, correspondence block, address, abstract, or other entity may span several
physical nodes; represent it with several Q items in source order. Never concatenate records
with `\n` into one Q and never assign such a synthetic string to the first record's node_hint.
When a mechanically created input window contains no front matter, return `article_type:null`,
all front entities, relations, and source-node arrays empty, nullable fields null, dates with
`format:"unknown"` and empty items, and `issues:[]`. Absence of front matter from one window is
normal and is not an uncertainty.

`author_quote` is the smallest contiguous byline excerpt containing that one printed author and
any immediately adjacent printed relationship markers; it must not include a neighboring
author. A logical author may also have degrees, ORCID identifiers, emails, addresses, and
correspondence text printed elsewhere. Their own Q pointers are independent source occurrences
and do not have to lie inside `author_quote`. Each degree quote is one complete printed
credential string: keep `M.D., Ph.D.` together. Each `keyword_quote` is exactly one keyword and must
exclude the Keywords label and separators. Address `line_quotes` include their printed
affiliation marker (such as superscript `1`) and the street/building/institution address text,
but exclude parenthetical Postal code, Tel, and email labels whose values go into their
dedicated quotes.
`postal_label_quote` and `phone_label_quote` point to the exact printed labels that give their
values meaning; do not include surrounding address content. Omit them only when no such label
is printed. `orcid_quote` is exactly the complete printed identifier value: a hyphenated iD, a
contiguous 16-character iD printed without hyphens, or its http/https ORCID URI. Exclude an
`ORCID:` label, surrounding whitespace, and sentence punctuation. Do not add hyphens or a URI
prefix yourself; the deterministic projection layer validates the checksum and performs that
standards-based normalization.

Every author, affiliation, physical address occurrence, correspondence statement, and
contributor note has one response-local `entity_id`. IDs are protocol handles, not manuscript
text and not final XML IDs. They must be unique in this response. Put every semantic connection
in the single top-level `relations` array. `source_id` and `target_id` copy entity IDs exactly;
they never contain a printed number, name, or symbol. The five relation kinds have these endpoint
types: author→affiliation, author→correspondence, author→address,
affiliation→address, and author→note.

`marker_quote` belongs to the relation, not to either entity's identity. Return it only when a
marker is visibly printed inside that source author's `author_quote`; preserve the exact
character, including a Unicode superscript, and never rewrite ² as 2. A number or symbol printed
on an affiliation line, address line, correspondence block, or note paragraph is NOT an
author-side relation marker and must not be returned here. In particular, `affiliation-address`
always has a null `marker_quote`; an `author-address` relation also has null unless its marker is
actually inside the author's `author_quote`. A semantic relation may legitimately have no
printed marker, so `marker_quote` may be null. Each `correspondences` item is one logical
statement and may contain several source excerpts when it spans physical paragraphs. Each
author's `email_quotes` contains every email that the manuscript explicitly associates with
that author; it is a list even when there is only one.

Dates: determine one document-level convention from all dates. If components contradict the
convention, report it instead of silently swapping. Return a date item only when the visible
value contains an actual decimal calendar year. A workflow status or placeholder without a
year is not a date: omit that item rather than copying the status into `year_quote`. Addresses
belong to affiliation lines
unless the source explicitly associates them with persons. Preserve all street, building,
postal and telephone text. Each `addresses` item represents ONE PHYSICAL PRINTED OCCURRENCE,
identified by source_nodes. Do not merge identical address text printed in different places;
return separate entities and connect each occurrence to its semantic owner through `relations`.
A correspondence
paragraph which happens to contain an institution or postal address is not thereby an address
owned by the author. Use `author-address` only when the manuscript presents that physical
occurrence as the author's own contact address; use `affiliation-address` for an ordinary
affiliation address. Do not add both relations merely because a personal contact address repeats
an institution name or begins with a printed affiliation number; classify the ownership of that
physical occurrence from how the manuscript presents it.

The following invented contrast defines identity versus printing; it is not a manuscript
template. If one author is followed by one unlabelled affiliation, return an
`author-affiliation` relation whose `marker_quote` is null and an affiliation whose
`label_quote` is null. Returning no relation merely because no number is printed is wrong.
Likewise, two authors connected to one correspondence block require two explicit
`author-correspondence` relations to the same correspondence entity; never guess ownership
later from the number of correspondence blocks.

The following invented few-shot examples show only the relevant response fragments. They do
not define wording, numbering, or layout patterns.

Example 1 -- a real relation without any printed marker
Source:
[doc/p2] Mira Sol
[doc/p3] Center for Coastal Research
Correct response fragment (unrelated top-level fields are not shown):
{"authors":[{"entity_id":"author:1","author_quote":{"quote":"Mira Sol","node_hint":"doc/p2","left_context":"","right_context":""},"surname_quote":{"quote":"Sol","node_hint":"doc/p2","left_context":"","right_context":""},"given_quote":{"quote":"Mira","node_hint":"doc/p2","left_context":"","right_context":""},"suffix_quote":null,"degree_quotes":[],"email_quotes":[],"orcid_quote":null,"author_comment_quotes":[]}],"affiliations":[{"entity_id":"affiliation:1","label_quote":null,"content_quotes":[{"quote":"Center for Coastal Research","node_hint":"doc/p3","left_context":"","right_context":""}]}],"relations":[{"kind":"author-affiliation","source_id":"author:1","target_id":"affiliation:1","marker_quote":null}]}
Incorrect: omitting the relation because neither line prints a number.

Example 2 -- printed symbols are evidence on relations, not entity IDs
Source:
[doc/p5] Arun Vale*, Lian Park*
[doc/p8] * Contacts: Arun Vale <arun@example.org>; Lian Park <lian@example.org>
Correct response fragment (unrelated top-level fields are not shown):
{"authors":[{"entity_id":"author:1","author_quote":{"quote":"Arun Vale*","node_hint":"doc/p5","left_context":"","right_context":""},"surname_quote":{"quote":"Vale","node_hint":"doc/p5","left_context":"","right_context":""},"given_quote":{"quote":"Arun","node_hint":"doc/p5","left_context":"","right_context":""},"suffix_quote":null,"degree_quotes":[],"email_quotes":[{"quote":"arun@example.org","node_hint":"doc/p8","left_context":"Arun Vale <","right_context":">;"}],"orcid_quote":null,"author_comment_quotes":[]},{"entity_id":"author:2","author_quote":{"quote":"Lian Park*","node_hint":"doc/p5","left_context":"","right_context":""},"surname_quote":{"quote":"Park","node_hint":"doc/p5","left_context":"","right_context":""},"given_quote":{"quote":"Lian","node_hint":"doc/p5","left_context":"","right_context":""},"suffix_quote":null,"degree_quotes":[],"email_quotes":[{"quote":"lian@example.org","node_hint":"doc/p8","left_context":"Lian Park <","right_context":">"}],"orcid_quote":null,"author_comment_quotes":[]}],"correspondences":[{"entity_id":"correspondence:1","content_quotes":[{"quote":"* Contacts: Arun Vale <arun@example.org>; Lian Park <lian@example.org>","node_hint":"doc/p8","left_context":"","right_context":""}]}],"relations":[{"kind":"author-correspondence","source_id":"author:1","target_id":"correspondence:1","marker_quote":{"quote":"*","node_hint":"doc/p5","left_context":"Arun Vale","right_context":", Lian"}},{"kind":"author-correspondence","source_id":"author:2","target_id":"correspondence:1","marker_quote":{"quote":"*","node_hint":"doc/p5","left_context":"Lian Park","right_context":""}}]}
Incorrect: using `*` as a target ID, or returning one relation and asking the program to infer
the other because there is only one correspondence block.

Example 3 -- a marker printed on an address is not an author-side marker
Source:
[doc/p4] Inez Toro
[doc/p9] Mailing address for Inez Toro: 4 River Building, North Campus
Correct response fragment (unrelated top-level fields are not shown):
{"authors":[{"entity_id":"author:1","author_quote":{"quote":"Inez Toro","node_hint":"doc/p4","left_context":"","right_context":""},"surname_quote":{"quote":"Toro","node_hint":"doc/p4","left_context":"","right_context":""},"given_quote":{"quote":"Inez","node_hint":"doc/p4","left_context":"","right_context":""},"suffix_quote":null,"degree_quotes":[],"email_quotes":[],"orcid_quote":null,"author_comment_quotes":[]}],"addresses":[{"entity_id":"address:1","source_nodes":["doc/p9"],"line_quotes":[{"quote":"4 River Building, North Campus","node_hint":"doc/p9","left_context":"Mailing address for Inez Toro: ","right_context":""}],"postal_label_quote":null,"postal_quote":null,"phone_label_quote":null,"phone_quote":null}],"relations":[{"kind":"author-address","source_id":"author:1","target_id":"address:1","marker_quote":null}]}
Incorrect: copying the address-side `4` into the relation's `marker_quote`.

Example 4 -- one logical correspondence entity spans several physical source nodes
Source:
[doc/p20] Contact for Luma Grey:
[doc/p21] Room 8, Cedar Research House
[doc/p22] Email: luma@example.org
Correct response fragment (unrelated top-level fields are not shown):
{"correspondences":[{"entity_id":"correspondence:1","content_quotes":[{"quote":"Contact for Luma Grey:","node_hint":"doc/p20","left_context":"","right_context":""},{"quote":"Room 8, Cedar Research House","node_hint":"doc/p21","left_context":"","right_context":""},{"quote":"Email: luma@example.org","node_hint":"doc/p22","left_context":"","right_context":""}]}]}
Incorrect: one Q whose quote is the three lines joined by `\n` and whose node_hint is `doc/p20`.

Example 5 -- several abstract subsections share one physical source node
Source:
[doc/p30] Summary
[doc/p31] Rationale: Coastal sensors drift over time. Procedure: We compared two calibration methods. Interpretation: The second method was more stable.
Correct response fragment (unrelated top-level fields are not shown):
{"abstracts":[{"kind":"main","source_nodes":["doc/p30","doc/p31"],"container_title_quote":{"quote":"Summary","node_hint":"doc/p30","left_context":"","right_context":""},"sections":[{"title_quote":{"quote":"Rationale:","node_hint":"doc/p31","left_context":"","right_context":" Coastal"},"paragraph_quotes":[{"quote":"Coastal sensors drift over time.","node_hint":"doc/p31","left_context":"Rationale: ","right_context":" Procedure:"}],"wrapped":true},{"title_quote":{"quote":"Procedure:","node_hint":"doc/p31","left_context":"time. ","right_context":" We compared"},"paragraph_quotes":[{"quote":"We compared two calibration methods.","node_hint":"doc/p31","left_context":"Procedure: ","right_context":" Interpretation:"}],"wrapped":true},{"title_quote":{"quote":"Interpretation:","node_hint":"doc/p31","left_context":"methods. ","right_context":" The second"},"paragraph_quotes":[{"quote":"The second method was more stable.","node_hint":"doc/p31","left_context":"Interpretation: ","right_context":""}],"wrapped":true}],"graphics":[]}]}
Incorrect: returning one section whose first title is `Rationale:` and whose paragraph list also
contains `Rationale:` or the later `Procedure:` and `Interpretation:` subsections. Physical Word
paragraph boundaries do not determine logical abstract subsection boundaries.

An abstract's
`container_title_quote` is a printed heading which names the abstract container as a whole; it
is consumed by the semantic `<abstract>` role and is not an abstract subsection. An abstract
section title and its paragraph may share a node; abstract/keyword source_nodes must list every
source node consumed by that container, including any container heading.
Each logical abstract subsection must be one separate object in `sections`. Its `title_quote`
contains only that subsection's printed title, and its `paragraph_quotes` contain only the text
governed by that title; these source spans must not overlap. Never put text governed by a later
printed subsection title into an earlier section object. This remains true when all subsection
titles and text are printed in one Word paragraph.
Set an abstract section's `wrapped` to true only when the source presents a real subsection
with a printed title that you return in `title_quote`. An unstructured abstract whose paragraph
has no subsection title must use `wrapped:false`, because JATS sec requires a title. Never use
the heading of the abstract container itself as the title of its first subsection. A meaningful
title printed inside an abstract, rather than merely naming the container, may be returned as
an unwrapped section title.
`contributor_notes` represents shared notes referenced by one or more authors. Its
`marker_quote` points to the marker printed with the NOTE itself; each author-side occurrence is
the `marker_quote` of a separate `author-note` relation. The note paragraph quote includes the
complete visible note paragraph, including its printed marker. Return a contributor note only
when a non-empty printed note paragraph exists. A correspondence asterisk without a shared note paragraph is not an equal-
contributor note. `correspondences[].content_quotes` covers source-printed information about
how or with whom to correspond: it may give contact channels, identify corresponding
contributors, or do both. An author-side marker by itself does not state either fact. If the
source contains no correspondence statement, return no correspondence entity and no
author-correspondence relation; never complete a missing statement from a few-shot example or
from publishing convention.
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


HEAD_BOUNDARY_SYSTEM = r"""You locate the end of the bibliographic and contributor header at
the beginning of an English scholarly manuscript. Do not extract or interpret its fields.

The user supplies the first input window beginning at the first Word body record. Each line
has an address and a JSON array of consecutive Word text segments. Concatenating the `text`
values gives the exact visible record. The header is one contiguous region beginning at the
start of the manuscript. It contains the printed article type/category, title, byline,
affiliations, contributor addresses and contacts, manuscript-history dates, editor lines, and
front-matter contributor notes. Abstracts, graphical abstracts, precis, keywords, body
sections, author-contribution declarations, acknowledgments, funding, conflicts, ethics, data
statements, AI statements, and references are outside this region.

Return one strict JSON object and no prose. `last_head_node` is the address of the last non-empty
record that still belongs to that header. `first_outside_head_node` is the address of the first
later non-empty record outside it. Copy addresses exactly from the visible input; do not repeat
the record text. Empty separator records do not serve as either boundary. If no bibliographic/
contributor header is visible, use null for `last_head_node`. If the header genuinely reaches
the end of the supplied input, use null for `first_outside_head_node`.
Never return a record merely because it contains an author name later in the manuscript.

Invented example:
[doc/p1] [{"text":"Research Article","styles":[]}]
[doc/p2] [{"text":"A study of tidal marshes","styles":[]}]
[doc/p3] [{"text":"Lina Hart","styles":[]}]
[doc/p4] [{"text":"Coastal Institute","styles":[]}]
[doc/p5] [{"text":"","styles":[]}]
[doc/p6] [{"text":"Abstract","styles":["bold"]}]
[doc/p7] [{"text":"Marshes were surveyed ...","styles":[]}]
Correct: {"last_head_node":"doc/p4","first_outside_head_node":"doc/p6","issues":[]}.
Incorrect: selecting doc/p7 as part of the header because it mentions the paper's subject.
"""


HEAD_METADATA_SYSTEM = r"""You identify only the bibliographic and contributor metadata printed
in the front matter of an English scholarly manuscript.

INPUT
The user supplies only the first input window, beginning at the first Word body record. Each
line has an address and a JSON array of consecutive Word text segments:
  [doc/p2] [{"text":"Mira Sol","styles":[]},{"text":"a,*","styles":["superscript"]}]
Concatenating every `text` value on a line gives the exact visible source record. `styles` are
facts read from Word, not manuscript characters. They may distinguish a name from immediately
adjacent affiliation or correspondence markers. Table rows use their own displayed addresses.

TASK BOUNDARY
Return article type, category, article title, authors, affiliations, physical addresses,
correspondence statements, manuscript-history dates, editors, and notes physically belonging
to the front matter. Do not return abstracts, graphical abstracts, precis, keywords, body
sections, author-contribution declarations, acknowledgments, funding, conflicts, ethics, data
statements, AI statements, references, or a body boundary. Later text may be visible because a
short manuscript fits in the first window; that does not make body/back material front matter.

OUTPUT AND SOURCE DISCIPLINE
Return one strict JSON object and no prose. Never correct, expand, translate, or invent printed
metadata. Unknown or absent values are null/[]; never infer publication metadata from outside
the supplied source. A source pointer is {"node":"doc/pN","quote":"..."}. Its quote is one
exact contiguous substring of the addressed record after concatenating that record's `text`
segments; JSON syntax and style names are not part of the quote. A logical item spanning several
records uses several source pointers in source order. Do not supply left/right context.

OUTPUT SHAPE
Use exactly the following field names and nesting, in addition to the submitted JSON Schema.
Every listed key is required even when its value is null or an empty array. `P` below means the
source-pointer object defined above; never output the bare letter P.
{
 "article_type":"research-article|review-article|case-report|editorial|other"|null,
 "category":P|null,
 "title":[P],
 "authors":[{
   "source":P,"given_names":"...","surname":"...","suffix":"..."|null,
   "degrees":[P],"emails":[P],"orcid":P|null,"comments":[P],
   "affiliation_links":[{"target":1,"marker":"..."|null}],
   "address_links":[{"target":1,"marker":"..."|null}],
   "correspondence_links":[{"target":1,"marker":"..."|null}],
   "note_links":[{"target":1,"marker":"..."|null}]
 }],
 "affiliations":[{"label":P|null,"content":[P],"address_indexes":[1]}],
 "addresses":[{"lines":[P],"postal_code":P|null,"phone":P|null}],
 "correspondences":[{"content":[P]}],
 "dates":[{"kind":"received|revised|accepted","source":P,
            "year":"...","month":"..."|null,"day":"..."|null}],
 "editors":[{"source":P,"given_names":"...","surname":"...","role":P|null}],
 "contributor_notes":[{"kind":"equal|other","label":P|null,"content":[P]}],
 "author_notes":[P],
 "issues":["..."]
}
Do not use alternate keys such as `name`, `line`, `manuscript_history`, or `notes`. Emit the
object once, without Markdown fences, and stop after its closing brace.

The `source` of one author covers only that author's printed byline occurrence, including any
adjacent printed relationship markers. `given_names`, `surname`, and `suffix` are exact substrings
inside that source; markers are not part of a name. Relationships are embedded on each author:
each link contains a one-based target index and the exact author-side printed marker, or null
when the relationship is real but unmarked. Do not create relationships merely from matching
numbers or cardinality; decide them from the complete front-matter presentation.

Addresses represent physical printed address occurrences. An address inside a correspondence
block is not automatically an author-owned address. Affiliations list their own address indexes;
authors list only addresses the manuscript presents as belonging to that person. A
correspondence item is one printed statement that identifies corresponding contributors,
provides a contact route, or both. A bare star is relationship evidence, not a correspondence
statement. General responsibility prose printed in the front belongs in `author_notes`, not in
correspondence content.

Return a date only when an actual decimal year is printed. `year`, `month`, and `day` are exact
substrings inside `source.quote`; use null for a component not printed. Do not treat a workflow
placeholder as a date. A shared contributor note is a printed front-matter note referenced by
one or more authors; connect it through each author's `note_links`. Do not treat a later Author
contributions section as such a note.

Invented example 1 — formatted markers are not part of a name:
Source:
[doc/p2] [{"text":"Mira Sol","styles":[]},{"text":"a,*","styles":["superscript"]}]
[doc/p3] [{"text":"a Coastal Research Center","styles":[]}]
[doc/p4] [{"text":"* Correspondence: mira@example.org","styles":[]}]
Correct fragment:
{"authors":[{"source":{"node":"doc/p2","quote":"Mira Sola,*"},"given_names":"Mira","surname":"Sol","suffix":null,"degrees":[],"emails":[{"node":"doc/p4","quote":"mira@example.org"}],"orcid":null,"comments":[],"affiliation_links":[{"target":1,"marker":"a"}],"address_links":[],"correspondence_links":[{"target":1,"marker":"*"}],"note_links":[]}],"affiliations":[{"label":{"node":"doc/p3","quote":"a"},"content":[{"node":"doc/p3","quote":"Coastal Research Center"}],"address_indexes":[]}],"correspondences":[{"content":[{"node":"doc/p4","quote":"* Correspondence: mira@example.org"}]}]}
Incorrect: surname `Sola`, or using `a`/`*` as target identities.

Invented example 2 — an unmarked relationship is still explicit:
Source:
[doc/p6] [{"text":"Ivo Reed","styles":[]}]
[doc/p7] [{"text":"Laboratory of Open Systems","styles":[]}]
Correct fragment: the author has affiliation link {"target":1,"marker":null}, and the
affiliation has a null label. Incorrect: omitting the relationship only because no number is
printed.

Invented negative example — later declarations are outside this task:
Source:
[doc/p20] [{"text":"Introduction","styles":[]}]
[doc/p80] [{"text":"Author contributions","styles":["bold"]}]
[doc/p81] [{"text":"Mira designed the study.","styles":[]}]
Correct: neither line is returned as a contributor note or author note.
"""


def _strict_object(**properties):
    """生成供模型服务端和本地共用的封闭对象模式。"""
    return {
        "type": "object",
        "properties": properties,
        "required": list(properties),
        "additionalProperties": False,
    }


def _array(items):
    return {"type": "array", "items": items}


def _nullable(value):
    return {"anyOf": [value, {"type": "null"}]}


_HEAD_SOURCE = _strict_object(
    node={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
    quote={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
)

HEAD_BOUNDARY_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manuscript_head_boundary",
        "strict": True,
        "schema": _strict_object(
            last_head_node=_nullable({
                "type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$",
            }),
            first_outside_head_node=_nullable({
                "type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$",
            }),
            issues=_array({"type": "string"}),
        ),
    },
}

_HEAD_LINK = _strict_object(
    target={"type": "integer", "minimum": 1},
    marker=_nullable({"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"}),
)

_HEAD_AUTHOR = _strict_object(
    source=_HEAD_SOURCE,
    given_names={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
    surname={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
    suffix=_nullable({"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"}),
    degrees=_array(_HEAD_SOURCE),
    emails=_array(_HEAD_SOURCE),
    orcid=_nullable(_HEAD_SOURCE),
    comments=_array(_HEAD_SOURCE),
    affiliation_links=_array(_HEAD_LINK),
    address_links=_array(_HEAD_LINK),
    correspondence_links=_array(_HEAD_LINK),
    note_links=_array(_HEAD_LINK),
)

_HEAD_AFFILIATION = _strict_object(
    label=_nullable(_HEAD_SOURCE),
    content=_array(_HEAD_SOURCE),
    address_indexes=_array({"type": "integer", "minimum": 1}),
)

_HEAD_ADDRESS = _strict_object(
    lines=_array(_HEAD_SOURCE),
    postal_code=_nullable(_HEAD_SOURCE),
    phone=_nullable(_HEAD_SOURCE),
)

_HEAD_CORRESPONDENCE = _strict_object(content=_array(_HEAD_SOURCE))

_HEAD_DATE = _strict_object(
    kind={"type": "string", "enum": ["received", "revised", "accepted"]},
    source=_HEAD_SOURCE,
    year={"type": "string", "minLength": 1, "pattern": "^[0-9]+$"},
    month=_nullable({"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"}),
    day=_nullable({"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"}),
)

_HEAD_EDITOR = _strict_object(
    source=_HEAD_SOURCE,
    given_names={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
    surname={"type": "string", "minLength": 1, "pattern": "^[^\\r\\n]+$"},
    role=_nullable(_HEAD_SOURCE),
)

_HEAD_NOTE = _strict_object(
    kind={"type": "string", "enum": ["equal", "other"]},
    label=_nullable(_HEAD_SOURCE),
    content=_array(_HEAD_SOURCE),
)

HEAD_METADATA_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manuscript_head_metadata",
        "strict": True,
        "schema": _strict_object(
            article_type=_nullable({
                "type": "string", "enum": [
                    "research-article", "review-article", "case-report",
                    "editorial", "other",
                ],
            }),
            category=_nullable(_HEAD_SOURCE),
            title=_array(_HEAD_SOURCE),
            authors=_array(_HEAD_AUTHOR),
            affiliations=_array(_HEAD_AFFILIATION),
            addresses=_array(_HEAD_ADDRESS),
            correspondences=_array(_HEAD_CORRESPONDENCE),
            dates=_array(_HEAD_DATE),
            editors=_array(_HEAD_EDITOR),
            contributor_notes=_array(_HEAD_NOTE),
            author_notes=_array(_HEAD_SOURCE),
            issues=_array({"type": "string"}),
        ),
    },
}


_FRONT_Q = _strict_object(
    quote={
        "type": "string",
        "minLength": 1,
        "pattern": "^[^\\r\\n]+$",
        "description": (
            "Exact contiguous source characters from one displayed record; never join "
            "several records with a newline"
        ),
    },
    node_hint={
        "type": "string",
        "minLength": 1,
        "pattern": "^[^\\r\\n]+$",
        "description": "Exact displayed address of the one source node containing quote",
    },
    left_context={
        "type": "string",
        "pattern": "^[^\\r\\n]*$",
        "description": (
            "Exact immediately adjacent characters in the same source node, long enough "
            "with quote and right_context to identify one occurrence"
        ),
    },
    right_context={
        "type": "string",
        "pattern": "^[^\\r\\n]*$",
        "description": (
            "Exact immediately adjacent characters in the same source node, long enough "
            "with left_context and quote to identify one occurrence"
        ),
    },
)

_FRONT_AUTHOR = _strict_object(
    entity_id={"type": "string"},
    author_quote=_FRONT_Q,
    surname_quote=_FRONT_Q,
    given_quote=_FRONT_Q,
    suffix_quote=_nullable(_FRONT_Q),
    degree_quotes=_array(_FRONT_Q),
    email_quotes=_array(_FRONT_Q),
    orcid_quote=_nullable(_FRONT_Q),
    author_comment_quotes=_array(_FRONT_Q),
)

_FRONT_AFFILIATION = _strict_object(
    entity_id={"type": "string"},
    label_quote=_nullable(_FRONT_Q),
    content_quotes=_array(_FRONT_Q),
)

_FRONT_ADDRESS = _strict_object(
    entity_id={"type": "string"},
    source_nodes=_array({"type": "string"}),
    line_quotes=_array(_FRONT_Q),
    postal_label_quote=_nullable(_FRONT_Q),
    postal_quote=_nullable(_FRONT_Q),
    phone_label_quote=_nullable(_FRONT_Q),
    phone_quote=_nullable(_FRONT_Q),
)

_FRONT_CORRESPONDENCE = _strict_object(
    entity_id={"type": "string"},
    content_quotes=_array(_FRONT_Q),
)

_FRONT_DATE_ITEM = _strict_object(
    kind={"type": "string", "enum": ["received", "revised", "accepted"]},
    whole_quote=_FRONT_Q,
    year_quote=_FRONT_Q,
    month_quote=_nullable(_FRONT_Q),
    day_quote=_nullable(_FRONT_Q),
)

_FRONT_EDITOR = _strict_object(
    surname_quote=_FRONT_Q,
    given_quote=_FRONT_Q,
    role_quote=_nullable(_FRONT_Q),
)

_FRONT_ABSTRACT_SECTION = _strict_object(
    title_quote=_nullable(_FRONT_Q),
    paragraph_quotes=_array(_FRONT_Q),
    wrapped={"type": "boolean"},
)

_FRONT_ABSTRACT = _strict_object(
    kind={"type": "string", "enum": ["main", "graphical", "precis"]},
    source_nodes=_array({"type": "string"}),
    container_title_quote=_nullable(_FRONT_Q),
    sections=_array(_FRONT_ABSTRACT_SECTION),
    graphics=_array({"type": "string"}),
)

_FRONT_KEYWORDS = _strict_object(
    source_nodes=_array({"type": "string"}),
    title_quote=_nullable(_FRONT_Q),
    keyword_quotes=_array(_FRONT_Q),
)

_FRONT_CONTRIBUTOR_NOTE = _strict_object(
    entity_id={"type": "string"},
    marker_quote=_FRONT_Q,
    paragraph_quotes=_array(_FRONT_Q),
    kind={"type": "string", "enum": ["equal", "other"]},
)

_FRONT_RELATION = _strict_object(
    kind={
        "type": "string",
        "enum": [
            "author-affiliation", "author-correspondence", "author-address",
            "affiliation-address", "author-note",
        ],
    },
    source_id={"type": "string"},
    target_id={"type": "string"},
    marker_quote=_nullable(_FRONT_Q),
)

FRONT_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "manuscript_front_matter",
        "strict": True,
        "schema": _strict_object(
            article_type=_nullable({
                "type": "string", "enum": [
                    "research-article", "review-article", "case-report",
                    "editorial", "other",
                ],
            }),
            category_quote=_nullable(_FRONT_Q),
            title_quotes=_array(_FRONT_Q),
            authors=_array(_FRONT_AUTHOR),
            affiliations=_array(_FRONT_AFFILIATION),
            addresses=_array(_FRONT_ADDRESS),
            correspondences=_array(_FRONT_CORRESPONDENCE),
            dates=_strict_object(
                format={"type": "string", "enum": ["dmy", "mdy", "ymd", "unknown"]},
                items=_array(_FRONT_DATE_ITEM),
            ),
            editors=_array(_FRONT_EDITOR),
            abstracts=_array(_FRONT_ABSTRACT),
            keywords=_nullable(_FRONT_KEYWORDS),
            contributor_notes=_array(_FRONT_CONTRIBUTOR_NOTE),
            relations=_array(_FRONT_RELATION),
            author_note_quotes=_array(_FRONT_Q),
            front_nodes=_array({"type": "string"}),
            body_start_node=_nullable({"type": "string"}),
            issues=_array({"type": "string"}),
        ),
    },
}


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
Some paragraph records are followed by a `WORD_FACTS(record_key)` line. It is non-content
metadata read directly from Word: paragraph style identity/name, effective outline level,
numbering, and source-character ranges carrying effective text formatting. Use these
facts together with wording and surrounding structure when deciding block role and section
nesting. The facts line itself is never manuscript text: never quote it or return it as a node.
In its compact facts object, `o` is Word's effective zero-based outline level, `n` is
`[numbering_id, numbering_level]`, `s` is `[style_id, style_name]`, and each `f` item is
`[start, end, "effective_format_name+..."]` over a zero-based half-open source-character range.
Style identifiers and names are producer-defined evidence, not a fixed mapping to JATS levels;
authors may misuse styles, and visual bold/italic alone does not make a section. Conversely, do
not discard explicit Word structure and then guess solely from heading wording. No single fact
overrides the complete document context.
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


_CITATION_QUOTE_SCHEMA = {
    "type": "object",
    "properties": {
        "quote": {
            "type": "string",
            "description": "Exact visible citation characters copied from the manuscript",
        },
        "record_key": {
            "type": "string",
            "description": (
                "Exact address printed before the source record that contains the citation"
            ),
        },
        "left_context": {
            "type": "string",
            "pattern": "^[^\\r\\n]*$",
            "description": (
                "Exact visible characters immediately before quote in the same source record"
            ),
        },
        "right_context": {
            "type": "string",
            "pattern": "^[^\\r\\n]*$",
            "description": (
                "Exact visible characters immediately after quote in the same source record"
            ),
        },
    },
    "required": ["quote", "record_key", "left_context", "right_context"],
    "additionalProperties": False,
}


CITATION_RESPONSE_FORMAT = {
    "type": "json_schema",
    "json_schema": {
        "name": "bibliographic_citation_links",
        "strict": True,
        "schema": {
            "type": "object",
            "properties": {
                "compact_range_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_quote": _CITATION_QUOTE_SCHEMA,
                            "target_reference_ids": {
                                "type": "array",
                                "items": {"type": "string"},
                                "description": (
                                    "Ordered stable entity IDs represented by one compact "
                                    "range, including targets without separate visible text"
                                ),
                            },
                        },
                        "required": ["citation_quote", "target_reference_ids"],
                        "additionalProperties": False,
                    },
                },
                "single_target_citations": {
                    "type": "array",
                    "items": {
                        "type": "object",
                        "properties": {
                            "citation_quote": _CITATION_QUOTE_SCHEMA,
                            "target_reference_id": {
                                "type": "string",
                                "description": (
                                    "One stable entity ID copied from REFERENCE IDENTITIES"
                                ),
                            },
                        },
                        "required": ["citation_quote", "target_reference_id"],
                        "additionalProperties": False,
                    },
                },
                "issues": {
                    "type": "array",
                    "items": {"type": "string"},
                },
            },
            "required": [
                "compact_range_citations", "single_target_citations", "issues",
            ],
            "additionalProperties": False,
        },
    },
}


CITATION_SYSTEM = PREAMBLE + r"""
TASK: identify every visible bibliographic citation outside the bibliography and link each one
to the supplied reference identities. Citations may occur in narrative prose, native Word table
rows, tab-separated ordinary paragraphs, captions, notes, or other manuscript records; all have
the same status in this task. "In-text" does not mean "narrative prose only". The user message
contains an addressable manuscript window. A separate
REFERENCE IDENTITIES section gives each stable entity ID its printed label, author surnames,
year, year suffix, and title evidence, all copied from the already delimited bibliography.

Return exactly this concrete JSON structure:
{
 "compact_range_citations":[{
   "citation_quote":{
     "quote":"one compact visible range",
     "record_key":"doc/pN",
     "left_context":"exact adjacent text before it",
     "right_context":"exact adjacent text after it"
   },
   "target_reference_ids":["reference:1","reference:2","reference:3"]
 }],
 "single_target_citations":[{
   "citation_quote":{
     "quote":"exact visible citation unit",
     "record_key":"doc/pN",
     "left_context":"exact adjacent text before it",
     "right_context":"exact adjacent text after it"
   },
   "target_reference_id":"reference:1"
 }],
 "issues":[]
}

`citation_quote` is always the four-field object shown above. Never return a bare string.
`record_key` is the exact address printed at the start of the source record. `quote` is the
visible source text that will be wrapped in one JATS xref. `left_context` and `right_context`
are locating evidence only; they do not enter the xref. Copy them from the characters which
immediately touch the left and right sides of `quote` in that same record. Copy enough adjacent
text to make `left_context + quote + right_context` occur exactly once in the addressed record.
A context may be empty at a record edge, but do not leave both contexts empty when the quote is
repeated. Do not include the printed `[record_key]` address in a context.

Work in this order: first identify and write every `compact_range_citations` item; only then
write `single_target_citations` outside those already occupied source spans.

Use `single_target_citations` whenever one visible source substring points to one reference.
This includes every independently visible member of a comma/semicolon list, every ordinary
author-year citation, and both endpoints of a two-reference range. Its target field is the
singular string `target_reference_id`, so never put a list there.

Use `compact_range_citations` only when one compact visible range represents several targets
and at least one target has no separate visible characters. Its plural `target_reference_ids`
lists all targets in source order. Never use this array merely to combine visible list members.
The two arrays are mutually exclusive at the source-character level: after putting a range in
`compact_range_citations`, do NOT also put its visible endpoints or any other substring of that
same range in `single_target_citations`. Never invent characters for an implied target. Shared
grouping punctuation remains ordinary source text.

The examples below are invented only to demonstrate the response contract. They are not
patterns for deciding what counts as a citation.

Example 1
Source: [doc/p12] Several earlier trials reached the same conclusion [2,5].
Available targets: reference:2 has printed label [2]; reference:5 has printed label [5].
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"2","record_key":"doc/p12","left_context":"conclusion [","right_context":",5]"},"target_reference_id":"reference:2"},{"citation_quote":{"quote":"5","record_key":"doc/p12","left_context":"[2,","right_context":"]."},"target_reference_id":"reference:5"}],"issues":[]}
The brackets and comma remain ordinary source text. Each visible label gets its own item.
Whitespace around a separator changes only the exact copied contexts, never whether the visible
labels are separate items.

Example 2
Source: [doc/p27] The findings differ (Rivera and Chen, 2021; Okafor, 2023).
Available targets: reference:8 has surnames Rivera and Chen and year 2021;
reference:9 has surname Okafor and year 2023.
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Rivera and Chen, 2021","record_key":"doc/p27","left_context":"differ (","right_context":"; Okafor"},"target_reference_id":"reference:8"},{"citation_quote":{"quote":"Okafor, 2023","record_key":"doc/p27","left_context":"2021; ","right_context":")."},"target_reference_id":"reference:9"}],"issues":[]}
The shared parentheses and semicolon remain ordinary source text. Do not wrap both works in
one citation_quote when each work has its own contiguous visible identifying text.

Example 3
Source: [doc/p40] Reed (2022) reported the first result; Reed (2022) later revised it.
Available target: reference:4 has surname Reed and year 2022.
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"","right_context":" reported"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"result; ","right_context":" later"},"target_reference_id":"reference:4"}],"issues":[]}
The same quote is located twice without counting occurrences: its adjacent source text identifies
which physical occurrence is meant.

Example 4
Source: [doc/p55] The combined evidence supports this conclusion [1-3, 7, 9-10].
Available targets have printed labels 1 through 3, 7, 9, and 10.
Correct JSON:
{"compact_range_citations":[{"citation_quote":{"quote":"1-3","record_key":"doc/p55","left_context":"conclusion [","right_context":", 7"},"target_reference_ids":["reference:1","reference:2","reference:3"]}],"single_target_citations":[{"citation_quote":{"quote":"7","record_key":"doc/p55","left_context":"1-3, ","right_context":", 9-10"},"target_reference_id":"reference:7"},{"citation_quote":{"quote":"9","record_key":"doc/p55","left_context":"7, ","right_context":"-10]"},"target_reference_id":"reference:9"},{"citation_quote":{"quote":"10","record_key":"doc/p55","left_context":"9-","right_context":"]."},"target_reference_id":"reference:10"}],"issues":[]}
`1-3` stays one unit because reference:2 has no separate printed characters. `9-10` splits into
two units because both targets have their own non-overlapping visible labels. Brackets, commas,
and the hyphen between separately wrapped 9 and 10 remain ordinary source text. In particular,
do NOT add separate single-target items for `1` or `3`; they are already inside the returned
`1-3` compact-range item.

Example 5
Source: [doc/tbl2.r3] Cohort A ⇥ Improved after treatment [4, 6]
Available targets have printed labels 4 and 6.
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"4","record_key":"doc/tbl2.r3","left_context":"treatment [","right_context":", 6]"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"6","record_key":"doc/tbl2.r3","left_context":"[4, ","right_context":"]"},"target_reference_id":"reference:6"}],"issues":[]}
Table-row addresses follow exactly the same source-location contract as paragraph addresses.
The following answer is incorrect for the same source:
{"compact_range_citations":[],"single_target_citations":[],"issues":["Skipped because the citations occur in a non-narrative table row."]}
Location in a table, comparison matrix, study-summary column, caption, or note is never by
itself a reason to exclude a visible bibliographic citation.

Example 6
Source: [doc/p83] Catalyst A ⇥ Stable ⇥ Earlier work [11,13]
Available targets have printed labels 11 and 13.
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"11","record_key":"doc/p83","left_context":"Earlier work [","right_context":",13]"},"target_reference_id":"reference:11"},{"citation_quote":{"quote":"13","record_key":"doc/p83","left_context":"[11,","right_context":"]"},"target_reference_id":"reference:13"}],"issues":[]}
An ordinary paragraph may use tabs to present a visual table row. Its `doc/pN` address does not
change the citation decision: inspect its visible content exactly as you inspect every other
record. Do not first require the program or source format to label it as a table.

Example 7
Source records:
[doc/p70] The first paragraph ends with supporting evidence [8].
[doc/p71] A new paragraph begins here.
Available target: reference:8 has printed label [8].
Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"]."},"target_reference_id":"reference:8"}],"issues":[]}
The right_context stops at the end of doc/p70. Never cross a newline into doc/p71, and never
omit an intervening printed record address to make text from two records appear adjacent.
Both examples below are incorrect:
{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\n[doc/p71] A new paragraph"},"target_reference_id":"reference:8"}
{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\nA new paragraph"},"target_reference_id":"reference:8"}
The first crosses the record boundary and copies an address. The second still crosses the
record boundary after hiding the address. Text from doc/p71 can never locate text in doc/p70.

Audit every supplied source record, including every `doc/tblN.rM` row and every `doc/pN`
paragraph containing tab separators. Do not silently skip a record class. Match numbered
citations to printed reference labels and author-year citations to surname +
year + suffix identity. Narrative and parenthetical citations use the same representation.
Use title and nearby semantic context only to resolve target identities, never to manufacture
visible text. Do not assume one punctuation, capitalization, numbering, or author-name style.
Do not return bibliography entries as citations. If either the exact source span or its unique
target set is uncertain, omit that relation and report the uncertainty instead of guessing.
Before returning, reread the source records from beginning to end and audit coverage: every
visible in-text bibliographic citation you can uniquely resolve must occur exactly once across
the two arrays. Do not skip a citation merely because its target appears nowhere else, and do
not duplicate a source span in both arrays.
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
