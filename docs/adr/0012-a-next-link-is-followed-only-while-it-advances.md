# 12. A next link is followed only while it advances

Date: 2026-08-31

Status: Accepted

## Context

`cn-cma-global-discovery-catalogue` had `last_sync = NULL` and had never once
completed a run. The runs it did leave ended on

> stopped paging cn-cma-global-discovery-catalogue after 50 pages; its next
> links do not terminate

which was `fetch_pages` reaching its ceiling and reporting the run failed.

The catalogue holds 560 records and answers `limit` and `offset` correctly.
What it does not do is build its paging links from the page it is on. Asked at
`offset=500`, `550`, `560` and `600`, every response — including the ones
carrying no features at all — carries the same link:

```json
{"rel": "next", "href": ".../items?limit=1&offset=1"}
```

Its `self` link is equally stale. So the read follows the first page of 500,
lands on a page of one record at offset 1, is offered offset 1 again, and
walks the second record of the collection until the ceiling stops it.

`fetch_pages` was written to follow the server's own link rather than an offset
it computes, and the reason is good: the link carries whatever query the server
needs to resume, and a filtered read that resumed without its filter would page
on through the whole collection believing it was still inside its window. The
ceiling existed for exactly the case CMA turns out to be. What the ceiling
cannot do is finish the read.

## Decision

**A next link that names an offset behind what has already been read is not a
next page.** It is this page again, or one before it. The rule replaces an
assumption that following the link makes progress — an assumption one of the
three catalogues this tool ships with does not satisfy.

**A link naming no offset at all is followed as given**, and so is one that
resumes *ahead* of what has been read. The first may be paging by a cursor
this knows nothing about, and refusing one for not being an offset would break
every server that pages properly by something else. The second is the server's
own statement about where its next page begins, and second-guessing it is how
a reader comes to skip or repeat. The ceiling stays as the guard for both,
which is what it was always for.

**A page with no next link at all ends the read**, however short of
`numberMatched` it was. A server offering none has said that is all of it, and
taking over its paging on the strength of a count it also published would be
calling one half of its answer wrong on the authority of the other. This only
ever takes over from a server that has contradicted itself, which keeps the
change to the fifty-four station registries and the archive poll at nil.

**Where the link will not advance, the read resumes from an offset of its
own** — the original URL and the original query, plus how much has been read.
Resuming from the original query is what keeps the filtered reads inside their
filter, which was the whole argument for following the link; adding an offset
to it does not weaken that.

**It resumes only while the collection says there is more, and only while
pages keep coming back with records in them.** `numberMatched` is what tells a
short read from a whole one, and a collection that reports no count has said
nothing — so the read stops where the server's links stopped advancing, which
is what it did before any of this. A server that answers an offset it does not
understand with an empty page stops on the second condition. One that answers
with the *same* page every time is bounded instead by the count climbing a
page each round until it reaches what the collection says it holds — a handful
of repeated records, applied twice and stored once, rather than a walk to the
ceiling.

**How much has been read is counted from the feature lists, not from
`numberReturned`.** That count becomes the offset a resume asks from, and the
servers this exists for are exactly the ones whose paging metadata is stale: a
server able to move the offset by publishing a number could make a read skip
records nobody would know were missing. Counted from the lists, the count can
only lag the server's own position — a link that jumped ahead leaves it
behind, never in front — so a resume can re-read, which is idempotent, and
cannot skip, which would not be.

**The ceiling stays.** It is the last resort for links that advance forever,
and a run that hits it is still a failed run: a partly-read registry that
reported success would be indistinguishable from a centre that really has only
these records.

**Reading the three numbers is `interpretation`'s, judging them is the sync's.**
`records_matched`, `records_returned` and `page_offset` join `next_page_url` as
facts read off a payload or a link. Which of them means "do not follow that" is
a rule about how a collection is read, and lives with the reading.

## Consequences

**CMA reads through.** Verified against all three catalogues at the time of
writing: `cn-cma` 560 records in two pages, `ca-eccc` 559 in two, `de-dwd` 560
in two. The catalogue that had never completed a run completes one, so the
region now has two reading catalogues rather than one.

**Nothing changed for a server that pages correctly**, including one whose
last page is short. Only a next link that resumes behind what has been read
diverts the read, so the two catalogues that page properly, all fifty-four
station registries and the archive poll behave exactly as they did.

**`records_returned` reads the feature list rather than the count beside it.**
That is a behaviour change for any server whose `numberReturned` disagrees
with what it sent, and the change is deliberate: what a reader holds is what
arrived.

**The comment in `interpretation.ogcapi` that said the link is followed as
given is now qualified rather than true.** It has been rewritten, since a rule
with a silent exception is worse than either.

**One more catalogue's records are applied on each run.** CMA is read-only, so
nothing new is written; what changes is that its count for the region is now a
count of the region rather than of 501 records.

## Not addressed here

**Paging by offset always, and ignoring the links.** All three catalogues and
every wis2box station registry support `offset`, so it would work today and
would be simpler. It would also silently break the first server that pages by
a cursor, and this tool reads whatever fifty-four centres happen to run. The
link stays primary and the offset stays the fallback.

**Telling CMA.** The defect is upstream, it is precise, and it is worth
reporting to the catalogue's operators. That is a message to send, not code to
write.

**Detecting a repeat by remembering every URL requested.** It catches one case
more than the offset test — a cursor that cycles — at the price of holding the
read's history. The ceiling already catches that case, one page later than a
seen-set would.
