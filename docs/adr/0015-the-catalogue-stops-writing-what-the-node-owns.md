# 15. The catalogue stops writing what the node owns

Date: 2026-09-02

Status: Accepted

## Context

ADR-0013 recorded that a Global Discovery Catalogue and a centre's own
metadata disagree, and reported the disagreement. ADR-0014 let the centre
settle one kind of it: a dataset the centre has stopped declaring is retired
rather than left standing as a ghost that collects other datasets' traffic.

Neither touched the ordinary case. The catalogue sync still wrote every
descriptive field of every dataset on every run, six-hourly, over whatever the
hourly node sync had put there -- and the node sync only ever filled in blanks,
so on the forty-three identifiers both registries describe the catalogue was
the sole author of the canonical row. It also wrote each centre's own broker
from the catalogue's copy of a link the centre publishes itself.

That is upside down. A catalogue holds what a centre registered at some point;
the centre's own endpoint says what it publishes today, and where the two
differ the copy is the one that is out of date. The measured disagreement is
currently nil across all forty-three, so nothing in the region changes hands
today -- which is exactly why the rule is worth writing now, while nobody is
depending on the wrong one.

There was also nothing recording **who wrote a field**. ADR-0007 answered that
question for a node's address, with `advertised_base_url`: a value equal to
what the catalogue last said is one this tool put there and may take back, and
a value that differs is one somebody typed. Below the address, nothing had an
equivalent, so "the catalogue is wrong here and I have fixed it by hand" was
not a state the registry could hold for a dataset.

## Decision

**The catalogue tells us a centre exists and where to reach it. The node tells
us what that centre publishes.**

**The catalogue keeps only what a node structurally cannot supply.** That a
centre exists at all, and its `base_url` -- the address you need in order to
ask the node anything. A node reachable at the wrong address is a
contradiction rather than a disagreement, so the address stays the catalogue's
under ADR-0007's ownership test, unchanged. Everything downstream of a
successful node fetch is the centre's own word about itself.

**The origin broker is downstream of the fetch.** A centre's own record carries
the broker as an `items` link with a channel, in exactly the shape a
catalogue's copy carries it, and a centre is better placed than a third-party
catalogue to say which host it runs. So the node sync writes it, and the
catalogue sync stands back for any centre whose own metadata something has
read. Which centres those are is asked of the sync logs, through the same
helper the divergence report's bound is built from, so that the centres a sync
defers to and the centres a report treats as having spoken can never be a
different set. A centre that has never answered is still described by the
catalogue outright, which is the only thing describing it.

**Provenance is the declaration, and it is per field.** ADR-0007's test
generalised: a canonical value equal to what some source's stored declaration
says is that source's value, and a value equal to none of them is somebody's
correction. That needs no new column, because the declarations already keep
each source's record whole; what a source contributed is worked out again from
the record by the same mapping that wrote it, rather than copied beside it
where the copy could drift.

From which the two rules follow. **The node writes over a registry and never
over a person**: a value some source is on record as having said is one the
centre may correct, and a value no source ever said is left exactly where it
was found. **The catalogue writes nothing once the centre has spoken**, and
until then fills what is empty and takes back what it itself last said -- so a
centre nobody can reach is still described by the most recent thing anybody
has said about it, and a hand-correction survives both syncs by construction.

**A declaration this tool cannot read back accounts for nothing.** That errs
the safe way in both directions: for a value a source really did write it
costs one stale field until the next run, and for a value somebody typed it is
the whole point.

**A dataset the node has retired is never reactivated by the catalogue.** This
is ADR-0014's rule applied to existence rather than to a field, and it is now
also the shape of every other field: `status` is written by neither source's
descriptive pass, and only the centre moves it either way. With a six-hourly
catalogue sync and an hourly node sync, a catalogue stamping `ACTIVE` would
flap a retired dataset four times a day, and silence, volume, the resolver and
every centre verdict would move with it.

**`last_synced` is still written on every catalogue run**, whatever else is
not. It is when this catalogue last confirmed the record rather than anything
the record says, and a staleness nobody stamped is one no report can read.

**A creation writes the record whole.** There is nobody else's value to
displace, and a row with no title is one nothing can name on a page.

## Consequences

**A centre's own words reach the surfaces**, rather than stopping at a
declaration nothing but the drift report reads. Where the two registries
disagree about a title, a topic or a data policy, what a page shows is what the
centre says, and what the catalogue said is still whole on its own declaration
-- which is what keeps the disagreement reportable.

**A hand-correction now survives every sync**, and is recognised rather than
declared: nobody has to flag anything. `is_manually_managed` is still the
node-level instrument and still means what it meant; this is the field-level
one that ADR-0007 said was missing everywhere below the address.

**The catalogue's reach shrinks to what only it can answer.** For the five
centres unreachable on a given sweep nothing changes at all -- they are
described by the catalogue exactly as before, brokers included, and are
reconciled whenever they come back.

**One extra query per record.** Whether the centre has declared this dataset,
asked per record on the catalogue's path. It is an indexed lookup on a row the
sync is already holding, against a few hundred records in the region every six
hours.

**Two syncs now write the origin broker.** They cannot both write it for the
same centre, because the catalogue's arm is conditioned on the centre not
having answered -- but the rule lives in two modules, and the shared write is
in one place so that at least what an advertised broker becomes cannot drift
between them. The node sync writes it once for the run rather than once per
record: a centre has one broker however many datasets advertise it, and a row
updated per dataset would be the same value written a dozen times an hour.

## Not addressed here

**A reachable node whose self-declared host differs from its stored
`base_url`.** That is real drift and worth telling somebody about, but the
address is the one field a centre's own record cannot settle by being served
from it -- a node answering at all is not evidence that the address this tool
holds is the one it should be asked at. It belongs in a report rather than in
a write, and ADR-0013's is where it would go.

**A centre that answers and advertises no broker.** The catalogue stands back
on the strength of the centre having answered, not of the answer having named
a broker, so such a centre is described by neither source and keeps whatever
it already had. That is a staleness rather than a wrong value -- the row the
catalogue last wrote stands -- and every centre in the region advertises the
link, so the alternative was machinery for a case nothing is in. It is pinned
by a test rather than left to be discovered.

**Provenance for a value the source itself withdrew.** A source that stops
declaring a field leaves the last value it declared standing, because a record
that omits a title is not a record retracting one. Whether a field can be
retracted at all is a separate question from who owns it, and nothing in the
region is asking it yet.
