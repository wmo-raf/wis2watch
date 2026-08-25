# 3. A finding that stopped being checkable is let go, not cleared

Date: 2026-08-25

Status: Accepted

## Context

The digest calls a finding cleared when its report stops listing it, after a
grace period of two daily digests. The grace exists because a report can stop
listing a finding without anything having been fixed — propagation gaps are
withheld for a centre whose own vantage points have gone dark, the
unattributed share is worked out over a window a quiet centre falls out of —
and announcing those as cleared would be wrong twice over: it announces a fix
that never happened, then announces the finding again as new when it comes
back.

The propagation horizon is not that kind of absence. Once a gap passes the raw
retention cutoff the Global Broker rows that would settle it have expired, so
it can never be closed and never be found again. The centre leaves the report
for good, and the grace period runs out on an absence that never ends.

Only half the harm applies. A centre leaves this way having had nothing new go
missing for the whole forensic window, so the news is not false; and the
finding cannot come back as itself, so it cannot be announced twice. What is
wrong is narrower: **a fortnight of no gaps at a centre that published nothing
in that fortnight is not a recovery, and the mail could not tell that from one.**

Two things were already true and did not settle it. The report says what it
bounded — `1 older gap is not listed…` — and that sentence is carried into the
mail beside its news, so the qualification sits in the same section as
`Cleared:`. And the reader it is written for is the one who knows what the
horizon is.

The argument against building anything was that a fifth callable on
`GapReport` would serve one report's one case, next to a qualification already
in the same email.

## Decision

**`GapReport` gains `find_unsettled`**, alongside `find_rows`, `count_rows`,
`describe_row` and `describe_bound`. It answers with the keys the report can
no longer settle either way — not the ones it merely stopped listing. Four of
the five reports answer with nothing, by the shared default: an absence in a
report bounded by its filters is one that can end, and the grace period is
already right for it.

**The propagation report answers with the centres whose last word is an
unanswerable gap.** The horizon is asked for by the same query that counts
gaps in `propagation_gaps_left_out` — one horizon asked two ways cannot be
allowed to disagree about what is past it. Centres whose own vantage points
are dark are absent from both, for the same reason: their gaps are withheld
rather than unanswerable.

**A centre heard from since is not among them.** A gap the world turned out to
carry is the only thing that settles anything here, so a centre holding one
later than its last unanswerable gap has been observed publishing to a path
that works, and its clearing is real. Without that, one gap left open a spring
ago would silence every good word about that centre for the life of the
installation — an unanswerable gap is never closed and never purged.

**The digest lets those findings go silently.** They are excluded from
`resolved`, their remembered rows are dropped, and nothing is said. A finding
the report still lists is untouched however much else that report can no
longer answer for.

**The dropping happens on every run, before the send.** It is not news, so it
cannot be made to wait on there being any — unlike `record_digest`, which
records what was reported only once somebody has actually been told.

## Consequences

**A centre whose gaps pass the horizon is no longer announced as cleared.** It
is forgotten instead, so the same centre breaking again is announced as new.

**Genuine recovery still clears**, including at a centre that also holds an
older gap nobody can check any more: the late arrival is later than the
question, so the centre leaves the report having been heard from.

**A centre that really did go quiet for the whole forensic window is not
announced either.** That is the cost, and it is why the report's account of
what it bounded is still carried into the mail: the reader gets the
qualification even though the digest now says nothing.

**A sixth report that can outlive its own evidence has somewhere to say so.**
The default means one that cannot is unaffected by existing.

## Not addressed here

**Retiring the gap rows themselves.** A gap past the horizon is kept because
it is the last thing holding the notification UUID; the rollups carry counts
alone. What it stops being is something to send somebody to a centre about.
