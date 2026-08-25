# 5. A centre nobody asked is not a centre that declares nothing

Date: 2026-08-25

Status: Accepted

## Context

A node's station registry is one of the three station pictures this tool
exists to compare, beside what OSCAR/Surface declares for the territory and
what is observed transmitting. It is read from an address on the node, and
until recently nothing wrote that address, so the sync had never run for any
centre on any install. A catalogue sync now fills `base_url` from a record's
`rel="canonical"` link and `save()` derives the registry URL from it.

Twelve of the thirty-two African centres the writing catalogue indexes carry
no canonical link on any of their records. They derive no address, so they
have no registry URL, so the hourly station sync passes over them in silence —
correctly, since there is nowhere to send the request. Two of the twelve
demonstrably do run registries: `bf-anam` answers with 71 stations and
`dj-anm` with 12, at hosts named only in a `license` link that #38 ruled out
as a source because a licence link is as often the agency's public website.

Every surface that reports on a centre's stations was reading that absence as
an answer. The node page said "No station is declared by this centre's
registry, and none has been heard transmitting under its topics". The
statistics tab said "Nothing is declared for this centre". The
transmitting-undeclared report listed every station of theirs as a
registration gap. The declared-but-silent report marked stations "No centre
declares it". Four claims about a centre, none of which anything had asked it.

This is the same class of mistake ADR-0004 named: a finding is about the
region, and a blind spot in the tool reported as a finding is worse than no
finding, because somebody acts on it. Here it would send a focal point to a
centre to ask about stations the centre's own registry may well declare.

## Decision

**Whether there is anywhere to ask is a fact about the node, and is named
there.** `WIS2Node.advertises_station_registry`, with
`advertising_a_station_registry()` / `advertising_no_station_registry()` on the
queryset beside it. Everything that reported the absence as an answer was
deriving it independently from an empty `stations_url`; the sync that skips
such a node and the pages that describe it now read the same property.

**Where the row mixes it with a second absence, the two collapse into one
answer.** A station transmitting undeclared has a centre that was asked, a
centre nobody could ask, or no centre on the record at all — and a centre no
catalogue has indexed cannot advertise a registry anywhere, so saying it
advertises none says nothing about it. Carried as `DeclaringCentre` on the
row, in the way `OriginWatch` carries the pair of vantage points, so the row,
the digest notice and the table cell each ask once rather than working the
same two flags out three times.

**The distinction is per-row where rows mix centres, and per-page where they
do not.** The node page and the statistics tab are about one centre, so they
say it once, at the top of the section, and every row beneath is read against
it. The transmitting-undeclared report mixes centres, so the row carries it —
and so does the digest notice, which is the one that arrives in somebody's
inbox as an errand.

**The rows stay.** A station transmitting under a centre nobody asked is still
traffic nothing accounts for, and dropping it would hide the least
accounted-for traffic there is. What changes is the claim beside it, not
whether it is listed. This is the opposite call from ADR-0004's withholding of
the unregistered-centres report, and for a reason: there, every row was
suspect, because the registry the question is put to had stopped answering.
Here the finding is sound and one column of it cannot be read as a claim about
the centre.

**`describe_caveat` is not `describe_bound`.** The declared-but-silent report
files its rows under a territory, not a centre, so no row can be told which
centre would have declared it — OSCAR declares against territories, which is
the whole reason that report is about countries. So the report counts the
centres nobody asked and says it once above the table. That is a new callable
on `GapReport` rather than a second meaning for the existing one: a bound is
about which findings reached the page and belongs on the index beside the
count, and a caveat is about what a column of the findings that did reach it
can be read to mean. A count that is right is worth opening whatever its
columns can distinguish, so the caveat stays off the index.

**The `license` fallback stays ruled out.** Guarding it by the wis2box path
shape (`/data` or `/metadata`) would recover `bf-anam` and `dj-anm`, and the
shape did separate the two working hosts from the three agency websites in the
sample. But it is a guess at an address dressed as a rule, and the honest
report of the gap is worth more than two recovered registries: the twelve are
a metadata defect their focal points can fix at the source, and a fallback
that mostly works would hide it.

## Consequences

**Four surfaces stop making a claim they were never entitled to**, and say
instead what is true: that nothing has asked.

**`GapReport` has a sixth callable**, used by one report. The argument against
it is the one ADR-0003 accepted for `find_unsettled` and ADR-0004 then
justified: one user is enough where the alternative is a second meaning for a
field that already has one.

**The station sync's behaviour is unchanged.** It already skipped these
centres and already declined to log a failure for them, for the reason its
docstring gives. What was missing was anything saying so where a reader would
see it.

**The twelve are still not asked.** Nothing here recovers a registry; the
issue was that the tool was lying about what it knew, and it has stopped.

## Not addressed here

**A report of centres whose catalogue records carry no canonical link.** It is
a finding about the catalogue's metadata quality rather than about station
data, and it is the list a focal point would chase. It wants its own report,
its own count on the index and its own digest notice, and the module docstring
that says there are five reports wants rewriting around six.

**Probing the `license` host to see whether it answers like a wis2box.** That
would settle the fallback question with evidence rather than a path shape, at
the cost of network calls inside the catalogue sync — which #38 ruled out for
the derived address for the same reason.

**A standing for "transmitting, and nobody asked whether it is declared".**
The four station standings are shared with the statistics tab's counts, its
export renderer and its filter control, and a fifth would ripple through all
of them to distinguish rows that the section's own sentence already covers.
