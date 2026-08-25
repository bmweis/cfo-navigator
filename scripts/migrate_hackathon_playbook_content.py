#!/usr/bin/env python3
"""One-time data migration (Original Content Phase 4b): sets body_md on the
existing `ai-hackathon-playbook` original_content row, so it can be served
through the shared article template (GET /thought-leadership/{slug}) instead
of the bespoke Python route. See CLAUDE.md's Original Content Phase 4b entry
for the full reasoning — same pattern as Phase 4a's
migrate_netsuite_mcp_content.py.

Copy is extracted VERBATIM from the retired webapp/app.py
`finops_ai_hackathon()` route — no rewriting, no paraphrasing. Structure
only changed: prose/headings/plain lists became real markdown; the
visually-designed elements (the case-study grid, the resource-links list,
both phase tracks, the 2x2 matrix, the tier strip, the flowchart, the
Notion template box) are preserved as raw HTML blocks using their existing
CSS classes, which this same PR moves into the shared `.oc-body`-scoped CSS
(`_OC_HACKATHON_CSS` in webapp/app.py) so they render correctly once served
through the shared template. Deliberately NOT ported: the `.fah-verdicts`/
`.fah-verdict`/`.fah-v-*`, `.fah-pull`, and `.fah-motif` CSS rules — all
confirmed dead in the source page (defined, never used by any element in
its body).

Also sets date_label to "June 2026" (derives sort_key from it) — same
visual-parity fix as Phase 4a, restoring the byline the bespoke page always
showed (blank since the Phase 1 seed, since _TL_FEATURED_CARDS tuples never
carried a date_label).

Also fixes a real bug found during this port's own verification: the row's
`title` was seeded (Phase 1) as the literal string "Sail, Don&rsquo;t Row" —
copied verbatim from _TL_FEATURED_CARDS, whose tuple pre-escapes its title
for RAW insertion by _tl_fcard() (the flagship-card renderer never calls
_esc() on title). But _original_content_article_body() — built in Phase 2,
for the article page's <h1> — DOES call _esc(row["title"]), correctly
treating title as plain text that needs escaping at render time (the right
behavior for an admin-typed title via the Phase 3 CRUD form). The combination
double-escapes this one row's pre-escaped title into a literal, visible
"Sail, Don&rsquo;t Row" in the ported page's <h1> — never seen before because
body_md was NULL until this migration, so the article route never rendered
for this slug. Same root-cause class as the "Speaking &amp; Events" and
edit-page-title double-escape bugs documented elsewhere in this repo's
history — the data was pre-escaped for a raw-insertion call site, and a
second call site correctly escaping plain text collided with that. Fixed
here by setting TITLE to the plain, unescaped string, matching the retired
bespoke page's own actual <h1> text verbatim ("Sail, Don't Row", straight
apostrophe — confirmed against the source, not the curly &rsquo; used
elsewhere on the site). This also changes what the homepage/thought-leadership
flagship card shows for this piece (straight apostrophe instead of curly,
since _tl_fcard() renders title raw) — a small, deliberate, documented
side effect of fixing the underlying bad data, not a separate content change.

This port's own screenshot-diff verification also caught two CSS specificity
bugs in _OC_HACKATHON_CSS (webapp/app.py) — an inheritance gap on
.fah-body/.fah-template h3 line-height, and a specificity-tie regression on
.fah-body/.fah-tier p line-height/margin-bottom against the sitewide
.tool-prose p rule — neither of which touches this script or the DB; see
_OC_HACKATHON_CSS's own comment block and CLAUDE.md's Phase 4b entry for
the full explanation.

Deliberately a manual, run-by-hand script — NOT wired into an automatic boot
hook, same standing rule as every other production DATA write in this repo.
Safe by default (preview only, no writes) — same --apply convention as
scripts/migrate_netsuite_mcp_content.py.

Idempotent: guarded by checking whether the row's body_md already matches
what this script would set — a second run reports "already applied" and
does nothing.

Per the write-then-read-back standing practice, an --apply run re-reads the
row afterward and confirms body_md/date_label/sort_key match what was set.

Usage:
    python -m scripts.migrate_hackathon_playbook_content --db library.db            # preview
    python -m scripts.migrate_hackathon_playbook_content --db library.db --apply     # write for real
"""
from __future__ import annotations

import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
from linklib.db import Library, resolve_db_path

SLUG = "ai-hackathon-playbook"
DATE_LABEL = "June 2026"
# Plain, unescaped — matches the retired bespoke page's own <h1> text
# verbatim. See the module docstring's "double-escape" section for why the
# Phase-1-seeded title (pre-escaped for a different call site) is wrong here.
TITLE = "Sail, Don't Row"

# Verbatim copy from the retired finops_ai_hackathon() route. Prose/headings/
# plain lists are real markdown; the designed elements (case-study grid,
# resource list, phase tracks, the 2x2 matrix, tier strip, flowchart,
# Notion template box) are raw HTML blocks using their original .fah-*
# classes, unchanged. The closing bio blurb preserves the "Sail, Don't Row
# is also a game" easter-egg link to /play verbatim.
BODY_MD = '''<p style="font-size:17px;font-style:italic;color:var(--ink-soft);margin:0 0 6px;line-height:1.5;">A playbook for running an AI hackathon with your finance team</p>

There are two ways to approach the AI moment in finance. The first is to row harder: one-off solutions, manual handoffs, each person finding their own tool at their own pace. Exhausting. Doesn't scale. The second is to sail: build the infrastructure deliberately, rig it carefully, and let the conditions do the work. The difference isn't capability. It's intention.

A hackathon is how a finance team learns to sail. It creates protected time and a low-stakes space to learn something hard together—as a team, where nobody has to already know the answer. The builds you ship at the end are real, but they're a byproduct. The point is the skill that stays when everyone goes home.

I've run one of these with my own finance and ops team, and this is the format distilled—what worked, why it worked, and how to run it yourself.

<div class="article-callout fah-callout">
  <div class="article-callout-title">Before any piece of work, two questions</div>
  <ol>
    <li>Is this worth doing?</li>
    <li>And am I sailing or rowing?</li>
  </ol>
  <p style="margin-top:10px;">Rowing isn't inherently bad. The point is being intentional about when you go manual and when you build something repeatable. Ad-hoc has a way of becoming permanent ad-hoc.</p>
</div>

## Why a hackathon—and why now

AI adoption in finance doesn't happen on its own. It gets crowded out by the close, the board deck, the forecast update. There's always something more urgent. Left to find the time on their own, most teams never do.

A hackathon fixes that by force. It carves out protected time and makes *exploring together* the actual assignment. The format works for three reasons:

- **Psychological safety.** When everyone is learning at the same time, in the same room, there's no expert to defer to and no reason to hide. Half-formed ideas get air.
- **Time-boxing as a feature.** The constraint—ninety minutes to build something shippable—focuses effort better than a two-week sprint with no end in sight. Done is better than perfect.
- **Compounding returns.** A team that has learned something together learns faster next time. The first hackathon is the hardest. Run it annually and it becomes a flywheel.

The goal isn't to automate the whole finance function. It's to close the gap between your team's potential and its current velocity—on purpose, together, in a way that compounds.

## The method behind it: design thinking

Before the mechanics, the philosophy. The prioritization format I use: post-its, dot stickers, a 2×2. It isn't a team-building exercise. It's the application of a specific method: design thinking.

Design thinking is a problem-solving approach that starts with the people experiencing the problem, not with the solution. It works in two modes:

- Diverge first. Everyone generates ideas independently, without talking.
- Then converge.

The separation matters—if you skip the silent step and just go around the room, the first voice anchors every other answer.

The other half is the ground rule going in: **no bad ideas.** No judgment, no evaluating while generating. The only requirement is a clear persona and use case: a real person with a real problem, not a vague wish. That's what makes people comfortable putting the half-formed thing on the wall—which is exactly where the good ones tend to start.

This method started in product design but works anywhere you need a group to surface and prioritize ideas without the usual political drag. A few examples of what it looks like at scale:

<div style="display:grid;grid-template-columns:1fr 1fr 1fr;gap:1px;background:var(--line-strong);border:1px solid var(--line-strong);border-radius:10px;overflow:hidden;margin:20px 0;">
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:600 11.5px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Airbnb</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">Early growth</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Bookings were flat. They visited hosts, looked at listings, and realized photos were terrible. One non-technical intervention. The insight came from observing the problem directly.</p>
  </div>
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:600 11.5px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">IBM</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">Enterprise shift</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Flipped the order: start with what the customer needs, then figure out the technology. Built internal design studios. Retrained thousands. Outputs improved. So did relationships.</p>
  </div>
  <div style="background:#fff;padding:18px 16px;">
    <div style="font:600 11.5px var(--font-body);letter-spacing:.1em;text-transform:uppercase;color:var(--muted);margin-bottom:6px;">Google</div>
    <div style="font-family:var(--font-head);font-size:15px;font-weight:600;color:var(--ink);margin-bottom:8px;">20% rule</div>
    <p style="font-size:13px;color:var(--ink-soft);margin:0;line-height:1.5;">Structured diverge time with real stakes attached. Gmail, Google News, and AdSense all started there. The roadmap never would have produced them.</p>
  </div>
</div>

<p style="font-size:14px;color:var(--muted);margin:0 0 8px;">Three resources worth sending as pre-reading before you run this:</p>
<div class="fah-resources">
  <a href="https://www.nngroup.com/articles/design-thinking/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">📖</span>
    <div><div class="fah-r-title">Design Thinking 101</div><p class="fah-r-desc">Covers the six phases and why this isn't just a brainstorming session with a fancier name.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://www.nngroup.com/articles/diverge-converge/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">🔀</span>
    <div><div class="fah-r-title">The Diverge-and-Converge Technique</div><p class="fah-r-desc">Why you split ideation from prioritization, and what goes wrong when you don't.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://www.nngroup.com/articles/dot-voting/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">🟢</span>
    <div><div class="fah-r-title">Dot Voting</div><p class="fah-r-desc">How many dots to give, when to rerun a vote, and the failure modes to watch for—especially the person who campaigns out loud before the stickers go up.</p><div class="fah-r-src">Nielsen Norman Group</div></div>
  </a>
  <a href="https://designthinking.ideo.com/" target="_blank" rel="noopener" class="fah-resource">
    <span class="fah-r-icon">💡</span>
    <div><div class="fah-r-title">IDEO Design Thinking</div><p class="fah-r-desc">The original source—where the method came from, with toolkits and examples across industries.</p><div class="fah-r-src">IDEO</div></div>
  </a>
</div>

## Before you arrive: the setup

The single biggest mistake in running a hackathon is walking into the room cold. If the first thing you do is ask "so what should we build?"—you'll spend half your time generating half-baked ideas and the other half convincing people to try something. Do the intake work before you're in a room together.

<div class="article-callout fah-callout">
  <div class="article-callout-title">Async intake—1–2 weeks before the event</div>
  <ul>
    <li>Set up a simple intake form (Notion works well) with these fields: <strong>problem statement</strong> (one sentence), <strong>who it hurts</strong>, <strong>type</strong> (automation / visibility / missing skill / analysis), <strong>impact and feasibility</strong> (a first guess), and <strong>definition of done</strong>.</li>
    <li>Ask specific questions. <em>"What do you do every week that feels like copy-paste?"</em> gets better answers than <em>"What problems do you have?"</em></li>
    <li>Make it frictionless. Let people dump free text if that's easier—you or an AI agent can structure it afterward. The goal is honest input, not a polished pitch.</li>
    <li>Optional: use AI to auto-fill the structured fields from each submission, then have people review and correct. That task itself is a small AI adoption moment.</li>
  </ul>
</div>

Also before the event: get your integrations connected. If you're building with Claude or another AI assistant, make sure it's linked to the systems you actually use—NetSuite, Notion, Google Drive, Slack. Spending build time on setup is demoralizing. Arrive ready to build.

Consider sending the design thinking pre-reads (linked above) a few days out. Not required, but teams that arrive with the method in their heads move faster once they're in the room.

## Day zero: from problems to priorities

This is the framing session—the day (or half-day) before the build. Its job is to turn a backlog of submitted problems into a ranked shortlist of sprint candidates. Here's the full sequence:

<div class="fah-track">
  <div class="fah-step">
    <div class="fah-num">1</div>
    <div class="fah-body">
      <h3>Transcribe to post-its</h3>
      <p>During a break, the facilitator writes each submitted problem onto a physical sticky note—one problem per note. This forces a human edit pass. You spot duplicates, catch problems that are really the same thing framed twice, and produce something the whole room can see simultaneously. Keep laptops closed for the rest of this session.</p>
      <span class="fah-tag">⏱ 15–20 min · facilitator only · done during a break</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">2</div>
    <div class="fah-body">
      <h3>Live additions + clustering</h3>
      <p>Give the room a few minutes to add anything not submitted async. Then cluster: pull duplicate or overlapping stickies together before voting. Facilitator-led, fast—group what's obviously similar and move on. Don't debate the clusters. Debate costs time and anchors thinking before the vote.</p>
      <span class="fah-tag">⏱ 10–15 min · whole team</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">3</div>
    <div class="fah-body">
      <h3>Silent dot voting</h3>
      <p>Everyone gets five dot stickers. You can stack all five on one idea or spread them across five. <strong>No talking while voting.</strong> Silence prevents the loudest voice from anchoring the group—the most common failure mode in group prioritization. Once votes are tallied, log totals back into your intake database so the prioritization is permanent.</p>
      <span class="fah-tag">⏱ 5–10 min · whole team · silence required</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">4</div>
    <div class="fah-body">
      <h3>The 2×2: value vs. effort</h3>
      <p>With vote tallies as a guide, place stickies on a 2×2 grid together as a team. <strong>Value</strong> on the vertical axis. <strong>Effort</strong> on the horizontal—and effort means all-in effort: time, skill required, data access, dependencies. Facilitator guides, team places. The conversation happens around placement, not around lobbying for ideas.</p>
    </div>
  </div>
</div>

<div class="fah-matrix">
  <div class="fah-matrix-label">Value vs. Effort—how to sort your ideas</div>
  <div class="fah-matrix-grid">
    <div class="fah-m-y">Value &uarr;</div>
    <div class="fah-q fah-q-star">
      <div class="fah-q-label">&#10022; Sprint targets</div>
      <div style="font-size:13px;line-height:1.45;">High value, achievable in 90 min. These are your hackathon finalists. Pick 3–4, pair up, build.</div>
    </div>
    <div class="fah-q fah-q-b">
      <div class="fah-q-label">Scope &amp; own</div>
      <div style="font-size:13px;line-height:1.45;color:var(--ink-soft);">High value, high effort. Real initiatives—not hackathon material. Each gets a named owner and goes on the roadmap.</div>
    </div>
    <div class="fah-q fah-q-c">
      <div class="fah-q-label" style="color:var(--line-strong);">Intentionally skip</div>
      <div style="font-size:13px;line-height:1.45;color:var(--line-strong);">Low value, low effort. Name it explicitly. Agree to leave it alone.</div>
    </div>
    <div class="fah-q fah-q-c">
      <div class="fah-q-label" style="color:var(--line-strong);">Intentionally skip</div>
      <div style="font-size:13px;line-height:1.45;color:var(--line-strong);">Low value, high effort. Clear no.</div>
    </div>
    <div class="fah-m-x">Effort &rarr;</div>
  </div>
</div>

<div class="article-warn fah-warn">
  <div class="article-warn-title fah-warn-title">Name what you're skipping</div>
  <p>Things that "fall off the list" have a way of coming back. Things you've explicitly decided to skip don't. For every idea below the line: name it, say it out loud, agree to leave it alone. <em>"Intentionally skipping"</em> and <em>"fell off the list"</em> are not the same thing.</p>
</div>

<div class="fah-track" style="margin-top:24px;">
  <div class="fah-step">
    <div class="fah-num">5</div>
    <div class="fah-body">
      <h3>Two final cuts: when, and whether it's ready</h3>
      <p>Sort the upper-right survivors two ways. First, <strong>timing:</strong> Build (do it at the hackathon), Later (worth doing, not this week), Even Later (needs scoping first). Second, <strong>readiness:</strong> can you touch this right now, or does it have a dependency, a data question, an unknown to resolve? A high-value idea that isn't ready to build isn't a sprint candidate—it's a scoping task. Naming that distinction keeps you honest.</p>
      <span class="fah-tag">⏱ 10–15 min · whole team</span>
    </div>
  </div>
  <div class="fah-step">
    <div class="fah-num">6</div>
    <div class="fah-body">
      <h3>Assign pairs</h3>
      <p>Match people to Build-ready ideas. Pairs, not solo work—two people per build keeps momentum up when one gets stuck. Assign 1–2 floaters (ideally including yourself if you're the leader) who stay unattached and circulate to unblock teams during the sprint.</p>
      <span class="fah-tag">⏱ 5 min · facilitator-led</span>
    </div>
  </div>
</div>

## Protect the gap: inspire, sleep, build

Here's the sequencing decision that separates a good hackathon from a great one: **don't build on the framing day.**

The framing session is dense with new thinking—a full backlog processed, clustered, voted on, and prioritized. Ending there, inspired rather than rushed, gives that thinking time to settle. People go home with a problem in their head. They think about it in the shower. They wake up with the approach half-formed. That overnight processing is doing real work.

<div class="fah-flow">
  <div class="fah-flow-step">Inspire</div>
  <div class="fah-flow-arrow"><span class="fah-flow-arrow-h">&rarr;</span><span class="fah-flow-arrow-v">&darr;</span></div>
  <div class="fah-flow-step">Sleep</div>
  <div class="fah-flow-arrow"><span class="fah-flow-arrow-h">&rarr;</span><span class="fah-flow-arrow-v">&darr;</span></div>
  <div class="fah-flow-step">Build</div>
</div>
<p class="fah-flow-caption">That's the sequence. The gap between the framing day and the build day isn't scheduling slack. It's part of the method.</p>

Add one more step on the morning of the build day before anyone opens a laptop: **15–20 minutes of inspiration.** Show examples of what other finance teams have actually built with AI. Real demos, not slides. Actual workflows someone is using. Then, and this is the move worth stealing, clear the votes and run a second idea-generation round from scratch. The second round is almost always better than the first. People arrive with new angles, sharper problem statements, and sometimes a completely different sense of what they want to build.

## The build day

The build sprint is simple by design. Complexity is the enemy of shipping.

<div class="article-callout fah-callout">
  <div class="article-callout-title">Build day structure</div>
  <ul>
    <li><strong>Morning reboot (15–20 min):</strong> Inspiration videos, second idea-generation round, confirm pairs and targets.</li>
    <li><strong>Sprint #1 (90 min):</strong> Pairs build. Floaters circulate. No whole-group check-ins until time's called—mid-sprint interruptions break flow.</li>
    <li><strong>Optional midpoint (45 min in):</strong> 5-minute pulse check per team. Not a demo—just calibration. Are you stuck? Do you need to scope down?</li>
    <li><strong>Demos + verdicts:</strong> Regroup as a full team. Each pair demos what they built or learned. 10 minutes per team, 2 minutes for the verdict decision. Every demo gets a verdict and a named owner before the next team starts.</li>
  </ul>
</div>

The constraint (90 minutes) is the point. It forces scope decisions early. A team that's trying to build the perfect reconciliation engine will fail. A team that's trying to build a working prototype of one slice of that engine will ship something. "Done enough to demo" is the bar.

What floaters actually do: when a pair is stuck on a tool behavior, a data question, or scope creep, the floater doesn't solve the problem for them. They ask one question: *"What's the smallest thing you could build that would prove this works?"* That's usually enough to unblock.

## Closing with verdicts and owners

The close is where most hackathons fail. Teams demo, everyone claps, and then... nothing. The builds sit in a Notion database for three months and quietly become shelf-ware. What prevents that is a discipline: **every demo gets a verdict and a named owner before the room empties.**

<div class="fah-tiers">
  <div class="fah-tier fah-t-ship">
    <div class="fah-tier-title">Ship</div>
    <p>Push it to production now. It's working, it's useful, it's ready. Assign an owner whose job is to not let it die.</p>
  </div>
  <div class="fah-tier fah-t-iter">
    <div class="fah-tier-title">Iterate</div>
    <p>Another pass before it's ready. Assign an owner and a target date. <em>Iterate without a date is just Park with extra steps.</em></p>
  </div>
  <div class="fah-tier fah-t-park">
    <div class="fah-tier-title">Park</div>
    <p>Not the right moment—but documented for later. This is a legitimate verdict. Honor it by writing down why.</p>
  </div>
</div>

The Park verdict deserves more credit than it gets. It's not failure—it's intellectual honesty. Naming why something isn't ready (wrong timing, missing data, dependency on something else) is more useful than letting it die quietly. A well-documented Park can become a Ship six months later when the conditions change.

The goal is at least one thing in production before anyone gets on a plane. Aim for that. It changes the energy of the room and sets the bar for everything that follows.

## The operating system: a Notion setup that compounds

The post-its get the attention. They're not what makes this work. What makes it work is the underlying system—one intake database, one page per idea, a structured record that outlives the event.

<div class="fah-template">
  <div class="fah-template-title">📋 Hackathon intake form—fields that matter</div>
  <h3>Submission name</h3>
  <p>A 3–6 word label. Forces clarity before anyone has read the full submission.</p>
  <h3>Problem statement</h3>
  <p>One sentence. The constraint is the discipline—a problem that needs "and" is really two problems. Split it.</p>
  <h3>Who it hurts</h3>
  <p>A specific person or role. "The team" is not a persona. "The controller on every close" is.</p>
  <h3>Type</h3>
  <p>Automation / visibility / missing skill / analysis / ops plumbing. Useful for spotting patterns—if half the submissions are "I can't see X," that's a signal.</p>
  <h3>Impact + feasibility</h3>
  <p>A first guess, not a commitment. You'll refine it during the 2×2.</p>
  <h3>Definition of done</h3>
  <p>Describe the two-minute demo. What does success look like when someone watches it work? This field does more work than any other.</p>
</div>

The move worth stealing: **one page per idea.** Not just a row in a table—an actual page that becomes the full record. What the team submitted, live notes from the build, what they learned, what broke, what to do next. The page carries the idea through the event and becomes searchable institutional knowledge.

Without this, the hackathon produces prototypes. With it, it produces compounding assets. The next person who picks up a similar problem starts from the answer, not from scratch.

Two database views worth setting up: an **effort × value matrix** (the digital twin of your sticky-note 2×2, auto-sorted by vote count) and a **groups board** by theme. The groups view is useful for spotting when one area—say, month-end close—quietly dominates the shortlist, which is usually a signal worth paying attention to.

## After: building the AI Lab

The hackathon is a beginning, not a destination. What makes it compound over time is institutionalizing what you learned: a shared space—call it the AI Lab, call it whatever fits your culture—where builds live and can be forked.

The operating model is simple:

- Build something useful → document it → drop it in the Lab.
- Find something someone else built → fork it → adapt it to your context.
- Review the Lab quarterly. What's still in use? What needs updating? What opened up new possibilities?

Run the hackathon again next year. The format gets easier the second time—the setup is faster, people know what to expect, and the ideas are sharper because everyone has spent a year noticing problems worth solving. The first one is the hardest. The flywheel needs one good push.

<div class="article-callout fah-callout">
  <div class="article-callout-title">What good looks like when you leave</div>
  <ul>
    <li>3–4 working prototypes, each with a verdict and a named owner</li>
    <li>A prioritized backlog in Notion for everything that didn't get built—with owners on anything that moves forward</li>
    <li>At least one thing in production before anyone leaves</li>
    <li>A shared Lab space where builds live and can be forked</li>
    <li>A date on the calendar for the next one</li>
  </ul>
</div>

The teams that get the most out of AI aren't the ones with the best tools. They're the ones that got good at using them—together, on purpose, through deliberate practice. A hackathon is how you start that. Sail, don't row.

<div style="border-top:1px solid var(--line-strong);margin-top:48px;padding-top:24px;">
  <p style="font-size:13px;color:var(--muted);margin:0;">Brian Weisberg is a tech CFO writing about finance leadership, AI adoption, and building finance teams that compound. <a href="/thought-leadership">More writing &rarr;</a></p>
  <p style="font-size:12px;color:var(--muted);margin:14px 0 0;">&#9973; Made it this far? <a href="/play">Sail, Don&rsquo;t Row</a> is also a game.</p>
</div>
'''


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", default=None, help="Path to library.db (or set LINKLIB_DB)")
    ap.add_argument("--apply", action="store_true",
                     help="Actually write the migration. Without this flag, only a preview "
                          "is printed — no DB writes.")
    args = ap.parse_args()
    args.db = resolve_db_path(args.db, allow_missing=False)
    print(f"Reading from: {args.db}\n")

    lib = Library(args.db)
    try:
        row = lib.get_original_content_by_slug(SLUG)
        if row is None:
            print(f"No original_content row with slug={SLUG!r} — nothing to do. "
                  "(Expected this row to already exist from the Phase 1 migration.)")
            return 1

        if row["body_md"] == BODY_MD and row["date_label"] == DATE_LABEL and row["title"] == TITLE:
            print("Already applied — body_md, date_label, and title already match. Nothing to do.")
            return 0

        current_body_desc = "NULL" if row["body_md"] is None else f"{len(row['body_md'])} chars"
        print(f"Row id={row['id']}, current title={row['title']!r}, current body_md is "
              f"{current_body_desc}, current date_label={row['date_label']!r}.")
        print(f"Would set title to {TITLE!r} (fixes a double-escape bug — see module docstring), "
              f"body_md to {len(BODY_MD)} chars of converted content, date_label to {DATE_LABEL!r}.")

        if not args.apply:
            print("\nPREVIEW ONLY — no DB writes. Re-run with --apply to write for real.")
            return 0

        sort_key = _sort_key_from_date_label(DATE_LABEL)
        lib.update_original_content(
            row["id"], row["slug"], TITLE, row["teaser"], row["tag_label"],
            row["link_label"], BODY_MD, row["status"], row["featured_home"],
            DATE_LABEL, sort_key, row["display_order"],
        )
        print("\nApplied.")

        # Write-then-read-back.
        after = lib.get_original_content(row["id"])
        assert after["title"] == TITLE, "title did not round-trip"
        assert after["body_md"] == BODY_MD, "body_md did not round-trip"
        assert after["date_label"] == DATE_LABEL, "date_label did not round-trip"
        assert after["sort_key"] == sort_key, "sort_key did not round-trip"
        assert after["status"] == "live", "status changed unexpectedly"
        print(f"Verified — read back title={after['title']!r}, "
              f"{len(after['body_md'])} chars of body_md, "
              f"date_label={after['date_label']!r}, sort_key={after['sort_key']!r}, "
              f"status={after['status']!r}.")
        return 0
    finally:
        lib.close()


def _sort_key_from_date_label(date_label: str) -> str:
    """Local copy of webapp.app._sort_key_from_date_label — kept in sync by
    hand rather than importing webapp.app here, since importing the full web
    app module (FastAPI routes, startup hooks) into a small DB-only script
    is more machinery than this one pure function is worth. Same logic,
    same "Mon YYYY"/"Month YYYY" -> "YYYY-MM" behavior."""
    from datetime import datetime
    s = date_label.strip()
    if not s:
        return ""
    for fmt in ("%b %Y", "%B %Y"):
        try:
            dt = datetime.strptime(s, fmt)
            return f"{dt.year:04d}-{dt.month:02d}"
        except ValueError:
            continue
    return ""


if __name__ == "__main__":
    raise SystemExit(main())
