# 13. A drift has a direction, and one report carries all three

Date: 2026-08-31

Status: Accepted

## Context

Until #132 a dataset had one source. A Global Discovery Catalogue published
what a centre once registered, this tool read the catalogue, and what the
centre itself serves at its own discovery metadata endpoint was fetched by
nothing. There was one picture, so there was nothing for it to disagree with.

There are two now, and they disagree. Measured against the region on the day
the second one started being recorded: eleven identifiers across ten centres
that a catalogue carries and the centre's own metadata does not, none the
other way round, and no field-level disagreement at all across the forty-three
identifiers both of them describe. Twenty-seven of the region's thirty-two
centres answered; five did not.

Every one of those eleven is somebody's errand. A catalogue advertising a
dataset the centre has stopped serving sends every consumer that reads a
catalogue — which is every consumer but this one — to a record that is not
there. And the reverse, when it happens, is worse: a centre publishing data
nothing reading a catalogue can discover is data nobody outside the region
knows exists.

## Decision

**One report, with the direction on the row.** Three findings — the catalogue
carries it and the centre does not, the centre declares it and no catalogue
does, traffic arrived and neither declares it — are one report because
measured against the region today only the first has any rows. Three reports
would be one report and two empty pages, and an empty page on the index is
read as a region with nothing wrong with it rather than as a direction nothing
has drifted in yet. If node-only ever becomes common, split then.

**The direction is a column rather than a filter.** It is the whole of what
makes a row actionable: the same table without it is a list of identifiers
somebody has to go and check one at a time, and the two directions are errands
in opposite directions.

**Presence, not fields.** What the report compares is whether both sources
describe the dataset at all. Nothing in the region disagrees about a dataset's
title, topic or policy, and a report comparing seven fields across
forty-three identifiers to find nothing would be a page of noise standing in
front of eleven findings.

**A centre nothing has read is bounded out, not listed.** This is ADR-0005's
rule at the dataset level, and it has to be, because the failure mode is
identical: a centre whose own metadata has never been answered for declares
nothing as far as this tool knows, so every record its catalogue holds would
read as a drift and eleven findings would be some hundreds. Its rows are
withheld and its centre ID is named in `describe_bound` instead — eleven
findings from twenty-seven of thirty-two centres is not "the region has eleven
drifts", it is eleven among the centres something could ask.

**The bound is read from the sync logs, and from every run rather than the
newest.** The probes are demonstrably flaky — `bi-igebu` failed one sweep and
answered the next — so a bound read from a live probe, or from whether the
last run worked, would move between two readings of the same page. What is
asked is whether anything has ever had an answer out of the centre, which only
stops being true by never having been true. A centre that answered a fortnight
ago and has failed every run since keeps its rows: what it last said stands,
which is the rule the sync writes by.

**The report reads and writes nothing.** It retires no catalogue record and
corrects no declaration. A stale global record is the centre's to withdraw
where it was registered, and a tool that quietly deleted the region's
catalogue records on the strength of a host that answered this morning would
be the worst version of this finding.

## Consequences

**The index has nine reports.** The count and the bound travel together on the
card, as they do for the propagation and unregistered reports, because a count
measured against twenty-seven of thirty-two centres is exactly the thing that
decides whether the report is worth opening.

**The dataset surfaces make ADR-0005's distinction, and do not need the
property to.** What that ADR insisted on is that a centre nobody asked is not
a centre that declares nothing, and this is the first dataset surface to say
so. But it draws the line at whether anything has ever had an answer rather
than at whether there is anywhere to ask, because the two ways of being
unasked are one absence here: a centre with no address and a centre whose
address never answers have both told this report nothing, and both belong in
the bound. So `advertises_discovery_metadata` still has no reader outside the
sync. The report that will need it is the one about unreadable dataset
endpoints — the not-answering report's twin, where the two absences are
different findings and only one of them is the centre's fault.

**A centre that starts answering moves its rows in, not out.** The digest
reads new findings as news, so the first run against a centre nothing had read
will announce whatever it disagrees with its catalogue about. That is correct
— they were findings all along and nothing had asked — but it means onboarding
a centre's metadata endpoint is a noisy morning.

## Not addressed here

**Field-level divergence.** Where both sources describe a dataset and describe
it differently — a topic renamed, a policy changed — nothing here says so.
There is none of it in the region today, and the declarations are kept whole
on both sides, so the report can be sharpened when there is something to
sharpen it against.

**Which catalogue carries a catalogue-only record.** One catalogue writes the
registry, so naming it on every row would be a column with one value in it.
Two readers indexing the region would make that a real question.

**A per-centre view of the same disagreement.** The node page lists a centre's
datasets and does not yet say which of them its own metadata declares. That is
where somebody chasing one centre would rather read this, and it wants the
same three states the station side already carries.
