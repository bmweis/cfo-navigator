"""A real FP&A Buddy question and answer, shown as the example on /tools/fpa-buddy.

A hardcoded snapshot, on purpose: it is the production conversation (ask_questions
row 5, read 2026-10-04: first turn, rated helpful, not hidden, not private), pasted
verbatim. It is never read live and has no setting, so hiding, privatizing or
deleting that row can't silently remove the example from the page. Do not edit,
shorten or reflow the text; tests/test_buddy_real_example.py pins it.
"""
import json

QUESTION = """What is a framework other software and SaaS businesses use to measure the effectiveness of R&D investments? It would be especially helpful to know how those frameworks have changed with AI."""

ANSWER = """The two most useful frameworks in the library here are a benchmark-and-ROI model for general R&D, and a Growth Engine Ratio that ties R&D to GTM outcomes. AI is starting to reshape both.

---

**General R&D effectiveness: benchmark + roadmap ROI + team performance**

The practical approach is three steps: (1) use R&D benchmarks from comparable private and public companies to ballpark whether overall spend is in range; (2) map each dollar of spend to a specific roadmap initiative with an expected ROI and timeline; and (3) performance-manage product and engineering teams as leading indicators, watching for missed timelines and overshot budgets.[1]

The most common portfolio heuristic—70% core product, 20% adjacent features, 10% moonshots—sounds clean but has real limits. That 70–20–10 rule is critiqued as too generalizable: Netflix's pivot to streaming, Meta's Reality Labs (multi-billion, 10+ year payback), and Microsoft's o365 Copilot (under 12 months to payback) all required allocation decisions that don't fit a tidy formula.[1]

A more decision-useful lens: classify R&D bets on a 2x2 as offensive vs. defensive and short- vs. long-term.[1] That forces explicit conversations about *why* you're spending, not just *how much*.

---

**Connecting R&D to GTM: the Growth Engine Ratio**

The benchmark approach tells you if spend is in range. It doesn't tell you if the product is actually moving the business. CAC payback, Magic Number, and similar GTM-efficiency metrics measure only half the picture—R&D consumes 20–30% of revenue at scale but its contribution to growth is inferred, not measured. The Growth Engine Ratio Framework addresses this by evaluating product and GTM investment together as two reinforcing engines, so finance leaders can separate "did the sales motion improve?" from "did the product get easier to sell?"[2]

Signals of strong product–GTM alignment include shortening sales cycles, organic expansion without heavy CS intervention, and reduced retention risk. Weakness shows up as product releases that don't move conversion or expansion, spend rising faster than ARR, and product and GTM leaders disagreeing on where the bottleneck is.[2]

Read efficiency as a trend across multiple quarters—a single-quarter spike is noise.[2]

---

**How AI is changing this**

The main change is that AI is compressing time horizons and making the offensive/defensive 2x2 more consequential. The core discipline—start with the business problem, not the technology, and target narrow, bounded use cases that show results in 30–90 days—keeps the ROI clock manageable.[3]

On unit economics, the picture is temporarily messy but improving. AI companies scale faster than traditional SaaS because model costs (cost per token) are dropping faster than cloud costs ever did, which means current weak gross margins and the "AI tax" on unit economics are likely temporary.[4]

The build-vs-buy decision now carries real cost differences that should anchor your roadmap ROI mapping: building custom AI runs $500K–$2M+ over 6–12 months; buying platforms runs $50K–$500K over 1–3 months; a hybrid approach (foundation models with fine-tuning) lands at $200K–$1M over 3–6 months.[3]

And expect a lag before you can measure anything. Most AI projects need 3–6 months before showing value, following an S-curve adoption pattern—so budget 2–3x initial estimates and frame it as capital investment that amortizes over years, not a quarterly expense.[3]

---

**The practical implication**

The frameworks themselves haven't been replaced by AI—benchmarks still matter, roadmap ROI mapping still matters, and tying product investment to GTM outcomes still matters. What AI changes is the time-to-value assumptions inside those frameworks and the capital required at each decision point. If your current R&D ROI model assumes a 12–18 month feedback loop, it's probably too slow for AI initiatives.

One question worth clarifying: are you evaluating R&D effectiveness for traditional product work, AI-specific investments, or both? The answer shapes which of these frameworks deserves the most weight."""

# All four are library articles, so they render with the Curated archive icon.
CITATIONS = [
    {"n": 1, "type": "library", "title": 'How to Think of R&D Spend', "url": 'https://a16z.com/how-to-think-of-rd-spend/'},
    {"n": 2, "type": "library", "title": 'Going Beyond CAC Payback: The Framework for Measuring… | The F Suite', "url": 'https://fsuite.co/blog/growth-engine-ratio'},
    {"n": 3, "type": "library", "title": "A Founder's Guide to AI Implementation | Tomasz Tunguz", "url": 'https://www.tomtunguz.com/ai-implementation-guide/'},
    {"n": 4, "type": "library", "title": 'The Redpoint Ventures Playbook: How Top VCs Are Really Investing in AI Applications (And What It Means for Your SaaS Strategy) | SaaStrAI', "url": 'https://saastr.com/the-redpoint-ventures-playbook-how-top-vcs-are-really-investing-in-ai-applications-and-what-it-means-for-your-saas-strategy'},
]

CITATIONS_JSON = json.dumps(CITATIONS)
