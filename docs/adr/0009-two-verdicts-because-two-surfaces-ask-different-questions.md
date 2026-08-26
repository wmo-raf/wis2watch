# 9. Two verdicts, because two surfaces ask different questions

Date: 2026-08-26

Status: Accepted

Amended 2026-08-26, hours after it was accepted, once a reader who had never
registered a WCMP2 record met rank 2's label on the front page. Two sections
were rewritten in place rather than superseded by a new record: "`silent`
keeps its name" is now "`silent` keeps its key", and "One component, two
views" gains the sub-line. What was decided is marked where it changed. The
measurement below is untouched and is not relitigated.

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
| 2 | `silent` | Behind schedule |
| 3 | `transmitting` | Transmitting |

The same region reads **2 never heard from, 1 gone quiet, 7 behind schedule,
22 transmitting**. Ten centres to look at, twenty-two to leave alone.

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

### `silent` keeps its key, and the label went plain

Rank 2 lands on centres publishing **three hundred notifications an hour**,
last heard from six minutes ago -- one dataset past its own cadence is enough.
The internal key stays `silent` because it comes straight from
`Silence.SILENT`. The jarring word stays out of sight.

**Amended.** The label was "Datasets overdue" and is now **"Behind
schedule"**. The original reasoning was sound about what the label must not
say -- nothing about the centre being quiet -- and wrong about what it could
assume. "Dataset" is WCMP2's noun for a registered discovery record. It is
correct, it is the catalogue's own word, and a reader who has never registered
one cannot act on it: met alone in a Status column, with a `Quiet` cell
reading six minutes right beside it, it is a term to look up rather than a
verdict to act on.

"Behind schedule" is the exact antonym of `Silence.ON_SCHEDULE`'s "On
schedule", which the detailed table already draws for the same judgement, so
the plainer word *narrows* the shared vocabulary rather than splitting it. It
is changed in **both** `NodeStanding` and `TransmissionStanding`, which is why
`test_three_of_its_four_labels_are_the_full_standings_own` still passes
untouched -- that test is the guardrail against exactly the front-page-only
rename this could have been.

The word is not lost, it is demoted to where it can be learned. The sub-line
below says "3 of 12 datasets overdue", and the count is what makes the noun
teachable: it renders a dataset a countable thing this centre has twelve of.
Plain word in the verdict, domain word in the evidence -- not the reverse.

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

**Amended: the glance table draws a sub-line, the detailed one does not.**

This record moved the overview page's extra lines under two badges into
tooltips, on the grounds that *rows two and three deep destroy the reading
down a column that a worst-first table exists for*. That holds on the twelve-
column table it was written about, and it does not reach the seven-column one.

The glance table shows a verdict and none of what it was folded from: no
Silence badge, no dataset count, no tooltip, and a `Quiet` column reading six
minutes directly beside "Behind schedule". A verdict with its evidence nowhere
on the row and its apparent contradiction next to it is worse than a quiet
row.

So `subline()` in `rows.js` -- beside `VIEWS` and for the same reason, because
the component draws and `rows.js` decides -- returns the overdue sentence on
the glance table, on `silent` rows only, and an empty string everywhere else.
Two constraints keep it from becoming what was rejected:

- **Only faulty rows.** Seven in thirty-two, in the measured region. The other
  twenty-five stay one line. This *inverts* the original objection: a second
  line only where there is a fault makes the faulty rows taller, which helps a
  worst-first scan rather than flattening it.
- **Only the glance table.** The detailed one already draws the Silence badge,
  the dataset count, and this very sentence as that badge's tooltip. A second
  copy in its Standing cell would put one sentence twice on one row, which
  teaches a reader the two cells might mean different things.

Both surfaces call `overdueSentence()`, so the tooltip and the sub-line cannot
drift into two spellings of one fact.

### Amended: a third colour, because the second one was lying about the order

Rank 2 was a *ringed* red dot, sharing that mark with ranks 3 and 4, above the
filled red of ranks 0 and 1. On a table sorted worst-first the colour ran
backwards -- red, then red again -- with the distinction living in two pixels
of inset shadow on a half-rem dot.

`--stat-slipping` (amber) now covers ranks 2 to 4 on both tables, filled
rather than ringed: the ring existed to say "arriving, but faulty" while
spending only red, and amber says that on its own. The scale is red, amber,
teal, monotone with the rank, and read without being taught.

The role lives in `roles.css` with the other two, not beside the table that
uses it, for that file's own stated reason. It is a **mark** colour only:
amber-600 is 3.2:1 against the light surface, which clears what a graphical
object is held to and misses what text is -- so the sub-line beneath it is
muted ink, and painting evidence in the finding's own colour would have the
row shout twice for one fault regardless.

## Consequences

- **ADR-0008 is narrowed, not overturned.** A centre's health is still one
  worst-of standing wherever all four judgements are shown. What changed is the
  recognition that a surface showing only two of them needs a verdict folded
  from only those two -- a verdict that named faults its own table could not
  display was the actual defect.
- **Two vocabularies to keep in step.** They cannot silently diverge on
  ordering, because the coarsening relation is asserted in the tests; they
  *can* diverge on wording, and the labels are shared by hand. The rename to
  "Behind schedule" was the first live test of that: the guardrail held,
  because renaming on the front page alone would have failed
  `test_three_of_its_four_labels_are_the_full_standings_own` rather than
  shipping quietly.
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
- **Amended: a label is not tested by whether it is accurate.** "Datasets
  overdue" was precise, was the catalogue's own term, and had a whole section
  of this record defending it. It still failed, because the reader it had to
  work for was not the one who wrote it. What is measurable about a verdict --
  how many rows it lands on -- was measured; who could read it was assumed.
- **Amended: this file was rewritten rather than superseded.** The repo's
  pattern is a new record narrowing the old one, and 0008 was left intact when
  this one narrowed it. That was not followed here, deliberately and against
  the recommendation, because the record was hours old. The cost is that a
  reader six months out cannot tell original from revision except by the
  **Amended** markers and this line. Prefer a new record next time.
