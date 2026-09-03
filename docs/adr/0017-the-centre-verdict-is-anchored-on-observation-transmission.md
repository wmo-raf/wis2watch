# 17. The centre verdict is anchored on observation transmission

Date: 2026-09-03

Status: Accepted

## Context

ADR-0016 gave every dataset a kind read off its own topic, and changed no
judgement with it. This is the slice the classifier existed for.

The front page is read on login to answer one question, and for this
installation that question is **are observations coming out of these centres**.
It answered a wider one. `NodeLastSeen` is a denormalised row per node advanced
on any message at all, so a centre whose synops died three days ago and whose
aerodrome reports are flowing read **Active** -- the tool looking like it works
while it has stopped working, which is the failure mode this codebase is most
afraid of.

Measured against the region as the nodes themselves declare it: 36 observation
datasets across 27 centres, against 7 non-observation datasets across 4
centres.

## Decision

**`NodeStanding`, `TransmissionStanding` and the staleness and silence they are
folded from are computed over observation datasets only.** A centre whose
observations have stopped reads as gone quiet however much else it publishes.

**The columns beside the verdict are not observation-scoped.** Volume, the
dataset count and cache pickup keep counting everything a centre publishes:
they report how big a centre is and where its data went, which are not
questions about observations. Aviation and advisories keep their rows on the
node detail page, keep their own silence judgement and keep their volume --
they simply never decide the headline word.

**No scope control.** A control's job here would be to hide 7 datasets across 4
centres, and an untouched control is worse than none: it implies the default
was somebody's choice rather than what the tool is for.

**Staleness gains a fourth state.** A centre with no observation datasets at
all has no observation traffic to be stale about, and must read as neither
**Never seen** nor **Gone quiet** -- reporting either would put a fault on a
centre that has committed none, on the one row nobody could act on.
`CachePickup` already carries a third state for exactly this reason. Both
verdicts carry the state too, because a centre publishing warnings by the hour
must not read as **Transmitting** on a panel watching for observations. Zero
centres are in that state today; a centre could onboard tomorrow publishing
only warnings.

**`no_observations` ranks with the transmission judgements, above the plumbing
faults, and `Staleness` carries the same rank.** It is one of them -- the answer to "are observations coming out of
this centre" when the centre declares none -- and it is also what keeps the two
verdicts one order: `TransmissionStanding` is a coarsening of `NodeStanding`,
the all-centres table sorts both by the latter, and that only works while the
ranks they share come in one sequence. The accepted consequence is that a
centre declaring no observations *and* failing to reach the caches reads as the
first of the two, which is the rule every rank above it already follows.

**The observation-scoped last-seen is folded from the silence rows, not
queried again.** `NodeLastSeen` cannot answer it, and the obvious replacement
-- a backwards walk per node over rollups joined to observation datasets --
would be a second derivation of a fact the page already computes: silence
already walks the `(dataset, -hour)` index once per live dataset to find the
hour it last published in, and the centre's answer is the latest of those.
Asking twice is how the staleness column and the silence column beside it
would come to disagree about whether a centre published this morning. The fold
lives in `analysis/observations.py`, and `NodeSilence`/`silence_by_node` are
gone: one fold per centre, not two.

**The classification reaches the fold through the dataset, not a second parse.**
`DatasetSilenceRow` carries `is_observation` from `Dataset.is_observation`, so
the badge on the node detail page and every count of observation traffic are
one reading of one topic. There is still no SQL expression of the rule, and
ADR-0016's reasoning for that stands. The end-of-bucket reading both surfaces
judge quiet by is `rollups.end_of_hour` and `silence.hours_quiet_since`, for
the same reason: written twice, the staleness column and the silence column on
one row would be free to disagree about whether a centre published this
morning.

**Quiet is counted from the end of the hour bucket.** Forced rather than
chosen: the answer no longer comes from an instant maintained at ingest but
from the rollups, and the rollups are hourly buckets -- there is no finer
answer to be had. So it takes the reading `silence` already takes, through the
same two functions: from the end of the bucket, floored at nothing. It is the
smallest quiet the evidence supports, and reading from the bucket's *start*
instead would overstate every centre's quiet by up to an hour, which is how a
threshold manufactures findings. The visible consequence is that the effective
threshold moves by up to an hour: a centre quiet for 25 hours against a
24-hour threshold now reads active where it read stale.

## Consequences

**The overview page's "Last seen" column is now "Last observation"**, on both
tables, because that is what it measures. The count under a `silent` badge says
"observation datasets overdue" for the same reason -- the Datasets column two
along counts every kind, and a reader comparing "3 of 5" against a count of
twelve is owed the word that explains the difference.

**One reading order, not two.** The overview sorts by staleness and the
all-centres table sorts by the standing, over one region, so the two scales
rank the shared states in one sequence: a centre declaring no observations
sorts after what has gone quiet and before what is publishing, on both. The
first draft sorted it *last* on the overview -- reasoning that an absence is
not a fault -- while every surface put it fourth; a reading order nothing on
screen honours is worse than none.

**The table is no longer free of the time series.** It never was, quite --
volume comes from the rollups -- but the staleness column used to be one
indexed lookup per node and is now a share of the silence walk. That walk was
already being paid for on every open of this page, so the cost is a fold in
Python over rows already in memory rather than a new query.

**`NodeLastSeen` is still maintained and still read** -- by the node detail
page, where "when did anything last arrive from this centre" is a real
question. It just no longer decides a verdict.

**A centre that withdraws its observation datasets stops being stale.** The
fold reads the datasets a centre still publishes, which is the rule
`dataset_silence` already followed -- one the catalogue dropped is not
something anybody is waiting to hear from. So a centre whose observations were
retired moves from gone-quiet to the fourth state rather than staying a fault
for ever. That is the right reading of a withdrawal and the wrong one of a
mistake, and which it was is the drift report's question (ADR-0013) rather
than this column's.

## Not addressed here

**Reporting a centre that declares no observations as a finding.** It is a
state on the overview and a verdict on the front page; whether it also belongs
in a gap report is a question about the region's catalogue, and nothing is in
that state today.

**Which kind of observation stopped.** `synop` against `temp` is below the
category (ADR-0016), and the dataset rows on the node detail page already
answer it by name.

**Observation-scoped volume.** The sparkline and the message count are every
notification a centre published. Splitting them would put two numbers where the
column heading promises one, and the question they answer -- how much is this
centre publishing -- is not a question about observations.
