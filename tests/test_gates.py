"""Unit tests for linklib/gates.py — the shared review-state gate module
(Gate-Extraction PR B). Pure-function coverage of the full three-state
table (verified / pending / empty) x both viewers, for both entity shapes
(tools' independent per-field flags, communities' one-flag-drives-N-badges
model), plus the row-existence primitive both compare matrices share.

This is deliberately separate from tests/test_review_state_publish_gates.py,
which already covers the same standard end-to-end through real HTTP
responses on both entity types (profile page, compare matrix, directory
card) — that suite is the "does the rendered page still look right" check
and stays green unchanged by this PR (see its own module docstring). This
file is the "does the underlying decision logic itself behave correctly,
in isolation, independent of any page template" check — the layer the end-
to-end suite exercises indirectly."""
import pathlib
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib import gates


# ---------------------------------------------------------------------------
# GateState / state_for / field_state — the full 3-state x 2-viewer table.
# ---------------------------------------------------------------------------

class TestFieldState:
    def test_verified_when_content_present_and_not_unverified(self):
        assert gates.field_state("real content", False) == gates.GateState.VERIFIED

    def test_pending_when_content_present_and_unverified(self):
        assert gates.field_state("real content", True) == gates.GateState.PENDING

    def test_empty_when_content_blank(self):
        assert gates.field_state("", False) == gates.GateState.EMPTY
        assert gates.field_state("", True) == gates.GateState.EMPTY

    def test_empty_when_content_none(self):
        assert gates.field_state(None, False) == gates.GateState.EMPTY
        assert gates.field_state(None, True) == gates.GateState.EMPTY

    def test_empty_when_content_whitespace_only(self):
        """A populated-looking-but-actually-blank field (e.g. a stray
        newline from a save form) is still EMPTY, not VERIFIED/PENDING —
        matches every existing call site's own `.strip()` check."""
        assert gates.field_state("   \n\t  ", True) == gates.GateState.EMPTY

    def test_state_for_matches_field_state_for_nonblank_content(self):
        """state_for is the verified/pending half of field_state, used by
        callers that already know content is present (every real call
        site checks emptiness itself before deciding on a badge)."""
        assert gates.state_for(False) == gates.field_state("x", False)
        assert gates.state_for(True) == gates.field_state("x", True)


# ---------------------------------------------------------------------------
# badge_text — the actual copy, per viewer, per state.
# ---------------------------------------------------------------------------

class TestBadgeText:
    def test_pending_admin_gets_admin_copy(self):
        assert gates.badge_text(gates.GateState.PENDING, True) == gates.BADGE_TEXT_ADMIN
        assert gates.badge_text(gates.GateState.PENDING, True) == "unverified, visible to visitors"

    def test_pending_visitor_gets_visitor_copy(self):
        assert gates.badge_text(gates.GateState.PENDING, False) == gates.BADGE_TEXT_VISITOR
        assert gates.badge_text(gates.GateState.PENDING, False) == "under review"

    def test_verified_carries_no_badge_for_either_viewer(self):
        """A populated field with no badge IS the verified signal."""
        assert gates.badge_text(gates.GateState.VERIFIED, True) is None
        assert gates.badge_text(gates.GateState.VERIFIED, False) is None

    def test_empty_carries_no_badge_for_either_viewer(self):
        """An empty field gets its own placeholder, never a badge."""
        assert gates.badge_text(gates.GateState.EMPTY, True) is None
        assert gates.badge_text(gates.GateState.EMPTY, False) is None


# ---------------------------------------------------------------------------
# Full end-to-end combination table, per entity shape.
# ---------------------------------------------------------------------------

class TestFullTableTools:
    """Tools: 3 independent per-field flags. Each field's own unverified
    bool is what field_state/badge_text receive — no cross-field
    interaction, verified per field in isolation here."""

    def test_verified_populated_no_badge(self):
        state = gates.field_state("A confirmed description.", False)
        assert state == gates.GateState.VERIFIED
        assert gates.badge_text(state, True) is None
        assert gates.badge_text(state, False) is None

    def test_pending_populated_admin_badge(self):
        state = gates.field_state("A drafted description.", True)
        assert state == gates.GateState.PENDING
        assert gates.badge_text(state, True) == "unverified, visible to visitors"

    def test_pending_populated_visitor_badge(self):
        state = gates.field_state("A drafted description.", True)
        assert state == gates.GateState.PENDING
        assert gates.badge_text(state, False) == "under review"

    def test_empty_no_content_admin_and_visitor_both_get_no_badge(self):
        state = gates.field_state("", True)  # unverified=True is irrelevant once empty
        assert state == gates.GateState.EMPTY
        assert gates.badge_text(state, True) is None
        assert gates.badge_text(state, False) is None


class TestFullTableCommunities:
    """Communities: one whole-profile `needs_review` flag reused across
    every field in the profile — the one permitted cross-entity divergence
    (N badges from one flag, vs. tools' N independent flags), verified here
    by feeding the identical `unverified` bool into multiple field_state
    calls, same as _profile_badge being computed once and reused N times
    in webapp/app.py."""

    def test_one_flag_drives_verified_state_for_every_field_when_reviewed(self):
        unverified = False  # profile.get("needs_review") == 0
        for content in ("Ideal member text.", "Anti-fit text.", "2019"):
            assert gates.field_state(content, unverified) == gates.GateState.VERIFIED

    def test_one_flag_drives_pending_state_for_every_populated_field_when_unreviewed(self):
        unverified = True  # profile.get("needs_review") == 1
        for content in ("Ideal member text.", "Anti-fit text.", "2019"):
            state = gates.field_state(content, unverified)
            assert state == gates.GateState.PENDING
            assert gates.badge_text(state, True) == "unverified, visible to visitors"
            assert gates.badge_text(state, False) == "under review"

    def test_missing_profile_field_is_empty_regardless_of_whole_profile_flag(self):
        """A community with needs_review=1 but a genuinely blank field
        (e.g. never drafted) still reads EMPTY, not PENDING — an empty
        field's own placeholder wins over the whole-profile pending state."""
        for unverified in (True, False):
            assert gates.field_state("", unverified) == gates.GateState.EMPTY

    def test_missing_profile_dict_reads_as_empty(self):
        """webapp/app.py computes `bool(profile.get("needs_review"))` —
        an entirely-missing profile ({}).get(...) is falsy, so a community
        with no profile row at all reads as reviewed=True/unverified=False
        for the (moot, since content is also empty) badge decision, and
        every field is EMPTY regardless."""
        profile: dict = {}
        unverified = bool(profile.get("needs_review"))
        assert unverified is False
        assert gates.field_state(profile.get("verdict_summary"), unverified) == gates.GateState.EMPTY


# ---------------------------------------------------------------------------
# any_populated — the compare-matrix row-existence primitive (item #10).
# ---------------------------------------------------------------------------

class TestAnyPopulated:
    def test_true_when_at_least_one_value_nonblank(self):
        assert gates.any_populated(["", None, "real value"]) is True

    def test_false_when_every_value_blank(self):
        assert gates.any_populated(["", None, "   "]) is False

    def test_false_for_empty_list(self):
        assert gates.any_populated([]) is False

    def test_row_existence_independent_of_per_cell_gate_state(self):
        """A row can exist (any_populated True) while individual cells are
        in different states — row-existence and per-cell verified/pending/
        empty are architecturally separate checks (Phase 0 item #10),
        verified here directly rather than only through a rendered page."""
        values_with_mixed_states = ["A pending draft.", "", "A verified note."]
        assert gates.any_populated(values_with_mixed_states) is True
        # Per-cell states, independent of the row check above:
        assert gates.field_state(values_with_mixed_states[0], True) == gates.GateState.PENDING
        assert gates.field_state(values_with_mixed_states[1], False) == gates.GateState.EMPTY
        assert gates.field_state(values_with_mixed_states[2], False) == gates.GateState.VERIFIED


# ---------------------------------------------------------------------------
# EMPTY_COPY / COMPARE_EMPTY_LABELS — the frozen copy registry.
# ---------------------------------------------------------------------------

class TestEmptyCopyRegistry:
    def test_every_registered_field_has_visitor_text(self):
        for key, copy in gates.EMPTY_COPY.items():
            assert copy.visitor_text, f"{key} has no visitor_text"

    def test_tool_description_copy_matches_pr_a1_approved_string(self):
        copy = gates.EMPTY_COPY["tool_description"]
        assert copy.visitor_text == "Description coming soon."
        assert copy.admin_suffix == "Add one from the edit page."

    def test_community_profile_group_copy_matches_pr_a1_approved_string(self):
        copy = gates.EMPTY_COPY["community_profile_group"]
        assert copy.visitor_text == "This section hasn't been researched yet."

    def test_compare_empty_labels_have_no_admin_suffix_concept(self):
        """Compare-matrix empty labels are plain strings, not EmptyCopy
        tuples — a compare cell never carries a "go fill this in" prompt."""
        for key, label in gates.COMPARE_EMPTY_LABELS.items():
            assert isinstance(label, str)
            assert label


# ---------------------------------------------------------------------------
# Matchmaker constants — single-sourced, frozen copy.
# ---------------------------------------------------------------------------

class TestMatchmakerConstants:
    def test_field_suffix(self):
        assert gates.MATCHMAKER_FIELD_SUFFIX == " (unverified)"

    def test_community_note(self):
        assert gates.MATCHMAKER_COMMUNITY_NOTE == (
            "Note: this community's profile is unverified; treat the following details as provisional.\n"
        )

    def test_disclaimer_wording(self):
        assert "Some catalog details above are marked unverified." in gates.MATCHMAKER_DISCLAIMER
        assert "provisional" in gates.MATCHMAKER_DISCLAIMER


# ---------------------------------------------------------------------------
# The directory-JS badge copy — a confirmed, pre-existing, NOT-unified
# divergence from the profile/compare badge copy (capitalized vs.
# lowercase). Frozen here so a future edit can't silently "fix" it into a
# copy change this PR was explicitly not licensed to make.
# ---------------------------------------------------------------------------

class TestDirectoryJsBadgeDivergence:
    def test_directory_js_variant_is_capitalized_unlike_the_shared_badge(self):
        assert gates.DIRECTORY_JS_BADGE_TEXT_ADMIN == "Unverified, visible to visitors"
        assert gates.DIRECTORY_JS_BADGE_TEXT_VISITOR == "Under review"
        assert gates.DIRECTORY_JS_BADGE_TEXT_ADMIN != gates.BADGE_TEXT_ADMIN
        assert gates.DIRECTORY_JS_BADGE_TEXT_VISITOR != gates.BADGE_TEXT_VISITOR
        assert gates.DIRECTORY_JS_BADGE_TEXT_ADMIN.lower() == gates.BADGE_TEXT_ADMIN
        assert gates.DIRECTORY_JS_BADGE_TEXT_VISITOR.lower() == gates.BADGE_TEXT_VISITOR
