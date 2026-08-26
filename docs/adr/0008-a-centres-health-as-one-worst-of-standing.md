# 8. A centre's health as one worst-of standing

Date: 2026-08-26

Status: Accepted

Resolves [#118](https://github.com/wmo-raf/wis2watch/issues/118), the
all-centres table on the admin homepage.

## Context

The tool judges a centre four independent ways, and `analysis/overview.py`
opens by saying that keeping them apart is what the whole thing is built
around:

- **Staleness** -- how long since anything at all arrived, against one flat
  threshold.
- **Silence** -- each dataset against its own learned or stated cadence.
- **Cache pickup** -- whether the Global Caches carried the centre's core data.
- **Origin watch** -- which of the centre's own transports is answering.

They answer different questions on purpose. "Gone quiet" and "publishing where
no one can see it" are different failures with different people to ring, and
the overview page has shown them as four badge columns since it existed.

Four badges sort four ways. The overview page sorts by staleness alone, which
means the column a reader is actually asking about -- *which centre should I
look at first* -- is not the column the table is ordered by. That is tolerable
on a page somebody opened on purpose. It is not tolerable on a panel read on
login, which is what #118 adds, and which exists to answer that question in one
glance.

The station table one level down had already solved this. A station has a
single `StationStanding` with a `RANK`, and that one word drives the default
order and the filter. The obvious move was the same shape for a centre.

## Decision

### One standing, folded worst-of, and the badges stay

`NodeStanding` is a **worst-of** over the four judgements. It is read in rank
order and the first fault wins.

**The four badges are not replaced by it.** They stay as columns beside it.
The standing says a centre is worth looking at and puts it at the top; the
badges say which way it is broken. Every value of the standing names exactly
one badge, so the standing is never a new fact about a centre -- it is a
pointer at a badge that already carries one.

Rank order, worst first:

| Rank | Value | From |
|------|-------|------|
| 0 | `never_seen` | `Staleness.NEVER_SEEN` |
| 1 | `stale` | `Staleness.STALE` |
| 2 | `silent` | `Silence.SILENT` |
| 3 | `not_cached` | `CachePickup.NOT_PICKED_UP` |
| 4 | `no_broker` | `OriginWatch.UNWATCHED` |
| 5 | `archive_only` | `OriginWatch.AT_ARCHIVE` |
| 6 | `healthy` | none of the above |

**First fault wins, and that is a choice about causes.** The later judgements
are downstream of the earlier ones: a centre nothing has ever been heard from
has also cached nothing and has no cadence to be judged against. Reporting a
consequence in place of its cause is how a reader ends up chasing the wrong
thing, so a never-seen centre reads `never_seen` and not `not_cached`.

**`not_cached` outranks both broker faults.** Uncached core data is a failure
that reaches *users*: the centre announced data the world cannot retrieve from
anywhere but the centre itself. A centre with no dialable broker is failing an
obligation, and costs a data user nothing for as long as the Global Broker is
carrying it. It costs *this tool* a vantage point, which is a different kind of
loss and not the reader's most urgent one.

### `unwatched` and `watched_at_archive` get a rank each -- measured, not assumed

These were folded into one `no_broker` first, on the argument that they are one
fault: nothing outside can dial this centre's broker, and the Origin badge
beside the standing already says which flavour.

Run against the live region that turned out to be wrong, and the measurement is
the reason this section exists:

- **22 of 32 centres** landed in one undifferentiated `no_broker` block.
- **Nothing at all read `healthy`.**
- 28 of 32 centres are `watched_at_archive`, and **20 of those were publishing
  fine and being cached** -- `picked_up`, `on_schedule`, non-zero traffic.
- Only **2** were genuinely `unwatched`.

A standing two thirds of a region shares sorts nothing, and it buried the two
real cases among twenty systemic ones. Split, the same region reads 2
`never_seen` / 1 `stale` / 6 `silent` / 2 `no_broker` / 20 `archive_only` /
1 `healthy`.

**Neither of them is `healthy`.** That is the laundering `OriginWatch`'s own
docstring refuses, and splitting the rank does not do it: both sit above
healthy, and the archive fallback is still reported as a fault. What changed is
only that the least severe fault in the region stopped being spelled the same
way as the second least.

### Where it lives

`NodeStanding` is in `analysis/overview.py`, beside `CachePickup`, as
`NodeStanding.of(row)`.

It reads nothing but fields a `NodeOverviewRow` already carries, so that module
keeps its "nothing here reads the time series" promise and stays a handful of
indexed lookups. It also means the overview page can show the standing with no
new query when [#119](https://github.com/wmo-raf/wis2watch/issues/119) migrates
it -- which is the point of putting it there rather than beside the table that
first drew it.

## Consequences

- **`healthy` is a high bar, and may be empty.** All four judgements must be
  clear, so a centre publishing perfectly well over a broker nobody can dial
  does not reach it. On a region where every centre has fallen back to its
  archive, no row is healthy at all. That is not the scale failing -- it is the
  finding, and it is one no single column on the overview page ever stated.
- **Two surfaces, one vocabulary, for now by discipline rather than by
  construction.** The panel words its cells from labels the API sends, which
  are `NodeStanding.CHOICES` and the three beside it. The overview page still
  renders its own four badges through `{% trans %}` and shows no standing at
  all. They cannot contradict each other on the four judgements, because both
  read `node_overview()`; they can differ in what they *show*, until #119.
- **A seventh value is cheap; a re-ranking is not.** Adding a standing is a
  line in `CHOICES` -- the rank is its position, the client reads both off the
  payload, and nothing spells the order twice. Reordering existing values
  silently changes what every reader's default sort means, so it wants the same
  measurement this one got.
- **The standing must never become the only thing shown.** Its whole licence to
  fold four judgements is that all four remain on the row. A future surface
  that shows the standing alone would be making a claim this ADR did not
  approve.
