# 18. The registry has two writers, and each writes what only it can know

Date: 2026-09-03

Status: Accepted

Resolves [#138](https://github.com/wmo-raf/wis2watch/issues/138). Narrows
[ADR-0004](0004-the-registry-is-current-only-while-records-are-coming-back.md),
reversing one clause it opens on and leaving every decision it takes intact.
Extends [ADR-0007](0007-only-an-address-this-tool-wrote-is-one-it-may-take-back.md)'s
ownership test without changing a word of it. Gathers what
[#128](https://github.com/wmo-raf/wis2watch/issues/128),
[#131](https://github.com/wmo-raf/wis2watch/issues/131),
[#132](https://github.com/wmo-raf/wis2watch/issues/132),
[#133](https://github.com/wmo-raf/wis2watch/issues/133),
[#134](https://github.com/wmo-raf/wis2watch/issues/134) and
[#135](https://github.com/wmo-raf/wis2watch/issues/135) settled a slice at a
time, the last three under their own records --
[ADR-0013](0013-a-drift-has-a-direction-and-one-report-carries-all-three.md),
[ADR-0014](0014-a-centre-retires-its-own-datasets-and-the-history-follows-the-real-one.md),
[ADR-0015](0015-the-catalogue-stops-writing-what-the-node-owns.md).

## Context

ADR-0004 opens on a sentence that was a description of the world rather than a
decision taken in that record: *"Exactly one Global Discovery Catalogue creates
registry records. Nodes, datasets and origin brokers are all written by the
designated writer."* It was the premise the rest of that record reasoned from,
and it was true when it was written. It stopped being true a slice at a time,
and no record says so where it will be read: ADR-0013, ADR-0014 and ADR-0015
each take one step of the reversal and none of them names the clause it is
reversing. So a reader arriving at ADR-0004 for the sole-writer rule -- which
is the rule most likely to send somebody there -- reads a live record,
correctly marked Accepted, whose first two sentences are wrong.

Sole-writer was never a claim about who knows best. It was a claim about
collision: two catalogues writing one registry would flap records between two
copies that disagree, so one was designated and the rest were read for their
divergence. What was not noticed is that the rule bound a second thing at the
same time. The catalogues were the only sources, so *"only one writer"* and
*"only the catalogues write"* were the same sentence, and the second half was
never argued for.

The centre's own discovery metadata endpoint is not a second catalogue. It is
the thing the catalogues hold a copy of, and it is not in the collision the
rule exists to prevent: a catalogue and the centre it describes cannot flap a
record between them, because they are not answering the same question. The
catalogue answers *which centres are in WIS2 and where do you reach them*, from
an index it maintains. The centre answers *what am I publishing today*, from
the endpoint the index points at.

Four measurements pushed this over, none of them an argument about authority.

**The key was wrong, and it was failing silently.** `wmo_topic_hierarchy` was
globally unique, and a wis2box makes one dataset per station group with every
one of them on the centre's single `surface-based-observations/synop` topic. So
9 of 63 records in the region were rejected on every catalogue sync run, for at
least four days, recorded as `partial` and announced nowhere. Among them was
`urn:wmo:md:rw-rma:aws810`, Rwanda's largest observation feed at some 10,800
messages a week.

**The traffic was being filed under records the centres disown.** With the
topic as the key, a message carrying no metadata identifier resolved to the one
dataset claiming its topic -- and where the centre had stopped declaring that
dataset and the catalogue had not, that one claimant was a record the centre
says is not theirs. Over thirty days `rw-rma:kedehn` absorbed 67,685 messages
that way, and learned a two-hour publishing rhythm out of traffic that was
never its own.

**The two registries disagree, and only in one direction.** ADR-0013's
measurement, on the day the centres' own records started being kept: eleven
identifiers across ten centres
that a catalogue carries and the centre's own metadata does not, none the other
way round, and no field-level disagreement at all across the forty-three
identifiers both describe. Twenty-seven of the region's thirty-two centres
answered; five did not.

**Nothing in the region turns on the reversal today.** Nil field-level
divergence across all forty-three means the authority rule changes no value
currently on the page. That is not an argument for leaving it unwritten. It is
the argument for writing it now, while nobody is depending on the wrong one.

## Decision

**The catalogue says which centres exist and where to reach them. The centre
says what it publishes.** That is the whole split, and the line falls where it
does because of what each source structurally can and cannot know. A centre
cannot tell this tool it exists -- there is nowhere to ask before there is an
address -- and it cannot settle its own `base_url`, because a node answering at
all is not evidence that the address in hand is the one it should be asked at.
Everything downstream of a successful fetch is the centre's own word about
itself, including the origin broker, which the centre publishes in its own
record in exactly the shape a catalogue's copy carries it.

**ADR-0004's sole-writer rule survives, narrowed to what it was actually
about.** Exactly one Global Discovery Catalogue still creates registry records.
No other catalogue writes anything, the rest are read for their divergence, and
promoting a new writer is still an operator's call in the admin. What is
reversed is the second half of the clause -- that the catalogues are the *only*
writers -- and nothing about the first.

**Three sources declare a dataset, and none of them owns it.**
`DatasetSource` holds one row per source per dataset: `GDC` for a Global
Discovery Catalogue's copy of what a centre registered, `NODE` for the centre's
own record of what it publishes today, `OBSERVED` for a dataset traffic on the
wire names. Two rules hold them apart, the two the station picture is already
held together by. **Declaring is not owning**: the canonical row is shared and
a declaration sits beside it. **Fill, do not overwrite**: each source's record
is kept whole and as it said it, which is what makes a disagreement reportable
rather than merely lost. Which source a canonical field is currently holding is
worked out from the declarations rather than stored beside them, so there is no
provenance copy to drift.

**A dataset is `(node, identifier)`, and identifiers are never merged.** The
topic is not part of the key -- a centre sharing one topic between datasets is
the ordinary case, and `dj-anm` declares `metar` and `speci` on a single METAR
topic, which is what makes the constraint change necessary rather than merely
convenient. Where the catalogue and a centre name what may be the same dataset
differently, those stay two rows, because *same dataset, mangled identifier*
and *different dataset, the old one retired* cannot be told apart from the
outside, and a merge is the one of the two that cannot be undone. Which dataset
a message belongs to is settled by the message: `(node, metadata_id)` first,
`(node, topic)` only where exactly one active dataset matches, and unresolved
rather than arbitrary otherwise.

**A centre retires its own datasets, and nothing else does.** A dataset a
catalogue declares and the centre's own metadata does not moves to `inactive`
-- the word every surface already reads. The row, its declarations, its rollups
and its history all survive, because a retirement says what a centre publishes
now and not that the last two years did not happen. It is a conclusion from an
answer and never from silence: nothing is retired for a centre that could not
be reached, that answered with nothing at all, or whose record this run failed
to store, which is this tool failing rather than the centre disowning anything.

**A retirement re-points the history rather than splitting it.** Those counts
were mis-keyed by this tool's own resolver and not by anything the data
claimed, so moving them corrects an attribution rather than rewriting one --
and only where the centre leaves no doubt, meaning it declares exactly one
dataset on the ghost's topic. Splitting would preserve the error as evidence
and leave Rwanda's largest observation feed showing a cliff to zero with a
fresh series beside it in every ninety-day window. Where the successor is
ambiguous the counts stay put and the run records which datasets the choice lay
between.

**Nothing a centre has retired is resurrected by a catalogue.** The catalogue
sync stamps `status` on no record it refreshes, in either direction; a record
it creates takes the active default the model gives it, there being nobody
else's answer to displace. The retired dataset is precisely the record the
catalogue still carries, so a six-hourly run asserting `ACTIVE` would undo
every retirement and re-attribute the traffic with it -- flapping a dataset
four times a day, and moving silence, volume, the resolver and every centre
verdict with it. This is the same rule as the field-level one, applied to
existence: the source that was asked directly wins, and the other one stands
back.

**The disagreement is a report, and the report reads and writes nothing.** One
report with the direction on the row, because the two directions are errands in
opposite directions and neither is this tool's to run: a stale global record is
the centre's to withdraw where it was registered, and a tool that deleted the
region's catalogue records on the strength of a host that answered this morning
would be the worst version of this finding.

**The report is bounded by which centres have ever answered, and says so.** A
centre whose own metadata has never been read declares nothing as far as this
tool knows, so every record its catalogue holds would read as a drift, and
eleven findings would be some hundreds -- ADR-0005's mistake made about
datasets. Its rows are withheld and its centre ID is named in the bound
instead. The bound is read from every sync log rather than from the newest run
or a live probe, because the probes are demonstrably flaky and a bound that
moved between two readings of one page is a page nobody can quote. The same
helper answers which centres the catalogue sync leaves the origin broker to, so
the centres a sync defers to and the centres a report treats as having spoken
cannot be a different set. Below the broker the deferral is per dataset, on
whether the centre has declared that record.

**The centre is asked hourly and the catalogue six-hourly, and the ratio is
part of the rule rather than a scheduling detail.** The authority split is what
makes it coherent: the source that changes is asked often and the index that
confirms is asked rarely. It is also what makes the no-resurrection rule
load-bearing rather than tidy, since a catalogue permitted to write existence
would spend four of every six hourly runs undoing the answer the centre had
just given. Each centre is its own task in the fan-out, because several of the
region's hosts hang until the timeout and one of those must not hold up the
region; the minute belongs to the endpoint rather than to the centre, so that a
centre's two endpoints are not asked in the same one.

**ADR-0007's principle is extended, and its text is unchanged.** *Only an
address this tool wrote is one it may take back* is now also *only a source
that declared a thing may retire it* -- the same rule, applied to existence
rather than to a URL. ADR-0007 answered the question for one field and named
the gap below it; this closes the gap without touching the answer. A canonical
value equal to what some source is on record as having said is that source's
and may be taken back; a value equal to none of them is somebody's correction
and is left exactly where it was found. `advertised_base_url` is still the
instrument for the address, `is_manually_managed` is still the node-level one,
and neither means anything different than it did.

## Consequences

**Every decision ADR-0004 takes stands whole.** `CATALOGUE_WRITER_STALE` is
still a hard failure with the same once-per-spell treatment; freshness is still
measured from the last run that brought records back rather than the last that
succeeded; the unregistered-centres report is still withheld outright while it
stands; its findings are still let go through `find_unsettled` rather than
held. None of that depended on the catalogue being the only writer. All of it
depends on the catalogue being the only thing that can say a centre exists,
which this record affirms rather than touches: a dead catalogue still means new
centres never appear, are never subscribed to and are never in the overview.

**One of ADR-0004's consequence sentences narrows.** *"The registry stops
growing"* was true of the whole registry and is now true only of which centres
exist. Dataset records keep flowing from the nodes while the writer catalogue
is dark -- retirements, titles, topics, policies and origin brokers all --
which makes the frozen picture smaller and more sharply bounded than that
record could describe. It does not make the failure less severe: the centres a
frozen registry hides are the ones nothing else in this tool can discover.

**The reversal is now readable from ADR-0004 itself**, through a pointer added
under its status and nothing else. Superseding it was the alternative and would
have retired four correct decisions in order to change one clause, leaving live
design in a record marked Superseded and a reader chasing it. Amending in place
is the convention for a clarification, and this is a reversal: it has its own
date and its own evidence, and it should read as one.

**A hand-correction survives every sync, and is recognised rather than
declared.** Nobody has to flag anything, because a value no source ever said is
one no source may take back.

**The registry has a second writer, with all that follows.** What the two
syncs may each touch, how the origin broker is kept from being written twice,
and what a centre that answers and advertises no broker keeps are ADR-0015's to
state, and it states them.

**The vocabulary this settled is now written down.** `CONTEXT.md` exists at the
repo root, which `AGENTS.md` has been pointing at for some time, and carries
*declaration*, *drift*, *retired*, *observed dataset* and *node truth* along
with the finding-versus-hard-failure distinction the `HardFailure` docstring
had been the only home for.

## Not addressed here

**Automatic failover to another catalogue.** Unchanged from ADR-0004, and
unaffected: the split gives the centres authority over what they publish, not
over whether they exist, so a dark writer still has no stand-in.

**Field-level divergence between the two registries.** There is none in the
region today. The declarations are kept whole on both sides, so the report can
be sharpened whenever there is something to sharpen it against.

**A reachable node whose self-declared host differs from its stored
`base_url`.** Real drift, and the one field a centre's own record cannot settle
by being served from it. Left open by ADR-0015 and still open: it belongs in a
report rather than in a write.

**Whether a source may retract a field.** A source that stops declaring a
title leaves the last one it declared standing, because a record that omits a
title is not a record withdrawing one. Existence is retractable and fields are
not, which is a difference this record asserts rather than argues: nothing in
the region is asking the question yet.
