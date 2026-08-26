# 9. Two verdicts, because two surfaces ask different questions

Date: 2026-08-26

Status: Accepted

Resolves [#119](https://github.com/wmo-raf/wis2watch/issues/119). Narrows
[ADR-0008](0008-a-centres-health-as-one-worst-of-standing.md) on one surface,
and reverses one decision from
[#118](https://github.com/wmo-raf/wis2watch/issues/118).

## Context

ADR-0008 decided that a centre's health is one worst-of standing over the four
judgements the tool makes: staleness, silence, cache pickup, and which of the
centre's own transports is answering. That decision was right for the surface
it was made on -- a table where all four badges are drawn beside the standing
as its evidence.

It was then put on the admin's front page, which is not that surface.

The front page is read on login to answer one question: **is data coming out of
these centres**. Two of the four judgements do not answer it. Cache pickup is
what happened *downstream* after a centre published; origin watch is how this
tool is *reading* the centre at all. Both are true, both matter, and neither is
about whether the centre is transmitting.

Measured on the region rather than argued: **28 of 32 centres fall back to
their archives.** A worst-of over all four therefore put **21 of 32 under
"Archive only"** and left **exactly one row reading healthy** -- on a panel
whose entire job is to say whether data is flowing. The column that was
supposed to be scannable was two-thirds one word, and that word was about
plumbing.

## Decision

### Two verdicts on one row

`TransmissionStanding` folds staleness and silence alone:

| Rank | Value | Label |
|------|-------|-------|
| 0 | `never_seen` | Never heard from |
| 1 | `stale` | Gone quiet |
| 2 | `silent` | Datasets overdue |
| 3 | `transmitting` | Transmitting |

The same region reads **2 never heard from, 1 gone quiet, 7 with datasets
overdue, 22 transmitting**. Ten centres to look at, twenty-two to leave alone.

`NodeStanding` is unchanged and keeps all seven values. Both verdicts travel on
**every row**, always. One request serves both tables, and neither can be
computed from rows the other never saw -- which is the whole defence against
two all-centres tables disagreeing about a centre.

### A coarsening, not a rival

`TransmissionStanding` is deliberately a *coarsening* of `NodeStanding`: ranks
0, 1 and 2 are the same three faults under the same three names, and
`transmitting` is exactly the four ranks below them.

That is not tidiness, it is what makes one server-side order serve both tables.
Sorting by the full standing sorts by the transmission verdict as well, so both
surfaces put the same rows on top and a reader moving between them is never
told two different things about what is worst.

Three of the four labels are `NodeStanding`'s own, word for word, so the two
tables are one vocabulary rather than two. `Transmitting` is
`StationStanding`'s word for the same idea one level down.

### `silent` keeps its name, and its label does the work

Rank 2 lands on centres publishing **three hundred notifications an hour**,
last heard from six minutes ago -- one dataset past its own cadence is enough.
The internal key stays `silent` because it comes straight from
`Silence.SILENT`, and the label a reader sees is "Datasets overdue", which
claims nothing about the centre being quiet. The jarring word stays out of
sight.

### Which surface draws which

- **Admin home, "Transmission status"** -- seven columns, `transmission`,
  search but no standing filter (worst-first already puts every non-transmitting
  centre in the top rows).
- **`/node-overview/`, "Node overview"** -- twelve columns, `standing`, all four
  badges, search *and* filter, state synced to the address bar.

Both are the last 24 hours and **neither offers a window**. Going back in time
is the node statistics tab's job, over a real time series, 24 hours to ninety
days -- already built. Which is why:

### The centre code now leads to the statistics tab

#118 sent it to the diagnostic tab. It goes to statistics instead, because
that is the question both tables leave a reader with: *this centre looks wrong
today -- what has it been doing?* The diagnostic view is one tab click further.

The cost is real and accepted: on the detailed page the badges raise plumbing
questions the statistics tab does not answer, so the untruncated broker error
is now two clicks away. It is a tooltip on the Origin badge to keep it at zero.

### One component, two views

`data-view="glance"` or `"detail"` on the mount point picks the column list and
which verdict fills the status slot. Named lists in `rows.js`, not a boolean and
not a column list in a template: a boolean toggling five things is the
union-of-two-feature-sets prop this codebase already refused once, and a list in
a `data-` attribute is somewhere to typo a key into a silently missing column.

## Consequences

- **ADR-0008 is narrowed, not overturned.** A centre's health is still one
  worst-of standing wherever all four judgements are shown. What changed is the
  recognition that a surface showing only two of them needs a verdict folded
  from only those two -- a verdict that named faults its own table could not
  display was the actual defect.
- **Two vocabularies to keep in step.** They cannot silently diverge on
  ordering, because the coarsening relation is asserted in the tests; they
  *can* diverge on wording, and the labels are shared by hand.
- **`healthy` no longer appears on the front page at all.** Nothing there
  claims a centre is well -- only that data is arriving. That is honest, and it
  is a narrower claim than the old panel was making.
- **The detailed page stopped rendering its own table.** There is one
  derivation, one endpoint and one component; the drift #119 was opened to end
  cannot recur by construction. The twelve tests that asserted its server-
  rendered HTML moved to the endpoint and the derivation.
- **A third view is a list in `rows.js` and a `data-view`.** Adding one is
  cheap; adding a *third verdict* is not, and should want the same measurement
  this one got.
