# 7. Only an address this tool wrote is one it may take back

Date: 2026-08-25

Status: Accepted

## Context

#38 fills a node's `base_url` from its records' `rel="canonical"` link and
fills it **once** — blank addresses only, never written over. ADR-0006 and #40
then made the cost of that visible: a sixth gap report names any centre whose
own station registry has failed every run past 48 hours, on a page and in the
morning digest.

Naming it is where it stopped. The tool held an address it had just published
as dead, the writing catalogue was often advertising a different one, and
nothing put the two together. Four of the region's centres publish their
canonical links from bare IP addresses — `cm-meteocameroon` (`197.159.3.42`),
`sn-anacim` (`213.154.77.59:8002`), `sz-swazimet` (`136.156.134.190`),
`ly-lnmc` (`102.209.32.134`). Those are the addresses most likely to move, and
the ones a correction would most often fix.

Two rules were weighed in #40 and neither was taken then. *Only where the
address has never worked* is safe and does not cover the case that prompted
any of this: a moved host is one that worked. *Wherever the finding stands*
covers both, and on its own has a failure mode worse than the problem — if the
catalogue's address is also dead and an operator keeps correcting it by hand,
the sync overwrites their work every six hours for as long as the registry
stays down.

That is the part neither rule answered, and it is not about thresholds. It is
that nothing recorded **who wrote the address**. `is_manually_managed` exists
and is per-node and manual: it stops the catalogue touching a node at all,
which is more than an operator correcting one URL is asking for.

## Decision

**The finding licenses the write.** A registry named by the not-answering
report is one whose address this tool has itself published as dead, and an
address published as dead is one it may stop asking. A registry merely failing
this morning is not — hosts restart. The sync asks
`registries_not_answering_centre_ids` rather than working the condition out
again, so the address a sync corrects and the address a page reports as dead
can never be a different set of centres.

**Provenance protects the operator, not the threshold.** A new
`advertised_base_url` records what the catalogue last said. An address equal to
it is one this sync put there and may take back; an address that differs is one
somebody typed, and is never overwritten however dead its registry. This makes
the flap structurally impossible rather than unlikely.

**What is advertised is recorded even when nothing else is written.** It is
bookkeeping rather than a claim, it is what makes the next comparison mean
anything, and an operator reading the two side by side can see what this sync
thinks the centre says about itself. The field is not editable: it is a record
of what was received, and a hand-edited one would only corrupt the ownership
test it exists to answer.

**The existing rows are assumed to be the catalogue's.** Nothing recorded who
wrote them, so the migration backfills `advertised_base_url` from `base_url`
for every node not flagged manually managed. That is a reading of the existing
contract rather than a fact in the data: the sync already overwrites such a
node's country and its broker, so a hand-corrected node that is not flagged is
being written over today. Leaving them blank was the alternative and does not
work — a node whose host moved before this shipped would match nothing the
catalogue now says and would read as somebody's correction for ever, which is
exactly the node this exists to repair. Manually managed nodes keep the blank,
which reads as "not the catalogue's" and holds them out for good.

**Derived endpoints move with the address, where they were derived.**
`WIS2Node.save` fills an endpoint only where one is missing, so writing the
base URL alone leaves a node asking the host it has just left — #38's
`update_fields` trap one layer down, and immune to the same fix, because there
is a value there and `save` will not recompute over it. So the sync moves each
endpoint itself, and only where it equals what the old address would have
derived. Anything else belongs to a centre that does not serve the wis2box
paths and an operator who has said so. The paths now live in one place,
`DERIVED_ENDPOINTS`, because they are written twice and two copies that drifted
would leave a node asking half its endpoints at a host it had left.

**The centre's message archive moves with it too**, under the same ownership
test. Left behind it would point at the host this sync has just established is
not there — the same silent staleness one door down, on a vantage point whose
reachability is reported beside the registry's.

**A correction is logged at warning level**, naming both addresses. An address
that changes itself overnight in silence is what costs a diagnostic tool its
credibility on the row that mattered.

## Consequences

**A centre whose host moves now heals itself within six hours of the finding
standing**, without anybody watching 54 countries.

**An operator's correction is safe by construction**, and has two ways to stay
that way: an address that answers is never re-asserted, and an address that
differs from the catalogue's is never re-asserted whether it answers or not.

**A dead registry the catalogue still agrees with is left completely alone.**
There is nothing to correct it to; the host is simply down, and the report goes
on saying so.

**The catalogue sync writes where it used to only fill**, which is a real
widening of what one unattended job may do to the registry. It is bounded by
the finding, by provenance, and by the sole-writer rule that was already there.

**One extra query per writer sync**, read once before anything is written.

## Not addressed here

**Provenance for anything but the base URL.** The endpoints and the archive URL
are told apart by whether they equal what the base URL would have derived,
which is exact for the wis2box convention and says nothing about a field
nothing derives. A node's name, country and broker are still the catalogue's
outright.

**Telling an operator in the digest that an address was corrected.** It is
logged, and the finding clearing the next morning is the visible half. A digest
line about the tool's own edits is a different kind of message from the
findings around it, and wants deciding on its own.

**Re-asserting anything for a manually managed node.** The early return stands.
