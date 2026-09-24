"""Footer wrap fix (2026-09) — the "Logos provided by Logo.dev" phrase in
the shared site footer used to break mid-word ("Logos / provided / by /
Logo.dev") on narrow viewports, because `.site-footer .links` had no
`flex-wrap` control at all and its three items (Contact / · / Privacy /
· / the Logo.dev phrase) were `flex:none` at the mobile breakpoint — so
the row couldn't shrink and, rather than dropping cleanly to a new line,
silently overflowed the viewport with nothing to stop an individual
phrase's own text from wrapping internally if it were ever squeezed.

Fixed with two CSS rules: `.site-footer .links a{white-space:nowrap;}`
(no link's own text ever breaks internally, at any width) plus, inside
the `@media(max-width:640px)` block, `.site-footer .links{flex-wrap:wrap;
row-gap:6px;max-width:100%;}` (the row itself can now drop to a second
line as whole units instead of overflowing). `max-width:100%` is the
load-bearing part — `flex-wrap:wrap` alone does nothing here, since a
`flex:none` item's own box is sized to its content's max-content width
regardless of wrap; only capping it at the container's own width forces
its children to actually wrap.

These are markup-string-level checks (does the CSS contain what it needs
to), not computed-style checks — this file's own README-documented
sandbox has no reliable way to run a real browser against every CI
environment, and a live-browser verification of this exact fix was done
by hand during the PR (see the PR description) rather than committed as
a Playwright test."""
import pathlib
import re
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parents[1]))

import webapp.app as appmod


def _css_block():
    return appmod._CSS


def test_footer_links_have_nowrap_protection():
    """No link inside .site-footer .links should ever be allowed to break
    its own text mid-phrase, regardless of viewport width."""
    css = _css_block()
    assert ".site-footer .links a{white-space:nowrap;}" in css


def test_footer_links_row_can_wrap_as_whole_units_on_mobile():
    """Inside the mobile breakpoint, .links must be able to drop to a new
    line — flex-wrap alone is not enough for a flex:none item (its own box
    stays sized to its max-content width regardless of wrap), so max-width
    is what actually forces the wrap to take effect."""
    css = _css_block()
    # Locate the .site-footer mobile-breakpoint declaration block directly
    # (there are several unrelated @media(max-width:640px) blocks in the
    # sitewide CSS, so anchor on the known-adjacent footer rule instead of
    # trying to bound an arbitrary @media block generically).
    anchor = css.find(".site-footer{flex-wrap:wrap;justify-content:center;text-align:center;}")
    assert anchor != -1, "expected the .site-footer mobile-breakpoint rule"
    nearby = css[anchor:anchor + 400]
    # There's also a combined `.brand,.center,.links{flex:none;}` rule in
    # this same block — anchor on the standalone `.links{justify-content`
    # rule specifically, not the first ".links{" substring found.
    links_rule = re.search(r"\.site-footer \.links\{justify-content:center;([^}]*)\}", nearby)
    assert links_rule, f"expected a standalone .site-footer .links{{justify-content:center;...}} rule near the mobile breakpoint, got: {nearby!r}"
    decls = links_rule.group(1)
    assert "flex-wrap:wrap" in decls
    assert "max-width:100%" in decls


def test_desktop_footer_links_row_is_unaffected():
    """The nowrap-on-links-a rule and the wrap-enabling rule are the only
    changes; .site-footer .links' own desktop declaration (flex:1, no
    flex-wrap) must still be present and untouched outside the media
    query."""
    css = _css_block()
    # The desktop rule (outside any @media block) still ends in
    # justify-content:flex-end with no flex-wrap set on it directly.
    desktop_rule = re.search(
        r"\.site-footer \.links\{flex:1;display:flex;gap:10px;"
        r"align-items:center;justify-content:flex-end;\}",
        css,
    )
    assert desktop_rule, "desktop .links rule must be unchanged"
