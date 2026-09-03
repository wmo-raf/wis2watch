# WIS2Watch

One context: a diagnostic tool that watches whether the WMO Information System
2.0 centres in a region are publishing, and reports where they are not. It
reads the region's registries and its message traffic; it writes nothing back
to WIS2.

## Language

### Who publishes

**Centre**:
An organisation publishing to WIS2, identified by its centre ID (`rw-rma`).
The model is `WIS2Node`, and a centre has exactly one.
_Avoid_: agency, provider, member.

**Node**:
A centre's own endpoints -- its discovery metadata, its station registry, its
broker, its message archive. Where prose says *centre* it means the
organisation; where it says *node* it means the endpoints you can ask.

**Global Discovery Catalogue**:
A WIS2 global service indexing centres' discovery metadata records. Several
are read; exactly one is the **writer catalogue**, the only one of them that
may create a registry record, and the only source that can say a centre exists
at all.
_Avoid_: GDC outside code and identifiers.

**Dataset**:
A collection a centre publishes, keyed on `(node, identifier)`. The topic is
not part of the key: a centre publishing several datasets on one topic is the
ordinary case.

### Who says what

**Declaration**:
One source's record of what it said about a dataset or a station, kept whole
and as it said it, beside the canonical row rather than as it. `DatasetSource`
and `StationSource`.
_Avoid_: claim, assertion, provenance record.

**Source type**:
Which of the three said it -- `GDC`, a catalogue's copy of what the centre
registered; `NODE`, the centre's own record of what it publishes today;
`OBSERVED`, traffic on the wire.

**Node truth**:
What a centre's own endpoints say about it, which outranks any catalogue's
copy on everything below the address. The catalogue keeps only what a node
structurally cannot supply: that the centre exists, and where to reach it
(ADR-0018).

**Observed dataset**:
A dataset known only because a notification named it. It has no title, because
a notification says nothing about the record beyond its identifier, so the
identifier stands in.

**Drift**:
A dataset one source declares and another does not. It has a direction --
catalogue-only, node-only, or heard but declared by neither -- and the
direction is whose errand it is.
_Avoid_: mismatch, divergence for this specific finding.

**Retired**:
A dataset the centre has stopped declaring: `status` is `inactive`, and its
row, declarations, rollups and history all survive. Only the centre retires
one, and only the centre reinstates it.
_Avoid_: deleted, removed, disabled -- `deleted` is a state nothing in this
tool writes.

### What the tool says

**Finding**:
Something wrong in the region -- a gap, a silence, a drift, an unregistered
centre. Findings are what the reports and the digest are made of.

**Hard failure**:
Something wrong with the tool -- the Global Broker lost, ingestion stalled,
the writer catalogue not syncing -- meaning nothing the tool goes on to say
about the region can be believed until it is fixed. Recorded per spell, not
per check.
_Avoid_: calling a finding an alert, or a hard failure a finding.

**Bound**:
The sentence a report carries saying what it could not measure -- which
centres are not counted, and why. A count read without its bound is read as
the region.
