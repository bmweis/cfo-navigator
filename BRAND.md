# Brand Standards — bmweis.com / CFO Navigator

*New England nautical, restrained. An editorial system for a senior-finance-executive
audience: warm, structured, credible. Cool navy and seafoam, a single warm coral pop,
geometric sans typography, and exactly one nautical motif.*

---

## 1. Brand personality

| We are | We are not |
|---|---|
| Editorial, structured, considered | Flashy, trendy, startup-loud |
| Warm and human (off-white, not cold white) | Sterile / corporate-blue SaaS |
| Quietly nautical (one motif, used sparingly) | Anchors, ropes-everywhere, boats, clip-art |
| Confident in whitespace | Busy, gradient-heavy, decorative |

The whole system runs on **restraint**. Navy carries the structure, seafoam is a frequent-but-quiet
accent, and coral is the rare highlight you notice precisely *because* it's rare.

---

## 2. Color

### 2.1 The three brand colors — each with a working ramp

Three families: **Navy** (primary, cool), **Seafoam/Green** (cool accent), **Coral** (warm accent).
Each has a few shades so you're never stuck reaching outside the system. Contrast figures are
measured on the `#F5F4EF` canvas.

**Navy — primary (cool)**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--navy-deep` | `#001B4F` | 14:1 | Button hover, depth |
| `--navy` | `#002975` | 12:1 | Base — wordmark, links, buttons, headings accents |
| `--navy-light` | `#3F5C9A` | 5.9:1 | Lighter navy — secondary accents, borders (text-capable) |
| `--navy-wash` | `#EEF1F7` | fill | Soft navy fill — chip & ghost-button hovers |

**Seafoam / Green — cool accent**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--seafoam-deep` | `#1F7A66` | 4.7:1 | Deepest teal — text-capable on light (AA) |
| `--seafoam-mid` | `#2E9C86` | 3.1:1 | Mid teal — **data-viz** (legible as a fill/line); ≥18px text only |
| `--seafoam` | `#A3E5D4` | fill | Base accent (light mint) — tags, badges, active-nav underline |
| `--seafoam-wash` | `#EAF7F2` | fill | Soft fill — calculator readout, table accents |

**Coral — warm accent (rare)**
| Token | Hex | Contrast | Use |
|---|---|---|---|
| `--coral-deep` | `#B14A30` | 4.9:1 | Text-capable coral (AA) — *only* when coral must carry small text |
| `--coral` | `#E8704F` | 3.1:1 | Base — display pop, badges, data-viz; graphics & ≥24px only |
| `--coral-light` | `#F4A98F` | fill | Lighter coral — soft highlights, fills only (never text) |
| `--coral-wash` | `#FBEAE3` | fill | Soft fill — callout blocks (put **navy** text on it) |

> **Read the ramps the same way each time:** *deep* shades are dark enough for small text (AA);
> *base/mid* are for graphics, fills, and large display; *light/wash* are fills only. When in doubt,
> small text is navy, ink, or a `-deep`; never a `base`/`light`/`wash`.

### 2.2 Neutrals & semantic

| Token | Hex | Use |
|---|---|---|
| `--bg` | `#F5F4EF` | Page canvas (warm off-white) |
| `--surface` | `#FFFFFF` | Cards, inputs |
| `--surface-2` | `#FAF9F4` | Alt panels, table stripes |
| `--ink` | `#1a1a1a` | Headings, primary text |
| `--ink-soft` | `#3a3833` | Body copy |
| `--muted` | `#6F6A60` | Meta, captions, kickers |
| `--line` | `#E4E0D6` | Warm hairline |
| `--line-strong` | `#D6D1C4` | Heavier divider / top line of the rope rule |
| `--good` / `--caution` / `--alert` | `#002975` / `#9A6B12` / `#9E3B30` | **Status only** — GER tiers, form errors |

> **Semantic ≠ brand.** The alert red `#9E3B30` means *error/danger*. Coral is decorative and
> never signals status. They're 96 RGB-units apart so they don't read as the same color — keep it
> that way by never using coral for warnings or red for highlights.

### 2.3 Where coral goes (and where it doesn't)

Coral is the **warm counterweight** to a cool palette. Use it as a graphic/display accent, sparingly:

**✅ Sanctioned coral uses**
- "New" / "Just added" / "Featured" badges and eyebrow labels where you want energy
- A short coral underline or marker under a single hero word or section number
- Data-viz **third series** (e.g. the R&D lane in the GER contribution diagram)
- Large display numerals or stat call-outs (≥ 24px)
- A coral-wash (`#FBEAE3`) callout/quote block — put **navy** text on it (11:1 contrast)
- A coral variant of the compass-star for a special landing/section header

**🚫 Never coral**
- Body text or any text under ~18px (use `--coral-deep` only if unavoidable)
- Button fills (buttons are navy or ghost-navy — *no* color buttons, ever)
- Status/error states (that's `--alert`)
- More than ~one coral element per viewport — if you see two, remove one

### 2.4 Data-visualization palette

Charts use the three brand families as categorical colors — cool for structure and outcome,
warm to draw the eye. The GER calculator is the reference implementation:

| Series / role | Color | Token |
|---|---|---|
| GTM (investment) | Navy `#002975` | `--navy` |
| Revenue / growth (outcome) | Seafoam-mid teal `#2E9C86` | `--seafoam-mid` |
| R&D — the series to highlight ("the missing half") | Coral `#E8704F` | `--coral` |
| Tier / quality bands | seafoam · amber · coral **tints** | (light tints) |
| Axes, gridlines, reference lines | `#E4E0D6` · `#D6D1C4` · `#6F6A60` | `--line` / `--line-strong` / `--muted` |

Coral marks the one series you want noticed. Chart text is **DM Sans**; big readouts are **Outfit**.
Never reintroduce the old generic data palette (`#3b82f6` / `#10b981` / `#f4683b`).

### 2.5 Usage balance

Think **70 / 20 / 10**: ~70% navy + neutrals (structure and text), ~20% seafoam (tags, active
states, soft panels), ~10% — really less — coral (one highlight per screen). White space is a color too.

### 2.6 Pairings & accessibility (measured on the `#F5F4EF` canvas)

| Combination | Ratio | Verdict |
|---|---|---|
| Navy text on canvas / white | 12–14:1 | ✅ Anything |
| Navy text on seafoam-wash / coral-wash | 11:1 | ✅ Anything — preferred for tinted callouts |
| Coral `#E8704F` on canvas | 2.8:1 | ⚠️ Graphics & ≥24px display only — **not text** |
| Coral-deep `#B14A30` on canvas | 4.9:1 | ✅ AA for normal text (use sparingly) |
| Seafoam `#A3E5D4` as a fill behind navy | — | ✅ Tag/badge background only |

---

## 3. Typography

Three families, one combined Google Fonts import in `<head>`. No serif anywhere except the reader.

| Family | Role | Weights |
|---|---|---|
| **Outfit** | Headings, wordmark, display | 600 / 700 |
| **DM Sans** | Body copy, UI, labels, eyebrows | 400 / 500 / 600 |
| **Source Serif 4** | Long-form reading (`/read` only) | 400 / 500 / 600 |

### 3.1 Scale (as shipped)

| Element | Font | Size / line | Weight | Tracking |
|---|---|---|---|---|
| Wordmark | Outfit | 19px | 600 | -0.01em |
| Home masthead (H1) | Outfit | 42px / 1.05 | 600 | -0.025em |
| Page title (H1) | Outfit | 30px | 600 | -0.02em |
| Section (H2) | Outfit | 21px | 600 | -0.01em |
| Subhead (H3) | Outfit | 15px | 600 | — |
| Eyebrow / kicker | DM Sans | 12px uppercase | 600 | .12–.16em |
| Body | DM Sans | 16px / 1.65 | 400 | — |
| UI / labels | DM Sans | 13–15px | 500–600 | — |
| Reader body | Source Serif 4 | 18px / 1.75 | 400 | — |
| Reader chrome | DM Sans | 13–14px | 400–500 | — |

**Rules of thumb:** headings are tight (negative tracking) and Outfit; eyebrows are uppercase DM Sans
with wide tracking and `--muted` or `--navy`; the serif is *exclusively* for reading long articles.

---

## 4. The nautical motif (use exactly two, sparingly)

1. **Rope rule** — a double hairline: 1px `--line-strong` over 1px `--line`, 3px tall (`.rule`).
   Use it to frame the header and footer, or to separate major sections. Not as a decorative repeat.
2. **Compass star** — a single 8-point star mark (navy by default; coral for special headers).
   Lives in the footer beside the wordmark. One per page, maximum.

That's the entire nautical vocabulary. **No anchors, ropes, boats, waves, knots, or clip-art.**

---

## 5. UI components

- **Buttons** — primary = navy fill; secondary = ghost (navy outline, transparent). Radius 10px.
  Hover deepens to navy-deep. *Color is never a button background.*
- **Inline row-action buttons** — a third, smaller button style for compact per-row actions inside
  a list or card (e.g. "Quick edit" / "Full edit" / "Delete" / "Generate" on a Toolbox card, "Edit
  tags" on the reader). `--muted` text, `--line` border (not navy), radius 6px, ~12px font,
  `padding:3px 10px`. Hover fills `--navy-wash` (`--accent-light`) with `--ink` text. Quieter than
  the primary/secondary pair by design — these sit inside dense rows where a full navy or
  navy-outline button would compete with the row's own content, not label the row's primary action.
- **Inputs** — white surface, `--line` border, radius 10px. Focus = navy border + soft seafoam
  ring `0 0 0 3px rgba(163,229,212,.55)`.
- **Tags / badges** — seafoam fill, navy text, radius 6px, 600 weight, ~11px.
- **Cards** — white surface, `--line` border, radius 12–16px.
- **Tables** — navy header row with white text; alt rows `--surface-2`.
- **Links** — navy; optional seafoam underline for emphasis in editorial copy.

### Radius scale
`10px` buttons & inputs · `12–16px` cards & panels · `6px` tags/chips & inline row-action buttons ·
`999px` filter pills.

### Layout
Reading measure **780px** (820px for the calculator, 860px for the Toolbox grid).
Generous page padding (≈48px top). Whitespace before density.

---

## 6. Do / Don't

| ✅ Do | 🚫 Don't |
|---|---|
| Let navy + off-white do most of the work | Reach for color to fill space |
| Use coral once per screen, as a pop | Spread coral across a layout |
| Keep status colors for status only | Use alert red as a highlight, or coral as a warning |
| Headings in Outfit, reading in Source Serif 4 | Mix the serif into UI, or set body in Outfit |
| One rope rule / one compass star per page | Repeat the motif decoratively |
| Buttons navy or ghost | Make a seafoam or coral button |

---

## 7. Token reference (CSS variables)

<!-- BEGIN GENERATED TOKENS (scripts/generate_brand_docs.py) -->
> Generated from `webapp/app.py`'s `:root` block — don't hand-edit this table.
> To change the brand, edit the CSS, then run `python -m scripts.generate_brand_docs`.

```css
:root{
  /* Surfaces */
  --bg:#F5F4EF;            /* warm off-white page */
  --surface:#FFFFFF;       /* cards, inputs */
  --surface-2:#FAF9F4;     /* subtle alt panels, table stripes */
  /* Brand — Navy (primary, cool). deep · base · light · wash */
  --navy-deep:#001B4F;     /* button hover / depth */
  --navy:#002975;          /* primary base */
  --navy-light:#3F5C9A;    /* lighter navy — secondary accents, borders */
  --navy-wash:#EEF1F7;     /* soft navy fill — chip/ghost hovers */
  --accent:#002975;        /* legacy name now = navy (keeps old markup working) */
  --accent-light:#EEF1F7;  /* legacy name now = --navy-wash */
  /* Brand — Seafoam/Green (cool accent). deep · mid · base · wash */
  --seafoam-deep:#1F7A66;  /* deepest teal — text-capable on light (AA) */
  --seafoam-mid:#2E9C86;   /* mid teal — data-viz (legible as a fill/line) */
  --seafoam:#A3E5D4;       /* accent base (light mint) — tags, badges, underline */
  --seafoam-wash:#EAF7F2;  /* soft accent fill — calc readout, table accents */
  /* Brand — Coral (warm accent, rare). deep · base · light · wash */
  --coral-deep:#B14A30;    /* coral that must carry small text (AA on canvas) */
  --coral:#E8704F;         /* warm accent base — display pop, data-viz R&D series */
  --coral-light:#F4A98F;   /* lighter coral — soft highlights */
  --coral-wash:#FBEAE3;    /* soft coral fill — callouts (navy text) */
  /* Text */
  --ink:#1a1a1a;
  --ink-soft:#3a3833;
  --muted:#6F6A60;         /* warm mid-gray */
  /* Lines (warm-toned) */
  --line:#E4E0D6;
  --line-strong:#D6D1C4;
  /* Semantic — GER calculator readout only */
  --good:#002975; --caution:#9A6B12; --alert:#9E3B30;
  /* Type */
  --font-head:'Outfit',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-body:'DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
}
```
<!-- END GENERATED TOKENS -->

This token block is wired into the live `_CSS` in `webapp/app.py`. Coral is in use on the GER charts
(the data-viz R&D series). The automated brand check in `tests/test_brand_standards.py` keeps the
palette and fonts honest — any new color or font outside this system fails CI (see §8).

---

## 8. Automated enforcement

Two test suites run on every push/PR via GitHub Actions (`.github/workflows/qa.yml`):
`tests/test_brand_standards.py` (visual) and `tests/test_voice_standards.py` (verbal — see §9).
`test_brand_standards.py` scans the rendered site (`webapp/app.py`, including the inline CSS, SVG
charts, and JS-built markup) and fails if new content drifts off-brand:

- **Fonts** — only Outfit, DM Sans, Source Serif 4, and system/generic fallbacks may appear. Inter,
  Lora, Arial, Helvetica, Times, Roboto, etc. are banned (this is the exact class of regression that
  slipped in before the refresh).
- **Colors** — every hex in the codebase must be a brand token (parsed from the `:root` above, so the
  palette is its single source of truth) or one of the explicitly-documented auxiliary colors
  (status/feedback, benchmark badges, chart tints). A brand-new off-palette hex fails the build,
  forcing a deliberate choice: add it to the system or fix it.
- **Banned legacy colors** — the specific values purged in the refresh (old greens, the generic
  `#3b82f6`/`#10b981`/`#f4683b` data palette) can never reappear.
- **Token integrity** — the full token set (all three ramps + neutrals + semantic) must be present.

To run locally: `pip install -r requirements-dev.txt && pytest -q`.

When you intentionally introduce a new color (e.g. a new chart series or status state), add it to the
relevant group in `AUX_COLORS` in `linklib/brand_check.py` (the single source of truth for the rules,
also surfaced live on the **Checks** admin page) with a comment — that's the moment the decision gets
recorded, which is the point.

---

## 9. Verbal identity — voice

The brand is verbal as well as visual. The full voice guide (lead with the point, specific over
abstract, first-person proof, earned metaphors, the LinkedIn shape, and the hard mechanical rules) is
the editable **Voice guide** on `/admin/voice` — it's both what Claude uses to draft posts and the
rubric the voice check holds new writing to.

Like color, voice has two kinds of rules:

- **Mechanical** (deterministic) — banned buzzwords (*delve, robust, seamless, synergy, transformative,
  game-changer*), filler (*"at the end of the day", "in order to", "needless to say"*), and performative
  openers/closers (*"thrilled to", "Onward!", "excited for what's next"*). `tests/test_voice_standards.py`
  scans the site copy in `webapp/app.py` for these and fails the build on a hit. The rules live in
  `linklib/voice_review.py` (`BANNED_WORDS` / `FILLER_PHRASES` / `PERFORMATIVE`) as the single source
  of truth. Context-dependent words (*leverage* the noun, *actually*/*honestly* as filler) are left to
  the holistic review to avoid false positives.
- **Tone** (judgment) — "does this sound like me." Reviewed on demand by Claude, never in CI (it costs
  API and isn't deterministic). Use the **Check content against your voice** box on `/admin/voice`, or
  the CLI: `python -m scripts.voice_review draft.md` (reads a file or stdin; exits non-zero on any
  mechanical violation, so it can gate a pre-publish script).
