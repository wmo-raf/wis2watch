# 2. Alert on duty cycle, not instantaneous state

Date: 2026-08-25

Status: Accepted

## Context

The hard-failure alerts announced a Global Broker connection that had been
unreachable for five continuous minutes, and announced it again when it came
back. That rule describes a broker badly, and the installation watching Africa
showed how badly.

From 13 August the Météo-France Global Broker — the one active Global Broker,
and so a single point of failure for the whole region — began flapping, and by
the 20th had settled into a plateau of roughly 50% downtime: drops of seven to
nine minutes, recurring about every fourteen. Measured over the seven days to
25 August:

| | |
|---|---|
| broker drops clearing the five-minute rule | 284 |
| ingestion stalls | 71 |
| **emails per week** | **~710** |

The daily shape, from the `HardFailure` ledger:

```
2026-08-11   0:00:00  ( 0.0%)   0 drops
2026-08-12   0:00:00  ( 0.0%)   0 drops
2026-08-13   2:03:00  ( 8.5%)  19 drops   <- onset
2026-08-16   0:54:00  ( 3.8%)   9 drops
2026-08-19   6:40:00  (27.8%)  15 drops
2026-08-20  11:14:00  (46.8%)  61 drops
2026-08-24  11:28:00  (47.8%)  40 drops
```

Two problems, not one. The mail was unreadable — an outage announced a hundred
times a day is an outage nobody reads about the second time. And the thing
actually worth saying was never said by any of those hundred messages: that
the tool was blind for half of every hour, so every centre it called silent
might have been publishing to nobody listening.

The five-minute rule could not say it, because it was asking the wrong
question. "Is the socket closed right now?" is not what an operator needs to
know. "Has this tool been able to watch?" is.

## Decision

### The alert's contract is a duty cycle

A spell of unreliability opens when the Global Broker connection has failed to
carry **45 minutes of the trailing 120**, and clears when that falls back to
**10**. A blackout is that measure at its maximum and reaches the same alert by
the same route, so this replaces the old rule rather than sitting beside it.

Opening and clearing are deliberately different numbers. A spell that closed
the moment the measure dipped below what opened it would close and reopen all
day and announce itself on each — the noise this exists to end.

The window matters more than the budget. A short window turns every bad hour
into news of its own; a long one takes hours to notice a blackout. Simulated
against the real seven days:

| rule | episodes | emails/week |
|---|---|---|
| 5 min continuous *(previous)* | 284 | 568 |
| 10 min in 60, clear 3 | 11 | 21 |
| 20 min in 60, clear 5 | 12 | 23 |
| 30 min in 60, clear 8 | 15 | 29 |
| **45 min in 120, clear 10** | **6** | **11** |
| 60 min in 360, clear 15 | 4 | 7 |

Note that a *tighter* budget yields *fewer* episodes: the low clearing mark
holds one spell open across the gaps rather than letting it close and reopen.

### The ledger is separated from the announcement

Every drop still opens and closes a `GLOBAL_BROKER_LOST` row and is announced
to **nobody**. Those rows are the evidence the window is measured over, so they
must keep being written whether or not anything is worth saying. The stretch in
which they add up to the tool not really watching is one
`GLOBAL_BROKER_UNRELIABLE` row, and that is what is announced.

This is why the duty cycle needed no new model or connection-state log: the
`HardFailure` table was already a complete downtime ledger, because
`_reconcile` opened a row on every drop regardless of whether the notification
threshold was ever met.

`HardFailureCheck` gained `announce_now(failure, *, now) -> bool` to carry the
policy, so each kind's rule lives in the registry rather than in branches
spread across the announcing and the checking.

### A spell is dated from the first drop, not from noticing

`started_at` is the start of the earliest drop inside the window at the moment
of breach — on real data, roughly two hours before the rule fires. The old
check genuinely could not date an outage (`last_connected_at` records when a
broker came *up*, and is left standing when it goes down); the duty-cycle
version can, because the drops beneath it say exactly when each began. That is
the number an operator would quote to whoever runs the broker.

### The stall is left fast, and silenced only as a second telling

`ingestion_stalled` keeps its 15-minute detection unchanged. It is the only
check that can notice the ingest process having died while its connection
records still read healthy, and it is the fast path for a total blackout —
firing well before the 120-minute window has the evidence to call the
connection anything.

It is suppressed **only while a spell of unreliability stands**, where the two
checks are one event described twice and the reader already holds the cause.
66 of 71 stalls in the sample week fall inside a spell. The suppression is
never of the first telling: a stall beginning with no spell standing is
announced at once, and a stall that *outlives* the spell which silenced it is
announced the moment that spell clears — the broker returning while traffic
does not is the most alarming thing this tool can report, and is precisely the
case the check exists for.

A suppressed stall still gets its row. Whether the tool was blind is exactly
what the record exists to answer.

### The digest owns up to a bad day

Two independent ride-along lines, broker and ingestion, each appearing only
when its own total for the last whole UTC day crosses 30 minutes. They never
cause a digest to be sent — a line that did would put a daily email back in
front of the reader through the side door.

They are asked separately and neither is inferred from the other. The day worth
showing is the one where the broker was faultless and ingestion stalled anyway,
and a single line blaming the stall on the connection would render it as a
clean day with an unexplained footnote.

They also catch what no alert can: **16 August**, 54 minutes lost across 9
drops, each too small to be news and never breaching any spell.

### The ledger becomes visible

`HardFailure` had no admin surface at all — 436 rows over 30 days, reachable
only from a shell. It gets a read-only `ModelViewSet` mirroring the outgoing
email archive. With 97% of the mail suppressed, the rows are where the story
now lives.

## Consequences

**Expected volume: ~710 → ~21 emails/week.**

`WIS2WATCH_BROKER_OUTAGE_MINUTES` is **removed**. Nothing announces a drop, so
the setting had no reader. An operator who had raised it to quieten the noise
loses nothing they wanted.

New settings: `WIS2WATCH_BROKER_UNRELIABLE_MINUTES` (45),
`WIS2WATCH_BROKER_UNRELIABLE_WINDOW_MINUTES` (120),
`WIS2WATCH_BROKER_RELIABLE_MINUTES` (10), `WIS2WATCH_BAD_DAY_MINUTES` (30).

**A single solid blackout is no longer announced at 5 minutes.** It is caught
at 15 by `ingestion_stalled` instead — *conditionally*. That backstop holds
because the Global Cache rides on the Global Broker connection and the one
active origin broker delivered 9 messages in 24 hours, so a blackout genuinely
stops everything the stall check counts. **Activate chatty origin brokers and
the backstop weakens**, leaving the 45-minute rule as the only broker alert.
Revisit these numbers if origin ingestion is switched on in earnest.

**On deploy the first beat fires immediately**, since the trailing window reads
existing history. That is correct — the connection *has* been unreliable for
two hours — and is the change proving itself.

**The order of `HARD_FAILURE_CHECKS` is load-bearing.** Drops must reconcile
before the spell measured over them, and the spell before the stall it
silences.

## Not addressed here

**Why Météo-France drops us**, and activating a second Global Broker as
failover. Failover alone would largely fix this, since the check only fires
when *all* active brokers are down. The 13 August onset is the lead; the ingest
container has `RestartCount 0` across the plateau, so it is not our process
dying.
