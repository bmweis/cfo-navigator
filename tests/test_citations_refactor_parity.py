"""Phase 1a extraction-refactor parity check.

Proves linklib/agent.py's move of its citation-assembly logic into the
shared linklib/citations.py module (_build_source_documents,
_assemble_cited_answer) is byte-identical to the real pre-refactor
behavior — not "the existing tests still pass" (weaker: existing
assertions could coincidentally pass on a subtly different
implementation), but a literal diff against output captured from the
actual pre-refactor code and frozen to
tests/citations_fixtures/golden_output.json (see
scripts/archive/capture_citations_golden_fixtures.py — archived per
CLAUDE.md's standing rule once its one-time capture run was confirmed;
its docstring covers the capture methodology).

Both this test and the capture script import the exact same scenario
definitions from tests/citations_fixtures/scenarios.py, so any drift is a
genuine behavior change in agent.py, never a difference in what was fed in.
"""
import json
import os
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import agent
from tests.citations_fixtures import scenarios

_GOLDEN_PATH = os.path.join(os.path.dirname(os.path.abspath(__file__)),
                            "citations_fixtures", "golden_output.json")


def _load_golden() -> dict:
    with open(_GOLDEN_PATH) as f:
        return json.load(f)


def test_golden_fixture_file_has_expected_scenario_coverage():
    """Sanity check that the golden file and the live scenario list haven't
    drifted apart (e.g. a scenario renamed/added on one side only)."""
    golden = _load_golden()
    assert set(golden["build_source_documents"]) == {
        s["name"] for s in scenarios.BUILD_SOURCE_DOCUMENTS_SCENARIOS
    }
    assert set(golden["assemble_cited_answer"]) == {
        s["name"] for s in scenarios.ASSEMBLE_CITED_ANSWER_SCENARIOS
    }


def test_build_source_documents_matches_golden_pre_refactor_output():
    golden = _load_golden()["build_source_documents"]
    for scenario in scenarios.BUILD_SOURCE_DOCUMENTS_SCENARIOS:
        doc_blocks, sent_docs = agent._build_source_documents(**scenario["kwargs"])
        expected = golden[scenario["name"]]
        assert doc_blocks == expected["doc_blocks"], f"doc_blocks drifted for scenario {scenario['name']!r}"
        assert sent_docs == expected["sent_docs"], f"sent_docs drifted for scenario {scenario['name']!r}"


def test_assemble_cited_answer_matches_golden_pre_refactor_output():
    golden = _load_golden()["assemble_cited_answer"]
    for scenario in scenarios.ASSEMBLE_CITED_ANSWER_SCENARIOS:
        text, citations = agent._assemble_cited_answer(scenario["content_blocks"], scenario["sent_docs"])
        expected = golden[scenario["name"]]
        assert text == expected["text"], f"text drifted for scenario {scenario['name']!r}"
        assert citations == expected["citations"], f"citations drifted for scenario {scenario['name']!r}"


def test_citations_module_functions_directly_match_golden_too():
    """Same proof, one layer lower — the new linklib.citations functions
    themselves (not just agent.py's wrappers around them) reproduce the
    golden _assemble_cited_answer output when called the way agent.py calls
    them (inject_markers=True, the default agent.py relies on)."""
    from linklib.citations import extract_citations
    golden = _load_golden()["assemble_cited_answer"]
    for scenario in scenarios.ASSEMBLE_CITED_ANSWER_SCENARIOS:
        text, citations = extract_citations(scenario["content_blocks"], scenario["sent_docs"])
        expected = golden[scenario["name"]]
        assert text == expected["text"]
        assert citations == expected["citations"]
