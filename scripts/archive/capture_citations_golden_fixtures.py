#!/usr/bin/env python3
"""One-time capture: run linklib/agent.py's pre-refactor citation-assembly
functions (`_build_source_documents`, `_assemble_cited_answer`) against a
fixed battery of scenarios and freeze the output as a golden reference file.

This is Phase 1a of the Citations-API grounding fix (see CLAUDE.md/
ARCHITECTURE.md for the full plan): before `_build_source_documents` and
`_assemble_cited_answer`'s logic moves into the new shared
`linklib/citations.py` module, this script captures what they actually
return TODAY — run against the real, unmodified pre-refactor code, not
regenerated after the move — so the refactor can be proven byte-identical
against real captured output rather than merely "tests still pass."

Run once, committed alongside the golden file it produces
(`tests/citations_fixtures/golden_output.json`). Not meant to run again in
the ordinary course — see CLAUDE.md's "Archive a one-time script as soon as
its run is confirmed" rule; this moves to scripts/archive/ once the capture
is verified.

Usage:
    python -m scripts.capture_citations_golden_fixtures
"""
from __future__ import annotations

import json
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from linklib import agent
from tests.citations_fixtures import scenarios

OUT_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)), "..",
                        "tests", "citations_fixtures", "golden_output.json")


def _capture_build_source_documents() -> dict:
    out = {}
    for scenario in scenarios.BUILD_SOURCE_DOCUMENTS_SCENARIOS:
        doc_blocks, sent_docs = agent._build_source_documents(**scenario["kwargs"])
        out[scenario["name"]] = {"doc_blocks": doc_blocks, "sent_docs": sent_docs}
    return out


def _capture_assemble_cited_answer() -> dict:
    out = {}
    for scenario in scenarios.ASSEMBLE_CITED_ANSWER_SCENARIOS:
        text, citations = agent._assemble_cited_answer(scenario["content_blocks"], scenario["sent_docs"])
        out[scenario["name"]] = {"text": text, "citations": citations}
    return out


def main() -> int:
    golden = {
        "_source": "captured from pre-refactor linklib/agent.py — see this script's docstring",
        "build_source_documents": _capture_build_source_documents(),
        "assemble_cited_answer": _capture_assemble_cited_answer(),
    }

    out_path = os.path.abspath(OUT_PATH)
    with open(out_path, "w") as f:
        json.dump(golden, f, indent=2, sort_keys=True)
        f.write("\n")

    # Write-then-read-back verification, per the standing one-off-script
    # convention (CLAUDE.md, "One-off admin fixes against the database").
    with open(out_path) as f:
        read_back = json.load(f)
    assert read_back == golden, "write-then-read-back mismatch — golden file was not written correctly"

    n_bsd = len(golden["build_source_documents"])
    n_aca = len(golden["assemble_cited_answer"])
    print(f"Wrote {out_path}")
    print(f"  build_source_documents: {n_bsd} scenarios captured")
    print(f"  assemble_cited_answer:  {n_aca} scenarios captured")
    print("Read-back verified: written JSON matches captured data exactly.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
