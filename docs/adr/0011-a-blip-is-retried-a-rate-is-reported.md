# 11. A blip is retried, a rate is reported

Date: 2026-08-31

Status: Accepted

## Context

The designated writer catalogue, `ca-eccc-msc-global-discovery-catalogue`, was
failing about half of its six-hourly runs. Three days of the sync log:

```
21857  failed  ('Connection aborted.', RemoteDisconnected(...))
21520  failed  ('Connection broken: IncompleteRead(32178 bytes read, 4122521 more expected)')
20529  failed  HTTPSConnectionPool(host='wis2-gdc.weather.gc.ca') Max retries exceeded
20123  failed  HTTPSConnectionPool(host='wis2-gdc.weather.gc.ca') Max retries exceeded
17057  failed  HTTPSConnectionPool(host='wis2-gdc.weather.gc.ca') Max retries exceeded
16614  failed  HTTPSConnectionPool(host='wis2-gdc.weather.gc.ca') Max retries exceeded
16262  failed  ('Connection aborted.', RemoteDisconnected(...))
```

Every one of them is the transport failing rather than the catalogue
answering. A connection refused or timed out, a connection closed without a
reply, a body that stopped 32 KB into itself. Not one is an HTTP status.

What that costs is set by the shape of the read. The collection is 559
records; at `limit=500` the whole of it arrives as one response of 4,154,699
bytes, served uncompressed with a fixed `Content-Length`, and takes between
four and nine seconds. So a run is one eight-second transfer, `fetch_pages`
asked once and gave up, and `sync_catalogue` treats any exception as a failed
run. One blip anywhere in those eight seconds, and the registry stood
unwritten for another six hours.

Asked repeatedly from elsewhere the host answered every time, so its
availability is not something this tool can mend. What it can mend is that a
single transient fault destroyed a whole scheduled run.

The failures were also announced by nothing. ADR-0004 exists precisely because
a frozen registry looks healthy, but its clock is the last run that brought
records back and its threshold is 24 hours — and with every other run
succeeding, that clock was never more than twelve hours old. The registry was
being rebuilt at half the rate the schedule promises, everything read against
it was that much staler than it looked, and the only evidence anywhere was
seven rows in a sync log nobody opens.

## Decision

**A page whose transport failed is asked for again — three times, backing
off.** A page is a GET; asking again is safe in the way retrying a write never
is, and it costs the source one more read of something it is already serving.
Three attempts over six seconds clears the great majority of what was failing
these runs, and a source that has said nothing three times in half a minute is
not blipping.

**Only the transport is retried.** A refused connection, a read timeout and a
body cut off partway have in common that the source said nothing at all. An
HTTP status is an answer, and asking a source to repeat an answer is a hope
rather than a retry; so is asking again for a body that arrived whole and was
not JSON. The three faults retried are the three these runs actually failed
on.

**How many attempts is the caller's, because what a lost run costs is the
caller's.** Three is the default and the catalogue sync takes it. The two
hourly per-centre reads ask once: the station registries are read every hour
against every centre advertising one, a large share of them at addresses
nothing answers at and some at hosts that hang until the timeout, and a
picture that moves in months has lost nothing by missing an hour. The archive
poll asks hourly for a window six hours deep, so every message in it is
already fetched six times over — a retry there buys redundancy the window has.

**A read that kept failing says so as itself.** `ReadKeptFailing` names the
source and how many times it was asked, and quotes the last fault. What a
reader needs from a sync log is not that a connection was aborted — it is that
this source was asked three times over half a minute and said nothing each
time.

**The page size is left where it is.** Half the failures were connect-level,
where the size of the response is irrelevant, and a smaller page is more
requests to fail rather than fewer. Retrying covers both halves; splitting the
read covers one of them and would want its own evidence.

**A catalogue failing a share of its runs is an eighth gap report,
`catalogues-that-keep-failing`.** The rate is the finding, because it is the
one thing no other surface can state: one failed run is on the catalogue's own
sync log and means nothing, and it took reading twenty-eight rows and counting
to learn that fourteen of them had failed. The row carries the share, whether
the catalogue writes, when records last came back, and what the last failure
said.

**Reported rather than alerted**, for the reason ADR-0006 gave the
not-answering report. It is a pattern over time rather than one bad run;
nothing the tool says is untrue while it stands, only staler than it looks;
and nobody can do anything about a foreign host at three in the morning.
Joining `GAP_REPORTS` gets it the index line, the page and the digest at once.

**The threshold is a fifth of runs failing over seven days, in settings**, and
is judged only once four runs have been recorded. A week of the six-hourly
schedule is twenty-eight runs, which is enough for a share to mean something
and short enough that a catalogue mended on Tuesday is off the report by the
weekend. A fifth is set by two things rather than by what looks bad. Somebody
decided six hours was current enough, and a catalogue losing one run in five
is delivering three rebuilds a day instead of four. And across a week, a fifth
is six separate failures — more than any single outage can produce, since an
outage long enough to cost six six-hourly runs lasts a day and a half and is
announced as staleness instead. Four runs to judge at all, because one failure
out of two is a hundred per cent of nothing.

**A catalogue failing every run is in it as well as being announced stale.**
They are the same catalogue read two ways — the rate says how it is failing,
the alert says what to stop believing — and a report that dropped a catalogue
at a hundred per cent would be a rate that stopped being reported exactly when
it got worst. The row says which case it is by having no date under "records
last read".

**A read-only catalogue is in it too.** ADR-0004 declined to say anything at
all about one going dark, and that stands for *alerts*: the tool works without
them and nobody should be woken for one. A row in a report is not an
interruption, the catalogue is a Global Service somebody should hear about,
and the row says which kind it is so a reader can tell a frozen registry from
a Global Service to report upstream.

## Consequences

**The writer's failure rate should fall to near nothing**, since every fault
recorded here was one a second attempt would have cleared. What remains after
that is a genuine outage, and that is what the report is for.

**A catalogue run now takes up to six seconds longer to fail**, and a
catalogue that is really down is asked three times per page rather than once.
Against a six-hourly schedule that is nothing. Nothing else got slower: the
reads that run hourly against fifty-four centres ask exactly as often as they
did.

**A reader can see the rate without opening a sync log**, which is the whole
of what the upstream half of this is: the availability of a foreign host is
not ours to fix, and reporting it is what we can honestly do about it instead
of retrying silently forever.

**`ReportedFinding` keys on the catalogue**, so a catalogue failing all week is
one finding announced once, and announced again if it comes back. A key
carrying the rate would announce the same catalogue afresh every time the
share moved by a point.

**Sync logs are load-bearing for a third thing.** ADR-0006 made them the
evidence for "never answered" and ADR-0010 for which records were lost; they
are now the evidence for a failure rate. A retention policy over `SyncLog`
would silently reset every one of them.

## Not addressed here

**Retrying an HTTP status.** A 502 or a 503 from a proxy in front of a
catalogue is arguably as transient as a refused connection, and the same
argument would extend to it. Nothing in the evidence here was a status, so it
would be a guess about a failure this tool has not seen.

**Asking for a smaller page.** See above: it addresses half the faults, it
costs more requests, and nothing measured here says a 500-record page is worse
than five 100-record ones.

**A rate for the syncs that are not catalogues.** The station registries have
their own report, which asks a different question — whether the registry can
be reached at all — and a rate over fifty-four centres' hourly runs is a
report whose shape should be decided by what it finds rather than by symmetry
with this one.

**Watching the writer's rate as a hard failure.** A writer failing three
quarters of its runs is heading somewhere ADR-0004's check will eventually
catch, and an alert that fired earlier on the rate would be a second threshold
against the same event. The report says it a day sooner and interrupts nobody.
