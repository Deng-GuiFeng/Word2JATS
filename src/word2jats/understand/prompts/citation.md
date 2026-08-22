
TASK: identify every visible bibliographic citation outside the bibliography and link each one to the supplied reference identities. Citations may occur in narrative prose, native Word table rows, tab-separated ordinary paragraphs, captions, notes, or other manuscript records; all have the same status in this task. "In-text" does not mean "narrative prose only". The user message contains an addressable manuscript window. A separate REFERENCE IDENTITIES section gives each stable entity ID its printed label, author surnames, year, year suffix, and title evidence, all copied from the already delimited bibliography.

Return exactly this concrete JSON structure:
{"compact_range_citations":[{"citation_quote":{"quote":"one compact visible range","record_key":"doc/pN","left_context":"exact adjacent text before it","right_context":"exact adjacent text after it"},"target_reference_ids":["reference:1","reference:2","reference:3"]}],"single_target_citations":[{"citation_quote":{"quote":"exact visible citation unit","record_key":"doc/pN","left_context":"exact adjacent text before it","right_context":"exact adjacent text after it"},"target_reference_id":"reference:1"}],"issues":[]}

`citation_quote` is always the four-field object shown above. Never return a bare string. `record_key` is the exact address printed at the start of the source record. `quote` is the visible source text that will be wrapped in one JATS xref. `left_context` and `right_context` are locating evidence only; they do not enter the xref. Copy them from the characters which immediately touch the left and right sides of `quote` in that same record. Copy enough adjacent text to make `left_context + quote + right_context` occur exactly once in the addressed record. A context may be empty at a record edge, but do not leave both contexts empty when the quote is repeated. Do not include the printed `[record_key]` address in a context.

Work in this order: first identify and write every `compact_range_citations` item; only then write `single_target_citations` outside those already occupied source spans.

Use `single_target_citations` whenever one visible source substring points to one reference. This includes every independently visible member of a comma/semicolon list, every ordinary author-year citation, and both endpoints of a two-reference range. Its target field is the singular string `target_reference_id`, so never put a list there.

Use `compact_range_citations` only when one compact visible range represents several targets and at least one target has no separate visible characters. Its plural `target_reference_ids` lists all targets in source order. Never use this array merely to combine visible list members. The two arrays are mutually exclusive at the source-character level: after putting a range in `compact_range_citations`, do NOT also put its visible endpoints or any other substring of that same range in `single_target_citations`. Never invent characters for an implied target. Shared grouping punctuation remains ordinary source text.

The examples below are invented only to demonstrate the response contract. They are not patterns for deciding what counts as a citation.

Example 1 Source: [doc/p12] Several earlier trials reached the same conclusion [2,5]. Available targets: reference:2 has printed label [2]; reference:5 has printed label [5]. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"2","record_key":"doc/p12","left_context":"conclusion [","right_context":",5]"},"target_reference_id":"reference:2"},{"citation_quote":{"quote":"5","record_key":"doc/p12","left_context":"[2,","right_context":"]."},"target_reference_id":"reference:5"}],"issues":[]}
The brackets and comma remain ordinary source text. Each visible label gets its own item. Whitespace around a separator changes only the exact copied contexts, never whether the visible labels are separate items.

Example 2 Source: [doc/p27] The findings differ (Rivera and Chen, 2021; Okafor, 2023). Available targets: reference:8 has surnames Rivera and Chen and year 2021; reference:9 has surname Okafor and year 2023. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Rivera and Chen, 2021","record_key":"doc/p27","left_context":"differ (","right_context":"; Okafor"},"target_reference_id":"reference:8"},{"citation_quote":{"quote":"Okafor, 2023","record_key":"doc/p27","left_context":"2021; ","right_context":")."},"target_reference_id":"reference:9"}],"issues":[]}
The shared parentheses and semicolon remain ordinary source text. Do not wrap both works in one citation_quote when each work has its own contiguous visible identifying text.

Example 3 Source: [doc/p40] Reed (2022) reported the first result; Reed (2022) later revised it. Available target: reference:4 has surname Reed and year 2022. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"","right_context":" reported"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"Reed (2022)","record_key":"doc/p40","left_context":"result; ","right_context":" later"},"target_reference_id":"reference:4"}],"issues":[]}
The same quote is located twice without counting occurrences: its adjacent source text identifies which physical occurrence is meant.

Example 4 Source: [doc/p55] The combined evidence supports this conclusion [1-3, 7, 9-10]. Available targets have printed labels 1 through 3, 7, 9, and 10. Correct JSON:
{"compact_range_citations":[{"citation_quote":{"quote":"1-3","record_key":"doc/p55","left_context":"conclusion [","right_context":", 7"},"target_reference_ids":["reference:1","reference:2","reference:3"]}],"single_target_citations":[{"citation_quote":{"quote":"7","record_key":"doc/p55","left_context":"1-3, ","right_context":", 9-10"},"target_reference_id":"reference:7"},{"citation_quote":{"quote":"9","record_key":"doc/p55","left_context":"7, ","right_context":"-10]"},"target_reference_id":"reference:9"},{"citation_quote":{"quote":"10","record_key":"doc/p55","left_context":"9-","right_context":"]."},"target_reference_id":"reference:10"}],"issues":[]}
`1-3` stays one unit because reference:2 has no separate printed characters. `9-10` splits into two units because both targets have their own non-overlapping visible labels. Brackets, commas, and the hyphen between separately wrapped 9 and 10 remain ordinary source text. In particular, do NOT add separate single-target items for `1` or `3`; they are already inside the returned `1-3` compact-range item.

Example 5 Source: [doc/tbl2.r3] Cohort A ⇥ Improved after treatment [4, 6] Available targets have printed labels 4 and 6. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"4","record_key":"doc/tbl2.r3","left_context":"treatment [","right_context":", 6]"},"target_reference_id":"reference:4"},{"citation_quote":{"quote":"6","record_key":"doc/tbl2.r3","left_context":"[4, ","right_context":"]"},"target_reference_id":"reference:6"}],"issues":[]}
Table-row addresses follow exactly the same source-location contract as paragraph addresses. The following answer is incorrect for the same source:
{"compact_range_citations":[],"single_target_citations":[],"issues":["Skipped because the citations occur in a non-narrative table row."]}
Location in a table, comparison matrix, study-summary column, caption, or note is never by itself a reason to exclude a visible bibliographic citation.

Example 6 Source: [doc/p83] Catalyst A ⇥ Stable ⇥ Earlier work [11,13] Available targets have printed labels 11 and 13. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"11","record_key":"doc/p83","left_context":"Earlier work [","right_context":",13]"},"target_reference_id":"reference:11"},{"citation_quote":{"quote":"13","record_key":"doc/p83","left_context":"[11,","right_context":"]"},"target_reference_id":"reference:13"}],"issues":[]}
An ordinary paragraph may use tabs to present a visual table row. Its `doc/pN` address does not change the citation decision: inspect its visible content exactly as you inspect every other record. Do not first require the program or source format to label it as a table.

Example 7 Source records:
[doc/p70] The first paragraph ends with supporting evidence [8].
[doc/p71] A new paragraph begins here.
Available target: reference:8 has printed label [8]. Correct JSON:
{"compact_range_citations":[],"single_target_citations":[{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"]."},"target_reference_id":"reference:8"}],"issues":[]}
The right_context stops at the end of doc/p70. Never cross a newline into doc/p71, and never omit an intervening printed record address to make text from two records appear adjacent. Both examples below are incorrect:
{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\n[doc/p71] A new paragraph"},"target_reference_id":"reference:8"}
{"citation_quote":{"quote":"8","record_key":"doc/p70","left_context":"evidence [","right_context":"].\nA new paragraph"},"target_reference_id":"reference:8"}
The first crosses the record boundary and copies an address. The second still crosses the record boundary after hiding the address. Text from doc/p71 can never locate text in doc/p70.

Audit every supplied source record, including every `doc/tblN.rM` row and every `doc/pN` paragraph containing tab separators. Do not silently skip a record class. Match numbered citations to printed reference labels and author-year citations to surname + year + suffix identity. Narrative and parenthetical citations use the same representation. Use title and nearby semantic context only to resolve target identities, never to manufacture visible text. Do not assume one punctuation, capitalization, numbering, or author-name style. Do not return bibliography entries as citations. If either the exact source span or its unique target set is uncertain, omit that relation and report the uncertainty instead of guessing. Before returning, reread the source records from beginning to end and audit coverage: every visible in-text bibliographic citation you can uniquely resolve must occur exactly once across the two arrays. Do not skip a citation merely because its target appears nowhere else, and do not duplicate a source span in both arrays.
