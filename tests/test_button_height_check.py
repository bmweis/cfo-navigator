"""The "Button height is the shared standard" check: it passes on the real source and fails on a planted rogue style."""
import pathlib

from linklib import brand_check

APP_SRC = pathlib.Path(__file__).resolve().parents[1].joinpath("webapp", "app.py").read_text(encoding="utf-8")
SHARED = (":root{--control-h:28px;}\n.btn,.btn-ghost,.ctl{height:var(--control-h)!important;"
          "min-height:var(--control-h)!important;padding-top:0!important;}\n")


def test_real_source_is_clean():
    assert brand_check.button_height_problems(APP_SRC) == []


def test_planted_css_height_is_flagged():
    bad = SHARED + ".row-btn.btn{height:44px;}"
    assert any("sets its own button height" in p for p in brand_check.button_height_problems(bad))


def test_planted_min_height_and_vertical_padding_are_flagged():
    probs = brand_check.button_height_problems(SHARED + ".x .btn-ghost{min-height:44px;}")
    assert any("own button height" in p for p in probs)
    probs = brand_check.button_height_problems(SHARED + ".ask-ctl{padding:11px 22px;}")
    assert any("vertical padding" in p for p in probs)
    probs = brand_check.button_height_problems(SHARED + ".ctl{padding-top:6px;}")
    assert any("vertical padding" in p for p in probs)


def test_planted_fstring_braces_are_flagged():
    assert brand_check.button_height_problems(SHARED + ".admin-only{{height:42px;}}")


def test_planted_inline_height_is_flagged():
    bad = SHARED + '<button class="btn" style="height:60px;">x</button>'
    assert any("inline style" in p for p in brand_check.button_height_problems(bad))
    bad = SHARED + '<a class="btn-ghost" href="#" style="min-height:44px">x</a>'
    assert any("inline style" in p for p in brand_check.button_height_problems(bad))
    bad = SHARED + '<input type="submit" style="height:50px">'
    assert any("inline style" in p for p in brand_check.button_height_problems(bad))


def test_clean_variants_pass():
    ok = (SHARED + ".btn{padding:0 22px;}\n.fu-send svg{width:14px;height:14px;}\n"
          '<button class="btn" style="font-size:12px;padding:3px 10px;">x</button>\n'
          '<input type="text" style="height:50px">\n<div class="card" style="height:40px">\n'
          '<button role="switch" style="width:38px;height:22px">')
    assert brand_check.button_height_problems(ok) == []


def test_allowlisted_class_is_skipped_and_has_a_reason():
    ok = SHARED + '<button class="nav-toggle" style="height:40px">'
    assert brand_check.button_height_problems(ok) == []
    assert all(len(v) > 20 for v in brand_check.BUTTON_HEIGHT_ALLOWLIST.values())


def test_missing_shared_rule_or_token_is_flagged():
    probs = brand_check.button_height_problems("a{color:red}")
    assert any("token" in p for p in probs) and any("shared" in p for p in probs)


def test_check_row_is_on_admin_checks():
    from webapp import checks
    row = next(r for r in checks.run_all() if r["name"] == "Button height is the shared standard")
    assert row["ok"] is True and row["where"] == "Live + CI"
