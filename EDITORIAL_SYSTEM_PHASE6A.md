# Editorial Content System — Phase 6a Investigation

**Investigation only. No code changed, no pages touched.** Findings on what exists today,
concrete pattern options for an Axios-style short-form treatment and an Atlantic-style
long-form treatment, the simplest viable tagging mechanism, and an honest scope estimate
for each existing page — so Brian can choose a direction with real numbers in hand, not
just the two reference points.

---

## 1. Inventory of current long-form content

Word counts are `.page`'s rendered `innerText` (nav/footer live outside `.page`, so this
is close to true body content; it includes small chrome like the back-link and byline,
a few words of overcount, not enough to change the read).

| Page | Route | Words | H2 | H3 | Pull-quotes | Callouts/warnings | Images |
|---|---|---|---|---|---|---|---|
| Growth Engine Ratio | `/thought-leadership/growth-engine-ratio` | ~946 | 7 | 0 | 0 | 1 (cross-promo notice, navy-wash) | 0 |
| AI Hackathon Playbook | `/thought-leadership/ai-hackathon-playbook` | ~2,722 | 9 | 12 | 3 (`.fah-pull`) | 3 callout + 1 warning (`.fah-callout`/`.fah-warn`) | 0 |
| Connecting Claude to NetSuite | `/thought-leadership/netsuite-mcp` | ~2,270 | 7 | 15 | 0 (`.ns-pull` CSS exists, never used) | 2 callout + 3 warning (`.ns-callout`/`.ns-warn`) | 0 |
| How FP&A Buddy Works | `/admin/system/how-fpa-buddy-works` | ~709 | 0 | 4 | 0 | 0 | 0 (1 Mermaid flowchart + 1 table) |

**Others considered and excluded** — read as landing/hub pages or bio blurbs, not
articles: homepage (~266 words), About (~269 words, but see §2 — it already has the
site's only in-body photos), Thought Leadership landing (~406 words, a card index).
`/admin/tools/communities`'s collapsible reference block (~830 words) is UI documentation
for admins, not editorial content — out of scope here.

**Read on the numbers:** Hackathon Playbook and NetSuite MCP are already firmly
long-form by length and already lean on subheads + pull-quotes/callouts (Hackathon) or
subheads + callouts/warnings (NetSuite). GER and How FP&A Buddy Works are both well
under 1,000 words — short enough that Brian's length-based split would plausibly tag
both "Axios," which matters for scope (see §5): the two pages needing the *least* new
content happen to be the two shortest ones.

**One nuance on the length-based theory:** NetSuite MCP is long (2,270 words) but its
content type is a numbered technical setup guide with a permissions table and a
troubleshooting table — closer to structured documentation than a literary essay.
Length alone doesn't fully predict which register fits; a long *technical walkthrough*
may want Axios's scannability (bold ledes, tighter breaks) more than Atlantic's
generous unbroken reading column, even though it's well past any word-count threshold
for "long." Worth deciding case-by-case rather than purely by length if that distinction
matters to Brian.

---

## 2. What exists today to build on

**Images in article content: nothing reusable, one real precedent.** The only in-body
(non-card, non-avatar, non-screenshot) images on the whole site are the two speaking
photos on `/about` (`webapp/app.py:1412-1418`) — a hand-coded 2-column grid
(`grid-template-columns:2fr 3fr`), fixed `height:200px`, `object-fit:cover`, rounded
corners, and an italic caption below. It's a working, attractive pattern, but it's
bespoke inline CSS on that one page, not a shared helper — a new article wanting an
image would hand-roll the same markup rather than call a function. No CDN/upload
pipeline either: `/static/{filename}` just serves whatever's already in the Docker
image, so a new article image means adding a file to the repo, not a runtime upload.

**Pull-quotes and callouts: a real, proven pattern — but duplicated, not shared.**
Hackathon Playbook and NetSuite MCP each define their own copy of the identical
pull-quote/callout/warning CSS, just prefixed differently (`.fah-*` vs `.ns-*`):
- Pull-quote: navy-wash background, 3px navy left border, italic 16-17px text.
- Callout: seafoam-wash background, 2px seafoam-mid top border, labeled with an
  eyebrow-style title (`.fah-callout-title`/`.ns-callout-title`, now standardized at
  11.5px/600/.1em per the brand audit's eyebrow convergence).
- Warning: coral-wash background, 2px coral top border, same eyebrow-labeled title
  treatment in coral-deep.

This is functionally a "why it matters"-style aside already — the callout box's whole
job is "here's a framed, visually distinct thing worth pulling out of the flow." It's
proven across two live articles and reads well. The only thing missing is that it's
copy-pasted CSS per page (same repeat-the-pattern issue the brand audit found
elsewhere) rather than one shared definition — cheap to fix, and worth fixing *as part
of* building either new pattern rather than as prep work first.

GER has none of this in its body — its one navy-wash box is a cross-promotional link
to an external whitepaper, not an editorial device. How FP&A Buddy Works has none at
all: intro paragraph, one Mermaid diagram, then four H3-headed sections of plain
paragraphs and bullet lists, no visual break device anywhere in the prose itself.

**How a new long-form page gets built today: always a new, fully standalone Python
function.** Every article (`growth_engine_ratio()`, `finops_ai_hackathon()`,
`netsuite_mcp()`) is its own route handler that returns a big triple-quoted HTML
string, with its own `<style>` block defining page-specific classes from scratch. There
is no shared "article" scaffolding, template, or base class — not even a shared
pull-quote/callout helper, as above. `_page()` (the site-wide nav/footer wrapper) and
`.tool-prose`/`.tool-inner` (the shared width/reading-measure classes) are the only
things every article currently shares. This is directly relevant to §4: anything that
isn't a plain CSS class convention will require touching three existing route
functions to retrofit, plus becoming the pattern every future article route copies.

---

## 3. Two pattern proposals (sketches, not code)

Both reuse the existing color system as-is (seafoam-wash/coral-wash/navy-wash already
exist and are already documented for "callout blocks," BRAND.md §2.2) — no new tokens
needed for either.

### Pattern A — Axios-style (short-form, scan-first)

**Typography:** Body stays DM Sans at the current 16px, but the *first sentence* of
the opening paragraph of every major section is bold — a "lede." Paragraphs stay short:
2-4 sentences. Prefer a 3-bullet list over a 3-clause sentence wherever the content is
actually a list of parallel points (this is already how Hackathon Playbook's "three
reasons" section reads — that instinct already exists in the site's own writing).

**"Why it matters" box:** A single, sitewide-consistent callout — same visual family as
the existing `.fah-callout`/`.ns-callout` (seafoam-wash, top border, eyebrow title) but
with a fixed title convention: always literally "Why it matters," not a bespoke
per-instance title. Dropped in once per major section, right after the lede paragraph,
not scattered more often than that.

**No pull-quotes.** Axios doesn't really pull quotes out of body text; if a number or
stat deserves emphasis, treat it like GER's big readout numeral (`.ger-value-big`,
Outfit, large, navy) rather than a quoted aside.

**Concrete break rule:** no more than **~100 words (roughly 3-4 sentences) of
unbroken paragraph text** before a bullet list, a bolded lede opening the next
paragraph, or a "Why it matters" box. Checkable by eye or a simple word-count script
against paragraph-to-paragraph gaps.

### Pattern B — Atlantic-style (long-form, sit-with-it)

**Typography:** Keep `.tool-prose`'s current serif-free DM Sans body (no new font
needed — Source Serif 4 stays reserved for `/read` per BRAND.md §3, not extended
here), but widen line-height slightly (1.65 → ~1.75) and paragraph spacing for a more
generous, unhurried feel. No bolded ledes — paragraphs read as continuous prose, the
opposite instinct from Pattern A.

**Pull-quotes, generalized:** Promote the existing `.fah-pull`/`.ns-pull` visual
pattern (navy-wash, left border, italic) to one shared, unprefixed class (`.article-pull`
or similar) instead of two copy-pasted per-article versions — used to lift out one
resonant sentence per major section, roughly the same cadence Hackathon Playbook
already uses (3 pull-quotes across 9 H2 sections).

**Occasional imagery:** Reuse the About page's existing 2-column image-grid-with-caption
pattern (`webapp/app.py:1412-1418`), generalized into a shared helper, dropped in
roughly every 800-1,200 words as a genuine visual break, not decoration. This is the
one part of Pattern B with a real, non-trivial cost — new photos/diagrams need to
exist first (see §5).

**Concrete break rule:** no more than **~250 words (roughly 6-8 lines at the current
16px/1.65-1.75 body measure) of unbroken paragraph text** before a subhead (H2/H3), a
pull-quote, a callout, or an image. Looser than Axios's rule by design — Atlantic
readers are settling in, not scanning — but still a checkable number, not a vibe.

### On a third option
I don't think a third, separate visual system is warranted — a "blended" style is
exactly what Brian's brief is trying to avoid, and the two patterns above already share
their underlying primitives (same color washes, same eyebrow-title convention, same
`.tool-prose` reading measure). The real "third option" is the tagging mechanism
itself (§4), which is a separate axis from the two content patterns, not a third style.

---

## 4. Tagging mechanism — simplest viable option

Given the no-CMS, hand-written-HTML-per-route reality, the simplest mechanism that
actually works: **a second CSS class alongside the existing tier class**, e.g.
`class="page page-full article-axios"` or `class="page page-full article-atlantic"`,
with a small block of CSS rules keyed off `.article-axios .tool-prose{...}` /
`.article-atlantic .tool-prose{...}` for the typographic differences (line-height,
paragraph spacing). This requires zero new Python abstraction — it's the same pattern
`.page-full`/`.page-admin` already use, just one more class.

**What the tag can and can't do.** The class can flip a handful of CSS properties
(line-height, paragraph margin, maybe max-width nuance) automatically. It **cannot**
insert a bolded lede, decide where a pull-quote goes, or write a "why it matters" box —
those are authored by hand into the page's HTML either way, tag or no tag. The tag is a
typographic switch and a documentation signal ("this page follows the Atlantic
checklist"), not an enforcement mechanism. That's consistent with the fact that this is
inline-HTML-per-route with no CMS: nothing here can auto-validate "did the author
actually break up the text every 250 words," the same way nothing today auto-validates
prose quality. A human still has to apply the pattern's rules by hand each time — which
means the real ongoing cost isn't the CSS, it's an author (Brian, or whoever drafts
next) following the checklist consistently, the same discipline already required for
voice-review.

**A shared Python helper is possible but not necessary as a first step** — e.g. a
`_pull_quote(text)` / `_callout(title, body)` function pair so both patterns draw from
one implementation instead of two copy-pasted CSS blocks. Worth doing whenever the
patterns get built (it directly fixes the `.fah-*`/`.ns-*` duplication noted in §2),
but it's an implementation-quality improvement, not a prerequisite for the tagging
decision itself — the CSS-class tag works with or without it.

---

## 5. Honest scope estimate — new content, not just CSS

This is the number that should drive Brian's decision more than the stylistic
preference, since new copy always needs a voice pass before it ships.

| Page | If tagged Axios | If tagged Atlantic |
|---|---|---|
| **Growth Engine Ratio** (946w, 7 H2, 1 callout) | Low — mostly a CSS pass (bold the lede of each existing section's first paragraph) plus maybe 2-3 new "Why it matters" boxes drawn from content that's already there. No new research or claims needed. | Medium — needs 1-2 new pull-quotes pulled/adapted from existing text (low-cost) but a genuine image would need to be sourced or commissioned; GER has no photography angle today. |
| **How FP&A Buddy Works** (709w, 0 callouts, 0 images) | Low-medium — needs 1 "Why it matters" box per H3 section (4 new short boxes) and a few bolded ledes; all new copy, but short and mechanical (each box summarizes what's already stated below it), lowest-effort net-new writing of the four. | Medium-high — needs 1-2 new pull-quotes (there's no existing quotable prose to lift from — it's mostly mechanism description, not opinion, so a pull-quote here means writing a new, quotable line from scratch) and this page has zero photography/diagram angle beyond its existing flowchart, so "imagery" would mean commissioning a new diagram. |
| **AI Hackathon Playbook** (2,722w, already has 3 pull-quotes + 3 callouts + 1 warning) | Low — already has the callout/warning boxes; would mainly need bolded ledes added retroactively (a copy-editing pass, not new ideas) and the pull-quotes converted to bold-stat treatment or dropped. | Very low — already the closest of the four to this pattern today. Real new work is limited to 1-2 photos (if Brian wants imagery) since the pull-quotes/callouts already exist; could plausibly ship with zero new prose. |
| **Connecting Claude to NetSuite** (2,270w, 2 callouts + 3 warnings, 0 pull-quotes) | Low-medium — already has callouts/warnings; needs bolded ledes added and, given the content is genuinely a technical walkthrough (see §1's nuance), Axios's scan-first register may fit this page's *content type* better than length alone would suggest. | Medium — would need 2-3 new pull-quotes written from scratch (existing prose is instructional, not naturally quotable — "confirm your role first" doesn't pull-quote well) and, like How FP&A Buddy Works, no natural photography angle; a diagram (e.g. the two-role OAuth flow) is the more plausible "imagery" here, which is new design work. |

**Bottom line on cost:** the callout/warning boxes are the cheap part everywhere (they
already exist on 2 of 4 pages and are mechanical to add on the other 2). Pull-quotes
are cheap on pages with existing opinionated/narrative prose (Hackathon Playbook) and
expensive on pages that are mechanism-description or technical-instruction prose (How
FP&A Buddy Works, NetSuite MCP) — those would need genuinely new sentences written
specifically to be quotable, not just excerpted. Imagery is the most expensive
component across the board except `/about` and Hackathon Playbook (which already has a
plausible photo/screenshot angle from an in-person hackathon); the other three pages
have no natural photography subject, so "imagery" there really means "commission a new
diagram," which is real design work, not a five-minute add.

---

## Recommendation

Build the CSS-class tagging mechanism (§4) first, generalize the existing
pull-quote/callout/warning trio into one shared helper (fixing the `.fah-*`/`.ns-*`
duplication as a side effect), and apply the **Atlantic pattern to AI Hackathon
Playbook first** — it's the one page where the pattern is nearly free (the devices
already exist, length already fits, and it has the most plausible imagery angle of the
non-About pages). That gives a real, low-risk pilot to sign off on before touching
anything else.

I'd hold off on How FP&A Buddy Works and Connecting Claude to NetSuite regardless of
which pattern they're tagged — both need genuinely new writing (not just formatting)
to hit either pattern's break-rule honestly, and that's real drafting work requiring
Brian's own voice pass, not a mechanical retrofit. Growth Engine Ratio sits in between:
cheap under Axios, more expensive under Atlantic (needs an image with no natural
source). Given the length-based theory Brian's starting from, Axios for GER and How
FP&A Buddy Works, Atlantic for the two thought-leadership long-reads, is both the
theory's own prediction and the cheapest path by the numbers above — a reasonable
starting split if a decision is wanted now, but the real judgment call (NetSuite MCP's
content-type mismatch, whether an About-page-style photo is worth sourcing for GER) is
Brian's to make.
