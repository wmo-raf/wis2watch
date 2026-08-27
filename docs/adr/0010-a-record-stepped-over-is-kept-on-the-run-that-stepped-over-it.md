# 10. A record stepped over is kept on the run that stepped over it

Date: 2026-08-27

Status: Accepted

## Context

Every sync here closes its run with the same four counts, and a run that could
not store some of what it read is recorded as `partial` with `items_errored`
set. The counts were the whole of what survived. Which records were stepped
over, and what refused them, went to `logger.warning` in a worker's output.

`partial` was also surfaced nowhere a person reads. The status has always had
three values; two of them are read — the node page shows a badge, the
not-answering report reads `failed` runs, the hard failure reads runs that
brought records back — and `partial` was read by the node detail page's log
list and by nothing else.

That is how #128 happened. A unique constraint on a dataset's topic refused
nine of the region's sixty-three catalogue records on every run, for at least
four days. Every surface of this tool reported those nine datasets as absent
from the region, which is exactly what a centre that had stopped publishing
them would look like. The run that dropped them was a `partial` row on one
centre's page reading "errored 9", and the constraint that did it was in a
container log.

The counts were never the problem. Nine is a number to worry about; nine
identifiers and the constraint name is a fault somebody can go and fix, and
the run knew both at the moment it stepped over them.

## Decision

**The reason is kept on the run.** `SyncLog.stepped_over` holds one object per
record, with the identifier the source calls it by and what refused it. Every
sync already returns an outcome per record for the counts to add up; the
errored outcome becomes a `SteppedOver` carrying its reason, and `SyncCounts`
counts it and keeps it in the same movement. Nothing else about how a sync
reports a record changed.

**It is a seventh gap report, `syncs-stepping-over-records`**, for the reason
ADR-0006 made `registries-not-answering` the sixth: it is a state nobody was
looking at, and joining `GAP_REPORTS` gets it the index count, the page and
the digest at once. A reader who never opens a node detail page now sees that
a sync is losing records, and sees which ones on the page behind the count.

**The newest run of each sync answers, and only that one.** What a reader acts
on is whether records are being lost now. A sync whose next run got them down
is not a finding; listing every partial run there has ever been would be a log.
This is the same reading the not-answering report makes of its registries,
from the other side.

**A failed run is not in it.** A run that failed brought nothing back and is a
network or a source to chase — the distinction ADR-0006 drew, kept from this
end. A run that reached its source, stored fifty-four records and lost nine
reached it perfectly well: that is a data problem in the region, or a fault in
how this tool reads what it was sent, and it wants a different person. Nor is a
run called `partial` for any other reason: OSCAR calls a run partial for a
territory it could not read at all, and a row of that with nothing under it to
fix is not a finding.

**A run still speaks for its sync for 14 days**, in a setting. Nothing prunes
sync logs, so without a window the newest run of a sync nobody runs any more is
the newest run there will ever be, and a centre that stopped advertising a
registry would stand in this report for good. Long enough that the weekly OSCAR
run is never dropped by the window alone.

**Reasons stop at fifty per run; the count does not.** A run stepping over a
thousand records is one fault rather than a thousand errands, and the fiftieth
reason names it as well as the thousandth. `items_errored` keeps counting past
the ceiling, so a run that kept fewer reasons than it lost records says so by
the two numbers disagreeing rather than by quietly listing fewer.

**`CATALOGUE_WRITER_STALE` is left as it is.** Its clock reads the newest run
that did not fail and brought back more records than it stepped over, so a
writer erroring on nine of sixty-three passes it comfortably and always will.
That was checked and is right. The registry a partial run leaves behind is
current for the fifty-four it applied, which is not what "frozen" means, and
that failure asks a reader to stop believing the registry as a whole. A
persistently partial writer is a real finding and it is now reported as one —
as a gap about the region's records, not as an alert about this tool's
blindness. The line ADR-0006 drew between the two holds here unchanged.

## Consequences

**A run recorded before this kept no reasons**, and says so rather than
appearing to have stepped over nothing: the row shows the count with "and N
more, whose reasons were not kept" under it. `stepped_over` is empty for every
run already in the database, and backfilling it would be inventing evidence.

**The node detail page names them too.** The sync run table already carried the
count in a column; the records now sit under the outcome beside the run's own
error. That page is where somebody sent after a missing dataset lands, and it
was structurally unable to answer them.

**`ReportedFinding` keys on the sync**, not the run — the pair of what was read
and which sync read it. A registry stepping over the same station every hour is
one finding, not twenty-four a day; a sync that recovers leaves the report and
is let go, so one that breaks again is announced again.

**Sync logs carry evidence now as well as history.** ADR-0006 already made them
load-bearing for the "never answered" standing. A retention policy over
`SyncLog` would now also throw away the only record of which records the region
is missing and why.

## Not addressed here

**Reporting the record rather than the run.** A dataset that has been stepped
over on every run for a week is arguably a finding of its own, keyed on the
dataset, with a first-seen date like an unregistered centre has. It would want
its own table rather than a JSON field, and the run-level report is what says
whether that is ever worth having.

**Telling a record refused by the source apart from one refused here.** A title
longer than the column and a record this tool cannot interpret are both
"stepped over", and the reason text is the only thing that distinguishes them.
Classifying them would be guessing at exception types; the reason a reader
actually needs is the one the database or the parser wrote.

**A partial run on the overview.** The centre-level standing on the node
overview says whether data is flowing, and a sync losing records is not that.
It is a finding about this tool's reading of the region, which is what the gap
reports are for.
