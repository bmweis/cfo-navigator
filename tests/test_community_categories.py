"""Reserved-name guard on community categories (Phase J3 follow-up).

Community categories don't otherwise have a dedicated test file (coverage
lives inline in tests/test_admin_bulk_edit.py and test_admin_sort_filter.py);
this one just covers the "Uncategorized" reservation added alongside the
tool-category version in linklib.db.RESERVED_CATEGORY_NAME.
"""
import pathlib
import sys

import pytest

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

from linklib.db import Library


@pytest.fixture
def lib(tmp_path):
    db = Library(str(tmp_path / "t.db"))
    try:
        yield db
    finally:
        db.close()


def test_add_reserved_uncategorized_name_rejected_case_insensitive(lib):
    # "Uncategorized" is the directory's built-in hygiene filter (Phase J3,
    # the __uncategorized__ sentinel in the /tools/communities and
    # /admin/tools/communities pill filters) — a real category with this
    # name would be indistinguishable from it, so creation is blocked.
    with pytest.raises(ValueError):
        lib.add_community_category("Uncategorized")
    with pytest.raises(ValueError):
        lib.add_community_category("uncategorized")
    with pytest.raises(ValueError):
        lib.add_community_category("  UNCATEGORIZED  ")


def test_rename_to_reserved_uncategorized_name_rejected(lib):
    cat_id = lib.add_community_category("Peer Group")
    with pytest.raises(ValueError):
        lib.rename_community_category(cat_id, "Uncategorized")
