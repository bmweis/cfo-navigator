"""Structured content for the /thought-leadership page's four editorial lists
(Writing, Speaking & Events, Podcasts, Press).

Deliberately plain Python data, not a DB table: this is small (~35 items),
hand-curated by Brian/Claude Code, and changes a few times a quarter at most
— the same category as the three featured cards in webapp/app.py, which stay
untouched. Keeping it here (rather than in library.db, which isn't in git and
isn't provisioned on a fresh checkout) means every edit is a normal,
diffable, PR-reviewable code change, consistent with how the rest of the
site's copy is authored.

`description` is a synopsis, 1-2 sentences, in Brian's voice (see
linklib/social.py:BRIAN_VOICE_CORE). Where it's empty, `needs_synopsis` is
True and the page renders an explicit "Synopsis pending" placeholder instead
of silently showing nothing or inventing content — items land in that state
because their source couldn't be read yet (blocked fetch, invite-only event
with no page, dead link), never because a description was skipped by
accident.
"""
from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TLPhoto:
    src: str
    alt: str


@dataclass
class TLItem:
    title: str
    url: str              # "" renders as unlinked plain text (e.g. invite-only events)
    venue: str             # publication/host org; "" if self-published
    date_label: str        # display string, e.g. "Jun 2026"; "" if undated
    sort_key: str          # "YYYY-MM"; "" floats to the top of its section
    type: str              # "writing" | "speaking" | "podcast" | "press"
    description: str = ""
    needs_synopsis: bool = False
    photos: list[TLPhoto] = field(default_factory=list)
    photo_caption: str = ""


WRITING: list[TLItem] = [
    TLItem(
        title="The Growth Engine Ratio: Accounting for the Missing Half of Your Efficiency Equation",
        url="/growth-engine-ratio", venue="The F Suite", date_label="Jun 2026", sort_key="2026-06",
        type="writing",
        description="A metric for how R&D and GTM investment work together to drive growth—with an interactive calculator.",
    ),
    TLItem(
        title="Sail, Don't Row: A Playbook for Running an AI Hackathon With Your Finance Team",
        url="/finops-ai-hackathon", venue="", date_label="Jun 2026", sort_key="2026-06",
        type="writing",
        description="How to run an AI hackathon with your finance team—the full format, facilitation mechanics, and how to make it stick.",
    ),
    TLItem(
        title="Connecting Claude to NetSuite: A Setup Guide for Finance Teams",
        url="/netsuite-mcp", venue="", date_label="Jun 2026", sort_key="2026-06",
        type="writing",
        description="End-to-end setup for the two-role OAuth architecture—what it is, why it's secure, and how to use it.",
    ),
    TLItem(
        title="Exit Readiness for CFOs",
        url="https://www.fsuite.co/blog/exit-readiness-cfos", venue="The F Suite",
        date_label="Mar 2026", sort_key="2026-03", type="writing",
        description="There's no single right exit—IPO, acquisition, sponsor sale, secondary—so the real job is building a company that's ready no matter which door opens, starting years before any process does.",
    ),
    TLItem(
        title="Building Dashboards That Matter",
        url="https://www.onlycfo.io/p/building-dashboards-that-matter", venue="OnlyCFO",
        date_label="Apr 2024", sort_key="2024-04", type="writing",
        description="Stacking metrics isn't the same as building a dashboard that matters—the harder, more valuable work is the cross-functional alignment on what the business needs to see.",
    ),
]

SPEAKING: list[TLItem] = [
    TLItem(
        title="Abacum AI Summit — Recording",
        url="https://www.youtube.com/watch?v=MDBz0OpR1II", venue="Abacum",
        date_label="Apr 2026", sort_key="2026-04", type="speaking",
        description="A panel on what AI adoption changes inside a finance team once the pilot phase ends, recorded live at Abacum's invite-only summit in New York.",
        photos=[
            TLPhoto(src="/static/speaking-close.jpg",
                    alt="Brian Weisberg speaking at the Abacum AI Summit, April 2026"),
            TLPhoto(src="/static/speaking-wide.jpg",
                    alt="Panel discussion at the Abacum AI Summit, April 2026"),
        ],
        photo_caption="Abacum AI Summit · New York · April 2026",
    ),
    TLItem(
        title="Beyond the Spreadsheet: What FP&A Platforms Need to Deliver in an AI-First Era (Webinar Panel)",
        url="https://www.abacum.ai/webinars/beyond-the-spreadsheet-what-fp-a-platforms-need-to-deliver-in-an-ai-first-era",
        venue="Abacum", date_label="Jun 2026", sort_key="2026-06", type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="Claude in Action for Finance (Virtual Panel)",
        url="https://fsuitevirtualpanel430.splashthat.com", venue="The F Suite",
        date_label="Apr 2026", sort_key="2026-04", type="speaking",
        description="A working session, not a demo reel: how finance teams are using Claude to run multi-step workflows end to end, including a live example completed across systems from a single prompt.",
    ),
    TLItem(
        title="Growth CFO Salon (Boston)",
        url="", venue="The F Suite", date_label="Nov 2025", sort_key="2025-11",
        type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="Cash Cycle Demo Day — Opening & Closing Remarks",
        url="https://cashcycledemoday.splashthat.com/", venue="The F Suite",
        date_label="Oct 2025", sort_key="2025-10", type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="CFO Supper Club (Boston)",
        url="", venue="The F Suite", date_label="Aug 2025", sort_key="2025-08",
        type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="CFO Roundtable — M&A and Managing Uncertainty",
        url="https://luma.com/7fwtr2n8", venue="Fidelity",
        date_label="Mar 2025", sort_key="2025-03", type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="Lean Accounting Team (Webinar Host)",
        url="https://numeric.lpages.co/lean-accounting-team-webinar/", venue="Numeric",
        date_label="Mar 2024", sort_key="2024-03", type="speaking",
        description="Hosted a conversation with Crafty Apes CFO Leo Dencik and Numeric's Connor Foran on how lean teams cover both accounting and FP&A without adding headcount, and why spreading close work across the month beats cramming it into five days.",
    ),
    TLItem(
        title="Private Dinner & Guided Discussion (Boston)",
        url="", venue="The F Suite", date_label="Apr 2024", sort_key="2024-04",
        type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="CFO Dinner (Boston)",
        url="", venue="The F Suite", date_label="Dec 2023", sort_key="2023-12",
        type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="NC Launch Dinner",
        url="", venue="The F Suite", date_label="Jun 2023", sort_key="2023-06",
        type="speaking", needs_synopsis=True,
    ),
    TLItem(
        title="Agile Finance Summit",
        url="https://www.accelevents.com/e/agile-finance-summit-2021", venue="Teampay",
        date_label="Oct 2021", sort_key="2021-10", type="speaking",
        description="Teampay's second annual Agile Finance Summit—two days on overseeing company money and building a durable financial strategy, alongside finance tool makers like Mosaic and Tesorio.",
    ),
]

PODCASTS: list[TLItem] = [
    TLItem(
        title="The Cash Flow Show — Conversations About How Tech Companies Make Money (Host · Full Episode Feed)",
        url="https://www.onlycfo.io/podcast", venue="OnlyCFO", date_label="", sort_key="",
        type="podcast",
        description="Brian's own interview series, hosted via OnlyCFO—operators on how their companies make money, in their own words. Full episode archive.",
    ),
    TLItem(
        title="Adopting AI in Finance & Accounting — with Sowmya Ranganathan (former Controller, OpenAI)",
        url="https://open.spotify.com/episode/6uXkeypUPX5g5yB8lHGq2V", venue="The Cash Flow Show",
        date_label="Aug 2025", sort_key="2025-08", type="podcast",
        description="Sowmya scaled finance at Square, Rippling, and OpenAI before founding Lumera—we get into how OpenAI rolled out AI in finance, including running internal hackathons instead of mandating tools from the top down.",
    ),
    TLItem(
        title="State of Fundraising / Equity Market",
        url="https://open.spotify.com/episode/0rSm42OSNRzjiG3cYye3tX", venue="The Cash Flow Show",
        date_label="Jun 2025", sort_key="2025-06", type="podcast", needs_synopsis=True,
    ),
    TLItem(
        title="Is ARR Dead?",
        url="https://open.spotify.com/episode/5G0GUaRrOeGXxJwFh9WsPw", venue="The Cash Flow Show",
        date_label="Jun 2025", sort_key="2025-06", type="podcast",
        description="ARR stopped being a clean metric once companies started blending usage-based and non-traditional revenue into it—we dig into what's worth tracking instead.",
    ),
    TLItem(
        title="Commission Plan Strategies in 2025 — with Meir Rotenberg & David Ma",
        url="https://open.spotify.com/episode/64DsKmsDgOshd3LQdM4Cte", venue="The Cash Flow Show",
        date_label="Mar 2025", sort_key="2025-03", type="podcast", needs_synopsis=True,
    ),
    TLItem(
        title="The M&A Playbook",
        url="https://open.spotify.com/episode/5kMa3kkutDhsoc4SBoOBZ1", venue="The Cash Flow Show",
        date_label="Mar 2025", sort_key="2025-03", type="podcast", needs_synopsis=True,
    ),
    TLItem(
        title="Code to Cash, Ep. 9 — Monetizing Thoughtfully: Architecting Financial Stacks (Guest)",
        url="https://creators.spotify.com/pod/profile/codetocash/episodes/Episode-9-Monetizing-Thoughtfully--Architecting-Financial-Stacks-with-Brian-Weisberg--CFO-of-Tidelift-e28unsn",
        venue="Monetizely", date_label="Sep 2023", sort_key="2023-09", type="podcast", needs_synopsis=True,
    ),
    TLItem(
        title="SaaS Conversations: Dynamic Planning for SaaS Finance Leaders (Guest)",
        url="https://www.opexengine.com/webinar/opexengine-saas-conversations-dynamic-planning-for-saas-finance-leaders",
        venue="OpexEngine", date_label="May 2023", sort_key="2023-05", type="podcast",
        description="A conversation with CoreWeave's Evan Meagher on dynamic planning, and how benchmarking against peers cuts risk out of the operating plan.",
    ),
    TLItem(
        title="The Heuristics of Forecasting (Guest)",
        url="https://www.youtube.com/watch?v=mqVvcVVTSrk", venue="Role Forward Podcast · Mosaic Tech",
        date_label="Dec 2022", sort_key="2022-12", type="podcast",
        description="Heuristics for headcount planning specifically: being realistic about what a forecast can predict, and why finance needs to invest in the function earlier than feels comfortable.",
    ),
    TLItem(
        title="Collaborative Budgeting (Guest)",
        url="https://www.youtube.com/watch?v=GPdRstJ_sKw", venue="Role Forward Podcast · Mosaic Tech",
        date_label="Apr 2022", sort_key="2022-04", type="podcast",
        description="Why a finance leader's real job in budgeting is less about the model and more about knitting the whole business together around it.",
    ),
]

PRESS: list[TLItem] = [
    TLItem(
        title="From $1M to $100M: 6 Finance Lessons from the Frontline",
        url="https://www.sequencehq.com/blog/from-1m-to-100m-6-finance-lessons-from-the-frontline",
        venue="Sequence", date_label="Jul 2025", sort_key="2025-07", type="press",
        description="Six lessons from 15+ years scaling tech companies through two exits—starting with the one metric worth tracking above all others early on: ARR per employee.",
    ),
    TLItem(
        title="GC/CFO Collaboration",
        url="https://www.legaldive.com/news/gc-cfo-collaboration-svb-techgc-the-f-suite-silicon-valley-bank/646561/",
        venue="LegalDive", date_label="Mar 2023", sort_key="2023-03", type="press",
        description="How the F Suite mobilized over 200 CFOs onto a call within a day of Silicon Valley Bank's collapse, and coordinated with sister legal community TechGC to keep finance and legal leaders moving together through the crisis.",
    ),
    TLItem(
        title="When and How to Scale Your Accounting Department",
        url="https://www.numeric.io/blog/when-and-how-to-scale-your-accounting-department",
        venue="Numeric", date_label="Nov 2023", sort_key="2023-11", type="press",
        description="The case for building good documentation habits before they're painful to build, plus how to time new hires so you're neither churning your team nor over-staffing ahead of need.",
    ),
    TLItem(
        title="Startup CFO Primer",
        url="https://www.numeric.io/blog/startup-cfo-primer",
        venue="Numeric", date_label="Jun 2024", sort_key="2024-06", type="press",
        description="Why documentation is the multiplier for a lean team: Loom walkthroughs of the close process and numbered checklists that cut onboarding time without adding headcount.",
    ),
    TLItem(
        title="Innovative Cost-Saving Measures (Q&A)",
        url="https://cfodrive.com/qa/what-innovative-cost-saving-measures-can-significantly-impact-a-companys-bottom-line/",
        venue="CFO Drive", date_label="Jul 2024", sort_key="2024-07", type="press", needs_synopsis=True,
    ),
]

SECTIONS: list[tuple[str, str, list[TLItem]]] = [
    ("Writing", "📝", WRITING),
    ("Speaking & Events", "🎤", SPEAKING),
    ("Podcasts", "🎧", PODCASTS),
    ("Press", "📰", PRESS),
]
