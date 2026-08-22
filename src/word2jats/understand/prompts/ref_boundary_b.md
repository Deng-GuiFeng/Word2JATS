
TASK B (adversarial boundary audit): independently reconstruct the bibliography as a sequence of complete citations. Challenge paragraph-based and numbering-based assumptions: one citation may span blocks; several may share one block; labels may be absent; literal XML tag shells may be visible. For each citation return only a unique verbatim beginning excerpt in source order.

Return {"reference_title_node":"..."|null,"entries":[{"head_quote":"...","node_hint":"..."}],"first_non_reference_after":"..."|null,"non_reference_nodes":[],"issues":[]}.
