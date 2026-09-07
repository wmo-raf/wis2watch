# 20. A node that answers is still asked whether the address is its own

Date: 2026-09-07

Status: Accepted

## Context

ADR-0018 split the registry between its two writers, and ADR-0015 drew the
line: the catalogue says that a centre exists and where to reach it, and
everything downstream of a successful fetch is the centre's own word about
itself. ADR-0015 then named the one case the line does not settle, under *Not
addressed here*:

> **A reachable node whose self-declared host differs from its stored
> `base_url`.** That is real drift and worth telling somebody about, but the
> address is the one field a centre's own record cannot settle by being served
> from it -- a node answering at all is not evidence that the address this tool
> holds is the one it should be asked at. It belongs in a report rather than in
> a write, and ADR-0013's is where it would go.

Two things in that paragraph are decided here, and one of them differently.

The state itself is worth a reader's attention precisely because nothing is
broken. The centre answers, the records come back, every count in the tool is
good. But a centre answering at `A` while publishing its canonical links from
`B` is saying the address we ask is not the one it considers its own -- which
is a host likely to move, on the day it moves.

Nothing in the region is in this state today. Thirty-one of the thirty-two
centres have answered, and every one of them publishes its canonical links
from the host it is being asked at. That is the same nil ADR-0015 measured for
its own descriptive fields, and the same reason for writing the rule now:
while nobody is depending on the wrong one.

## Decision

**It is reported and never written.** ADR-0007 licenses a correction to a
stored address only where the registry has been *reported dead*, and this
registry is answering. There is no finding to license a write, and there
should not be: a node answering at all is not evidence that the address is
right, but it is not evidence that it is wrong either, and a tool that
repointed a working address on the strength of a link would break the one
thing it can currently ask.

**It is a report of its own, not a row in ADR-0013's.** This is where ADR-0015
guessed, before either report had rows. The drift report is dataset-grained --
its rows are identifiers carrying a direction, and its count is a count of
records -- and this finding is one address per centre. A row here would carry
no identifier in a table keyed on them, and would add a count of centres to a
count of datasets, which is a number nobody could read. `unregistered-centres`
and `registries-not-answering` are the precedent for a node-grained gap
report, and this is the eleventh beside them.

**The third address is derived, never stored.** Each node already carries two:
`base_url`, the one being asked, and `advertised_base_url`, what the writing
catalogue last said (ADR-0007). The third -- what the centre's own records
point at -- is read out of the `canonical` link of its `NODE` declarations, by
the same `interpretation` seam that reads it out of a catalogue's copy. A
column would need a migration and a writer, and a writer for a value already
whole on the declaration is a second copy that can drift from the record it
was copied from, which is exactly the mistake ADR-0015 declined to make for
every field below the address. Measured against the region, the pass costs
fifty-one declarations, a hundred and sixty kilobytes and thirty-five
milliseconds.

**Reachable is the whole precondition, and the rest is the bound.** A centre
nothing has asked declares nothing, and one whose records this tool has never
read has no third address to compare -- ADR-0005's rule applied to addresses.
Which centres those are is asked of the discovery-metadata sync logs through
`centres_answering_for_what_they_publish`, the same helper the catalogue sync
defers to, so that the centres this report treats as having spoken and the
centres a sync stands back for can never be a different set.

**One record naming another host is enough, and the counts say how far.** A
wis2box writes its canonical links from the address it is configured with, so
records already published keep the old host until they are republished: a
centre part-way through moving is precisely a set where some records name the
new host and the rest still name the old. Waiting for every record to agree
would report the move only once it was over, which is the one moment the
finding is worth nothing. So a row is raised on the first record that points
away, and carries how many of the centre's records point there -- all of them
is a move that is done, three of eight is one under way, one of eight is a
record somebody hand-wrote.

**Whose the asked address is stays ADR-0007's exact-string test.** The report
says which two of the three addresses agree, and whether the asked one is this
tool's own is settled by comparing `base_url` and `advertised_base_url` as
stored, which is the comparison the catalogue sync makes before it will move
an address. The centre's declared host is compared as a host, because it only
ever was one: it is read out of a link and never typed, so there is no
keystroke to preserve.

## Consequences

**The day a host moves is the day it is reported**, rather than the day
somebody notices the address has stopped answering. The report and
`registries-not-answering` are the two halves of the same fault seen before
and after: this one names a centre that still answers and has told us where it
is going, and that one names a centre where the move has already cost us the
registry.

**Four addresses in the tool, and each still has one writer.** Nothing here
writes, so ADR-0018's split is untouched: the catalogue still owns `base_url`,
the node still owns everything below it, and the third address is not stored
at all.

**A page of this report is one extra query and every declaration's payload.**
That is what deriving buys the absence of a migration and a writer with, and
it is the trade to revisit if the region grows by orders of magnitude.

## Not addressed here

**A centre that answers and declares no canonical link anywhere.** It has told
this tool nothing about where it lives, so it is not a row -- and it is not in
the bound either, because nothing about it is being withheld: it answered, and
named no host. Reading that silence as a disagreement would report every such
centre for an address it never gave. Pinned by a test, as ADR-0015 pinned the
same absence for a broker.

**A centre whose records name several hosts none of which is the asked one.**
The newest is the one the row names, on the grounds that it is what the centre
says now. Whether a centre serving two hosts at once is itself a finding is a
different question, and nothing in the region is asking it.

**Correcting the address once the centre has said where it is.** The obvious
next move, and the one ADR-0007 forbids while the registry answers. The day it
becomes right is the day the registry stops answering, and that already has a
rule.
