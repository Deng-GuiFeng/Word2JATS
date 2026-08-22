
TASK A (semantic segmentation): read the complete manuscript and identify the beginning of every bibliography entry by meaning, regardless of labels, paragraph boundaries, pasted XML, or line-break characters. Give a short unique verbatim head excerpt for each entry in source order. Do not parse fields.

Return {"reference_title_node":"..."|null,"entries":[{"head_quote":"...","node_hint":"..."}],"first_non_reference_after":"..."|null,"non_reference_nodes":[],"issues":[]}.
