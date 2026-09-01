# 14. A centre retires its own datasets, and the history follows the real one

Date: 2026-09-01

Status: Accepted

## Context

ADR-0013 made the disagreement between a catalogue and a centre a report:
eleven identifiers across ten centres that a catalogue carries and the centre's
own metadata does not. That report reads and writes nothing, which was right
for a finding about a record registered somewhere this tool does not
administer.

It is not enough, because these rows are not inert. Measured over thirty days:

```
dataset                                      rollup rows    msgs
rw-rma:kedehn                                        995  67,685
gh-gmet:urn:wmo:md:gh-gmet:core...synop            6,725  24,001
zw-msd:urn:wmo:md:zw-msd:surface-weather...        1,728   8,489
sz-swazimet:surface-based-observations.synop         945   3,563
```

Every one of those messages arrived on a topic, and the resolver attributes a
message with no metadata identifier of its own to the one dataset that claims
its topic. Where the centre has stopped declaring a dataset and the catalogue
has not, that one claimant is the record the centre says is not theirs. So the
counts are filed under a ghost, the ghost counts toward the centre's silence
and volume, and one of them has learned a publishing rhythm -- two hours, from
244 observations -- entirely out of traffic that was never its own.

## Decision

**A centre's own answer decides whether its datasets exist.** A dataset a
Global Discovery Catalogue declares and the centre's own metadata does not is
retired: `status` moves to inactive, which is the word every surface already
reads -- silence judges the active ones, the centre's dataset count counts
them, and the resolver claims them. The row, its declarations, its rollups and
its history all survive; a retirement says what the centre publishes now, not
that the last two years did not happen.

**Only the centre reinstates it.** It does so by declaring the record again.
In particular the catalogue no longer stamps a dataset active on every run,
which it did until now: the retired dataset is exactly the record the catalogue
still carries, so a six-hourly run would undo every retirement and re-attribute
the traffic with it. Where the two disagree, the one that was asked directly
wins.

**The history is re-pointed, not split.** Those rollups were mis-keyed by this
tool's own resolver and not by anything the data claimed -- the traffic arrived
on the centre's synop topic, and the centre says its synop dataset is a
different one. Re-pointing corrects an attribution this tool got wrong.
Splitting would preserve the error as evidence, and leave the region's largest
observation feed showing a cliff to zero with a fresh series beside it in every
ninety-day window.

**Only where the centre leaves no doubt.** The successor is the dataset the
centre declares today on the ghost's topic, and only where it declares exactly
one. Djibouti declares `metar` and `speci` on a single topic; a run that
guessed between them would write a wrong history indistinguishable from a right
one. There the counts stay where they are, and which datasets the choice lay
between is recorded on the run.

**What the centre declares is the answer in hand, not the declarations on
file.** A `NODE` declaration is refreshed when the centre says the same thing
again and is never expired otherwise, so a rule that read one would retire only
datasets the centre has *never* declared -- which is the ghost the region has
today, and not the one it will have tomorrow, when a centre drops a dataset it
used to serve. It would also make a reinstatement a one-way door, because the
run that reinstates writes the declaration that would stop the dataset ever
being retired again. So the ghost set, and the successor, are both read from
the records the run just read, and the stale `NODE` declaration is deleted with
the retirement: the centre has just been found not to declare it, and a row
saying otherwise would have the divergence report reading agreement between two
sources that have this minute been found to disagree. What the catalogue said
is untouched, which is what keeps the finding a finding.

**Retirement is a conclusion from an answer, never from silence.** Nothing is
retired for a centre that could not be reached, for a centre that answered with
nothing at all -- an empty answer and an endpoint returning an empty page
mid-rebuild are the same bytes, and the difference between them is every
dataset the centre has -- or for a record this run read and could not store,
which is this tool failing rather than the centre disowning anything.

**So it belongs inside the node sync, per centre.** The ghost set is only
knowable when a centre answers, so this is not a migration that runs once
against the region: it runs where the answer arrives. The five centres
unreachable on any given sweep are reconciled whenever they come back, with no
special handling.

**The ghost's cadence baseline is deleted rather than moved.** It was learned
from traffic the ghost never earned, and the scheduled run relearns the rhythm
against the corrected rollups, for the dataset that now holds them. That run
now learns from live datasets only: a retired dataset is judged by nothing, and
where its history could not be moved, the next night would otherwise learn back
the very baseline the retirement deleted.

**The raw notifications move with the counts.** The rollups are derived from
them and a scheduled run recomputes the last forty-eight hours of buckets from
scratch, so messages left pointing at the ghost would rebuild its buckets
within the day and write the successor's merged hours back down -- the
correction undone at exactly the end somebody is looking at. They are also
where the wrong attribution was made, so this is the same correction rather
than a second one.

**What was retired is counted on the run, not only logged.** A retirement moves
a centre's largest observation feed from one row to another. `items_retired`
and `rollups_repointed` are columns on the sync log, and `retired` keeps which
datasets they were and where each history went -- because a count with no
record of what it moved between is a number nobody can check afterwards.

## Consequences

**The drift report still reports the retired dataset.** Retiring it settles
what this tool counts; it does not withdraw the record from the catalogue,
which is the centre's errand where it was registered. ADR-0013's rule stands:
the report reads and writes nothing, and now what it reports has a
corresponding state rather than only a row.

**Traffic that used to resolve to the ghost resolves to the successor.** The
resolver claims active datasets only, so the correction applies to arriving
messages from the moment of retirement, and the re-pointing applies it
backwards. The two together are why the history is continuous.

**A retirement is idempotent, and reversible by the centre alone.** A second
run over the same centre finds no active ghost and moves nothing. A centre that
starts declaring the record again gets it back active, with the history where
this tool last concluded it belonged -- which is on the successor, not back on
the reinstated row. That is deliberate: the counts were the successor's, and a
reinstatement does not make the old attribution true again.

**A dataset marked deleted by hand is no longer reactivated by a catalogue
run either.** Nothing in this tool writes `deleted` today, so the state can
only have been set by somebody who meant it; that it now stands until the
centre declares the record again is a consequence of the same rule rather than
a decision taken about it.

## Not addressed here

**An ambiguous ghost's history is never resolved.** Where several datasets
claim the topic the counts stay on the retired row, visible on the node page
and on the run that retired it, waiting for somebody who knows the centre to
say where they belong. Nothing offers them a way to say so yet.

**A centre that answers with nothing is not reported.** It retires nothing,
which is right, but "this centre served an empty metadata endpoint" is a
finding nobody is told: it reads as a successful run of nought records.
