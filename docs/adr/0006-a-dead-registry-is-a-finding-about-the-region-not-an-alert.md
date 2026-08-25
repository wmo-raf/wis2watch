# 6. A dead registry is a finding about the region, not an alert

Date: 2026-08-25

Status: Accepted

## Context

One of the three station pictures this tool compares is what a centre's own
registry declares, and it is the only one asked for directly: no catalogue
carries it, so `sync_node_stations` goes to each centre hourly and writes a
`SyncLog` with `sync_type=NODE_STATIONS` per node per run.

A node's `base_url` is filled from its records' `rel="canonical"` link
(ADR-adjacent, decided in #38), and filled **once** — blank addresses only,
never written over. Fill-once cannot self-heal. Four of the region's centres
publish their canonical links from bare IP addresses — `cm-meteocameroon`
(`197.159.3.42`), `sn-anacim` (`213.154.77.59:8002`), `sz-swazimet`
(`136.156.134.190`), `ly-lnmc` (`102.209.32.134`) — and hostnames move too.
When one does, the stored address keeps pointing at the dead host, the hourly
sync keeps failing against it, and nothing surfaces the pattern.

The same blindness covers a centre that never had a working registry: an
address derived from a canonical link that is simply not where the API answers
fails identically, forever, in silence.

Everything needed was already recorded. Thousands of failed sync logs, one an
hour per centre, and the only thing that reads them is a node's own detail
page — which is to say, they are legible to somebody who already suspects the
centre. Nobody is watching 54 countries for host changes; that they need not
is the premise of the tool.

## Decision

**It is a sixth gap report, `registries-not-answering`**, beside the five
already there. It is a pattern over time rather than one bad run, which is
what the gap reports are for and what the propagation and staleness reports
already look like. Joining `GAP_REPORTS` gets it the index line, the page and
the digest at once, which is the whole reason that list is a list.

**Not a hard failure.** `alerts.py` is for failures of this tool where every
answer it goes on to give is an answer about its own blindness. One centre's
dead registry costs one of three station pictures for one centre; everything
the tool says about everywhere else stays good. Twenty of them would be twenty
interruptions for something nobody can fix before morning.

**The threshold is 48 hours of every run failing**, in a setting. Against the
hourly sync that is some fifty consecutive failures — past a host restarting
overnight or a certificate renewed badly at lunchtime, and still inside the
week the registry went. Like every threshold here it is a first guess.

**"Every run since" is derived, not counted.** The newest run being later than
the newest run that answered is exactly the statement that nothing since that
answer got one, whatever number of runs sits between them. So a week in which
the schedule itself was down cannot be read as a week of failures, and no
count of failures has to be kept in step with the streak it describes.

**A partial run answered.** The question this report asks is whether the
registry can be reached at all. A run that read it and stepped over a record it
could not store reached it; that is the node page's finding, not this one's.

**A registry that never answered is timed from when it was first asked**, and
named apart from one that answered and stopped. They are two errands. An
address that worked and stopped is a host that has moved, and the centre is
the only one who knows where to; an address that never answered was wrong from
the moment it was derived, and the question is where the API is at all. A
report that called both "failing" would send an operator to a centre to ask
about a host this tool invented.

**A centre nobody asked is not in it.** It advertises no registry, so it has no
address to fail against and no sync log — the distinction #39 drew for the
station reports, applied here. Naming it would report a failure that never
happened.

**The last run's error is on the row and in the digest line**, cut to one line.
A refused connection, a read timeout and a 404 are three different
conversations, and a report that said only "failing" would send somebody to
open the sync logs it exists to have read for them.

**When none of the registries answer, the report says so and lists them
anyway.** A handful failing is the region; every one failing at once is far
more likely to be here — an outbound route lost, a proxy retired — and the
sync logs of the two cases are identical. Said rather than withheld, unlike
the frozen-registry case in ADR-0004: these rows stay true either way, because
the tool really is failing to read those registries, and withholding them
would hide the only evidence of the fault. What must not happen is somebody
taking thirty of them to thirty centres, and a sentence prevents that. Nothing
is said of a single centre failing alone — "every registry is failing" over a
set of one is a coincidence dressed as a pattern.

## Consequences

**A registry that has failed every run for two days is now named**, on the
index, on a page, and once in the morning digest — and once more when it
starts answering again, which is the only good news this report can bring.

**`ReportedFinding` keys on the centre**, so a centre's registry is one finding
however long it stands, and the ordinary grace period applies to its absence.

**Nothing is let go unsettled.** Unlike propagation gaps, a registry's silence
never stops being checkable: the next hourly run answers the question again.

**Sync logs are load-bearing history now.** Nothing prunes them, and the
"never answered" standing is read off their earliest row, so a retention
policy over `SyncLog` would silently turn old failures into new ones.

## Not addressed here

**Re-asserting the address from the catalogue.** #38 rejected re-assertion as
the default and this report does not reopen it: the report names the centre
and changing a stored address stays an operator's call in the admin. Two
narrower rules were considered — re-assert only where the address has never
worked, and re-assert wherever this report's finding stands. The first does
not cover the case that prompted the issue, since a moved host is one that
worked; the second is coherent but is a write, and worth deciding once the
report has shown how often the catalogue actually knows better.

**Saying it on the node's own page.** A centre's page already lists its sync
runs, and a standing derived once and shown in both places is the next thing
to want. It is not needed for the failure to stop being silent.

**Carrying the all-failing sentence into the digest.** `describe_caveat` is
read by the report's page and by nothing else, which is where the existing
caveat plumbing ends. The digest is the surface that would mail thirty notices
with nothing beside them, so it is the surface that most wants the sentence --
but giving it one means a second channel for every report's caveat, and that
is a change to the digest rather than to this report.

**Probing the address to tell a dead host from a wrong path.** The sync's own
error already distinguishes a refused connection from a 404 well enough to
start the conversation, and a prober that ran against hosts known to hang
would be minutes of waiting for a sentence the sync log already wrote.
