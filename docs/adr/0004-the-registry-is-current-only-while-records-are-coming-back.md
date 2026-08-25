# 4. The registry is current only while records are coming back

Date: 2026-08-25

Status: Accepted

## Context

Exactly one Global Discovery Catalogue creates registry records. Nodes,
datasets and origin brokers are all written by the designated writer; the other
catalogues are fetched read-only so that their divergence from it is itself
reportable. Nothing announced it when that one catalogue stopped answering.

A writer sync that fails records its own `SyncLog` with an error message, and
the next scheduled run asks again six hours later. That is right for a blip.
For a catalogue that stays unreachable the consequences are silent and they
compound. The registry stops growing, so a centre that onboards to WIS2 next
week is never created, never subscribed to and never in the overview. The
read-only catalogues keep succeeding, so divergence is computed against a
frozen picture. The wildcard sweep keeps finding centre IDs absent from the
registry and keeps reporting them as unregistered, when the writer might have
had records for them all along.

The failure is visible only to somebody who opens a node detail page and reads
the sync logs — which is to say, to somebody who already suspects it. This is
the opposite failure mode to the two hard failures already here. A broker that
stops delivering empties the picture, and everything looks wrong. A registry
that stops being rebuilt freezes it, and everything goes on looking right and
getting quietly further from the truth.

Two things were already true and did not settle it. The sync log records every
run with its counts and its error, and the node detail page shows them. And the
`HardFailure` docstring already drew exactly the distinction that decides
this: findings are about the region, hard failures are about the tool, and a
hard failure means nothing the tool goes on to say can be believed until it is
fixed.

## Decision

**A writer catalogue that has not brought records back for far longer than its
schedule is a hard failure**, `CATALOGUE_WRITER_STALE`, alongside
`GLOBAL_BROKER_LOST` and `INGESTION_STALLED`. It gets the same once-per-spell
treatment `notified_at` gives the others: announced once however long it
lasts, and announced again when it clears.

**Freshness is measured from the last run that brought records back, not from
the last run that succeeded.** A catalogue answering 200 with nothing in it
passes every check this tool makes — the fetch worked, the run is green, the
`last_sync` stamp is fresh — and freezes the registry exactly as a refused
connection does. So the clock is read out of the sync logs, off the newest run
that did not fail and brought back more records than it stepped over, and
`last_sync` is left to mean what it has always meant. A run every record of
which errored reaches the registry no better than an empty answer does.

**The threshold is 24 hours against a six-hourly schedule** — three missed
runs with the fourth due — in a setting rather than a constant. One missed run
is a blip the next run fixes. Like every other threshold here it is a first
guess, meant to be revised once the region's rhythms are known.

**The detail says which of the three ways it is failing.** A run that failed
is a catalogue or a network to chase; a run that answered with nothing is a
catalogue that has lost the region's records; no run at all since is a
scheduler that has stopped. The last is the one nothing else in this tool
would ever say, because a sync that does not run leaves no failing sync log
to read.

**An installation with no writer designated is not one whose writer has
stopped.** It gets the silence a Global Broker nothing has been given already
gets. A writer that has never synced is a different thing and is reported,
timed from when it was first looked at, so the announcing threshold is the
grace a fresh installation gets.

**The unregistered-centres report is withheld outright while it stands**, and
says so in the sentence `describe_bound` already exists for. That report is a
question put to the registry — is this centre publishing that no catalogue has
indexed? — and while the registry is frozen the question has no answer: a
centre with no record cannot be told from a centre whose record this tool has
not read. Withheld rather than qualified, because a list of named centres with
a caveat above it is a list somebody acts on.

**Its findings are let go rather than held**, through the `find_unsettled`
mechanism ADR-0003 built for the propagation horizon. The grace period is no
use here: a writer unreachable for a week outlasts any grace, and every centre
the sweep had found would be mailed out as registered on the morning it ran
out.

**Each check carries its own consequence sentence.** The message ends on what
to stop believing while the failure stands, and a frozen registry costs
something different from a dark broker. Held one per check rather than as
branches in the template, so a fifth kind of breakage is one entry rather than
a fifth arm of an `if` somebody has to remember to extend.

## Consequences

**A frozen registry is now announced within a day**, and cleared when a sync
brings records back.

**A catalogue answering with nothing is caught by the same alert.** No other
check anywhere would call it a failure.

**The unregistered report goes quiet while the writer is dark**, and says how
many centres it is holding. A centre still unregistered when the catalogue
answers again is announced afresh — which is the right moment to say it, since
it has survived the registry catching up.

**`find_unsettled` has a second user**, which was the argument against building
it: ADR-0003 accepted it while noting it served one report's one case.

**A read-only catalogue going dark still says nothing.** The tool works
without them, and a divergence report computed while one is dark is quieting
in the same way — but there is no divergence report yet to suppress.

**Withholding follows the open row, not the message.** So an installation with
no alert recipient configured still withholds, and says why on the page; and a
fresh installation whose writer has not synced yet withholds through the day
of grace before anything is announced. Both are honest — the report cannot
stand behind what it would list either way — and the sentence beside it is
what a reader gets instead of a mail.

**Standing the writer down in the admin clears the spell**, and mails that it
recovered. Deleting it, deactivating it, or clearing its writer flag all leave
no writer designated, which this check reads as nothing to say rather than as
a failure. The message is wrong about what happened; the operator holding it
is the person who just did it.

## Not addressed here

**Automatic failover to another catalogue.** Sole-writer is deliberate —
records must not flap between catalogues that disagree — and promoting a new
writer is an operator's call, made in the admin.

**Suppressing divergence reporting.** Story 6 records how much each reading
catalogue found for the region; which records they disagree on is not built.
When it is, it will want withholding the same way, and against a stale reader
as well as a stale writer.

**Any measure of a read-only catalogue's staleness.** Nothing reads it yet, and
building the machinery ahead of the report that would use it would be guessing
at what that report needs.

**Letting a hard failure go rather than clearing it.** ADR-0003 built that for
findings, where a report can say which of its own it can no longer answer for.
The equivalent here is a spell that ends because the thing it was about
stopped being watched — a writer stood down mid-spell — and it wants a
mechanism the hard failures do not have. One misleading recovery message,
addressed to whoever caused it, did not seem worth building one for.
