# 19. What a reading catalogue carries is kept outside the registry

Date: 2026-09-07

Status: Accepted

Resolves [#139](https://github.com/wmo-raf/wis2watch/issues/139). Pays off a
promise [ADR-0004](0004-the-registry-is-current-only-while-records-are-coming-back.md)
made and never kept, under the sole-writer rule
[ADR-0018](0018-the-registry-has-two-writers-and-each-writes-what-only-it-can-know.md)
narrowed but left standing. Answers the question
[ADR-0013](0013-a-drift-has-a-direction-and-one-report-carries-all-three.md)
set aside as not yet real: which catalogue carries a record.

## Context

ADR-0004 says the catalogues that do not write the registry are *"fetched
read-only so that their divergence from it is itself reportable."* The
divergence was never computed. A reading catalogue's sync counted the records
it found for the region and discarded every one of them, so one of the three
found sixty-three records every six hours and nothing has ever looked at them.

That was defensible while there was nothing to compare a record against. It
stopped being defensible with [#131](https://github.com/wmo-raf/wis2watch/issues/131):
a declaration now names the catalogue that made it, so which catalogue said
what is a thing the schema can hold, and the comparison becomes a query rather
than a feature.

The finding it makes possible is a WIS2-level one rather than a regional one.
Two Global Discovery Catalogues are meant to be copies of one index, and where
they disagree about what a centre publishes, one of them is the record some
part of the world discovers this region through. A centre missing from a
catalogue is invisible to everybody reading that catalogue; a centre present
in one and absent from the registry's is a centre nothing here is watching at
all.

Two facts about the region shape the rest. Only two catalogues can be
compared at launch, because the third has never completed a run
([#130](https://github.com/wmo-raf/wis2watch/issues/130)) -- and a catalogue
that has never answered carries nothing as far as this tool knows, which is
the shape of ADR-0005's mistake one level up. And the writing catalogue is
demonstrably fallible: nine of the region's sixty-three records were being
stepped over on every run for at least four days, so "the registry's catalogue
has it and the other does not" is not a synonym for "the other one is wrong".

## Decision

**A reading catalogue's records go in a table of their own.** Every other
source's record of a dataset is a `DatasetSource` beside the canonical row,
and this one cannot be, for two reasons of which the second decides it. A
declaration hangs off a `Dataset`, so recording one is either a registry row
the catalogue was not entitled to create or a row it was -- and the record
most worth reporting is precisely the one with no canonical dataset to sit
beside: a record another catalogue carries for a monitored centre the registry
has never heard of. `ReadingCatalogueRecord` is keyed on the catalogue and the
identifier, and names its centre itself.

**Nothing a reading catalogue says reaches the registry, and the schema is
what says so.** No node, no dataset, no broker, no declaration, no status. The
rule was previously kept by a sync remembering to write nothing; it is now
kept by there being nowhere for a reading catalogue to write. ADR-0004's
sole-writer rule is untouched: exactly one catalogue creates registry records,
and promoting another is still an operator's call in the admin.

**Presence, not fields.** What is compared is whether both catalogues carry
the record at all. They hold copies of one registration, so a field-level
comparison would be a page of noise standing in front of the findings -- the
same judgement ADR-0013 made about a centre and its catalogue, for the same
reason, and reopenable the same way when there is something to sharpen it
against. The record is kept whole on both sides, so nothing has to be fetched
again to sharpen it.

**One report, with the catalogue and the direction on the row.** The catalogue
is the column ADR-0013 left out while one catalogue was the only one being
written down, and it is the whole of what makes a row actionable: the errand
is with the catalogue that is missing the record, or with the one carrying a
record the registry has never seen. The two directions are one report for
ADR-0013's reason -- one of them may have no rows in the region, and an empty
page on the index reads as nothing being wrong rather than as a direction
nothing has diverged in yet.

**Each catalogue is compared with the registry's, not with the others.** The
registry is the axis because it is the picture everything else in this tool is
built from: a divergence from it is a statement about what this tool is
watching, and two readers agreeing with each other and disagreeing with the
writer is exactly the case where that matters most.

**A catalogue nothing has ever got records out of is not compared, and is
named instead.** ADR-0005's rule a level up: a catalogue that has never
answered carries nothing as far as this tool knows, so every record the
registry holds would read as a divergence, and a handful of findings would be
some hundreds. What counts as having answered is the predicate the registry's
own staleness is measured by -- a run that brought records back -- so a run
that failed, and a run that answered with nothing at all, are both silence.
Read from every run rather than the newest, because a catalogue that answered
a fortnight ago and has failed every run since has still said what it carries.

**Silence is never read as agreement.** A run that could not read a catalogue
through writes nothing, so what it last carried stands and is dated. Clearing
its records on a refused connection would turn an unreachable catalogue into
one that agrees with the registry about everything, which is the opposite of
what is known. The bound says the same thing for the catalogue that has never
answered at all: an empty report with nothing beside it announces that the
region's catalogues carry the same records, and that is the one thing this
report cannot know about a catalogue nothing has read.

**Nothing is deleted, and what is compared is the newest complete read.**
The record a catalogue once carried is kept, in the way every declaration in
this tool is kept. What the report reads is the picture the newest run that
brought records back confirmed: a record that run did not see is one the
catalogue has withdrawn, which is precisely the divergence this report exists
to find, and a record that went on counting as carried because it was carried
in March would hide it for good. A run that failed, and one that answered with
nothing, move nothing -- so a catalogue failing every run since Tuesday is
still compared on what it said on Tuesday.

**A record the newest run stepped over is still carried.** That run read it
and could not store it, which is this tool failing rather than the catalogue
withdrawing anything. ADR-0010 keeps which records a run lost on the run
itself, and this is the question that list was worth keeping for. It is why
the currency rule above is safe to apply: the two ways a record can be missing
from a run are told apart by the run's own record of it.

**The comparison is dated rather than withheld.** ADR-0004 expected a
divergence report to be suppressed by a stale writer, the way the
unregistered-centre report is. A date reads better here. The rows stay true
either way -- a record this tool holds and another catalogue does not really
is a difference -- and what a reader needs is to know which side is stale, so
every catalogue in the comparison carries the instant it was last read
through, the writer among them. Agreement is the absence of a row and an
absent row carries no date, which is why the dates are said once above the
table rather than left to the rows.

**The report reads and writes nothing.** Neither catalogue is corrected from
the other. Which of them is wrong cannot be settled from here, and a tool that
wrote a record into a global catalogue on the strength of another one would be
the worst version of this finding.

## Consequences

**The index has ten reports.** The count and the bound travel together on the
card, as they do for every other bounded report: a count measured against two
of the region's three catalogues is exactly the thing that decides whether the
report is worth opening.

**A reading catalogue's sync log starts saying what it did.** Its records are
created and updated and counted as such, and a record it cannot store is
stepped over with its reason, exactly as the writer's are (ADR-0010). A run
that lost records already had a report of its own, which is where that finding
stays.

**A record no run has ever managed to store reads as a divergence.** The
stepped-over list keeps a record a run lost from reading as withdrawn, so this
is only true of one that has never been stored at all -- and of one lost past
the fifty a run records, which is a fault reported as one in its own right.

**The first run against a catalogue nothing had read is a noisy morning**, in
the way onboarding a centre's metadata endpoint is. Whatever it disagrees with
the registry about has been true all along and nothing had asked.

## Not addressed here

**Field-level divergence.** Where both catalogues carry a record and describe
it differently, nothing here says so. The records are kept whole on both
sides, so this can be sharpened without another sync.

**Readers compared with each other.** Two reading catalogues that agree with
each other and not with the writer produce a row apiece saying the same thing.
Naming that as one finding needs a third catalogue that answers, which the
region does not yet have.

**A record the writer catalogue has withdrawn.** The currency rule above is
applied to the reading catalogues and not to the writer, whose declarations
say what it carried whenever it last confirmed them and are never withdrawn.
So a record the writer has dropped goes on counting as carried, and neither
this report nor the drift report says so. Both sides would want it, and how
the drift report reads a catalogue declaration is ADR-0013's ground rather
than this record's -- changing it here would leave two reports disagreeing
about the same rows.

**A per-centre view of the same disagreement.** The node page lists a centre's
datasets and does not say which catalogues carry each of them. That is where
somebody chasing one centre would rather read this, and it is the same gap
ADR-0013 left open for the drift.
