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

### 2.1 The three brand colors

| Role | Name | Hex | Notes |
|---|---|---|---|
| **Primary** | Navy | `#002975` | Wordmark, headings accents, links, buttons, structure |
| | Navy-deep | `#001B4F` | Button hover, depth |
| **Cool accent** | Seafoam | `#A3E5D4` | Tags, badges, active-nav underline, calculator accents |
| | Seafoam-wash | `#EAF7F2` | Soft fills (readout panels, callouts) |
| **Warm accent (NEW)** | **Coral** | **`#E8704F`** | The rare 10% pop — see §2.3 for exactly where |
| | **Coral-wash** | **`#FBEAE3`** | Soft coral tint fills (parallels seafoam-wash) |
| | **Coral-deep** | **`#B14A30`** | *Only* when coral must carry small text (AA on canvas) |

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

### 2.4 Usage balance

Think **70 / 20 / 10**: ~70% navy + neutrals (structure and text), ~20% seafoam (tags, active
states, soft panels), ~10% — really less — coral (one highlight per screen). White space is a color too.

### 2.5 Pairings & accessibility (measured on the `#F5F4EF` canvas)

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
- **Inputs** — white surface, `--line` border, radius 10px. Focus = navy border + soft seafoam
  ring `0 0 0 3px rgba(163,229,212,.55)`.
- **Tags / badges** — seafoam fill, navy text, radius 6px, 600 weight, ~11px.
- **Cards** — white surface, `--line` border, radius 12–16px.
- **Tables** — navy header row with white text; alt rows `--surface-2`.
- **Links** — navy; optional seafoam underline for emphasis in editorial copy.

### Radius scale
`10px` buttons & inputs · `12–16px` cards & panels · `6px` tags/chips · `999px` filter pills.

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

```css
:root{
  /* Surfaces */
  --bg:#F5F4EF; --surface:#FFFFFF; --surface-2:#FAF9F4;
  /* Brand — cool */
  --navy:#002975; --navy-deep:#001B4F; --accent:#002975; /* legacy alias = navy */
  --seafoam:#A3E5D4; --seafoam-wash:#EAF7F2; --accent-light:#EEF1F7;
  /* Brand — warm (NEW) */
  --coral:#E8704F; --coral-wash:#FBEAE3; --coral-deep:#B14A30;
  /* Text */
  --ink:#1a1a1a; --ink-soft:#3a3833; --muted:#6F6A60;
  /* Lines */
  --line:#E4E0D6; --line-strong:#D6D1C4;
  /* Semantic — status only */
  --good:#002975; --caution:#9A6B12; --alert:#9E3B30;
  /* Type */
  --font-head:'Outfit',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-body:'DM Sans',system-ui,-apple-system,'Segoe UI',sans-serif;
  --font-read:'Source Serif 4',Georgia,serif;
}
```

The three `--coral*` tokens are **new** — not yet wired into the live `_CSS` in `webapp/app.py`.
Adding them is a one-line change; introducing coral into actual UI should be done deliberately,
one placement at a time, per §2.3.
