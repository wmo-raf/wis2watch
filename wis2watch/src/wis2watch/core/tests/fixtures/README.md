# Captured fixtures

Real payloads, captured from the sources WIS2Watch reads, committed so the
interpretation tests never touch the network. They double as documentation of
what these sources actually return, which is repeatedly not what their schemas
suggest.

The first three were captured on **2026-08-11**, the node station registry on
**2026-08-12**, and the two node message archives on **2026-08-13**.

## `global_broker_notifications.jsonl`

WIS2 Notification Messages taken off the Météo-France Global Broker
(`mqtts://everyone:everyone@globalbroker.meteo.fr:8883`), subscribing to
`origin/a/wis2/#` and `cache/a/wis2/#`. One JSON object per line, each
`{"topic": ..., "payload": ...}` exactly as received.

What the capture deliberately covers:

- African surface observations (`ke-meteo`, `ng-nimet`, …) that carry
  `properties.wigos_station_identifier`, and gridded products that carry no
  station at all — the unattributed case is the norm, not an edge case.
- The same publication seen on `origin/` and again on `cache/`, republished by
  different Global Caches. Note that a Global Cache republishes under **its own
  notification UUID**, so cache traffic cannot be matched to origin traffic by
  UUID.
- `br-inmet`, whose messages advertise their data under `rel: update` and carry
  **no canonical link at all**, alongside a `via` link into OSCAR.
- Data identifiers that spell out the WIGOS station identifier. Reading it back
  out would be inference; only the explicit property attributes a message.

## `gdc_discovery_metadata.json`

WCMP2 discovery metadata from the Canadian Global Discovery Catalogue
(`https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata/items`).
The envelope is as returned; the feature list is a nine-record subset of the
551 available, and `numberMatched`/`numberReturned` are set to that subset.

The records were picked for what they expose:

| Record | What it documents |
| --- | --- |
| `ke-meteo:synop-dataset-surface-observations` | No `wmo:topicHierarchy` — the topic lives in a link `name`. Only Global Broker links, so no origin broker. |
| `cg-met:core.climate.surface-based-observations.climat` | No `centre-id` property — the centre comes from the identifier URN. Advertises its own broker over plain MQTT on 1883. |
| `sz-swazimet:surface-based-observations.synop` | The well-formed case: topic hierarchy and centre ID both declared. |
| `gh-gmet:urn:wmo:md:gh-gmet:core.surface-based-observations.synop` | An identifier with the URN prefix repeated inside itself. |
| `il-ims:weather.observations.temp` | The topic exists only as a `channel`, naming the cached mirror rather than the origin topic. |
| `int-eumetsat:met09:amv` | A non-country centre prefix, and `mqtts://example.org` as a broker. |
| `us-cimss:dbnet.cris-fullch` | A broker advertised with no port; no `updated` timestamp. |
| `it-meteoam:observations.surface.synop-bufr` | No topic anywhere — must be skipped. |
| `fr-ifremer-argo:cor:msg:argo` | No topic anywhere — must be skipped. |

Of the 551 records available at capture time, 16 carried no topic and only 49
declared `wmo:topicHierarchy`, so both of these are the common case rather than
a curiosity.

Catalogues append one notification link per Global Broker, each titled as that
broker's service (`"… Global Broker Service (fr-meteofrance-global-broker)"`).
The node's own broker is the link that is *not* titled that way — that is the
rule the extraction uses, and the fixture carries both kinds.

## `oscar_stations_ke.json`

OSCAR/Surface station search for Kenya
(`https://oscar.wmo.int/surface/rest/api/search/station?territoryName=KEN`).
The envelope is as returned; the results are a 13-station subset of the 187
available, with `totalCount` set to that subset.

Covered: every operational status OSCAR reports for the territory
(`operational`, `partlyOperational`, `closed`, `unknown`), stations carrying
more than one WIGOS identifier, a station with no elevation, and one with an
elevation of zero.

Elsewhere in the region OSCAR also reports `silent`, which Kenya has none of;
the sync tests assert that one against a hand-written record. The station types
here — `Land (fixed)`, `Lake/River (fixed)`, `Underwater (mobile)` — are the
ones African territories actually carry, and only the first names a WIGOS
facility type.

OSCAR answers a territory whole: `itemsPerPage` comes back as 50000 and
`pageCount` as 1 even for territories running to thousands of stations, and its
station search takes no page parameter.

## `node_stations_gh_gmet.json`

Ghana's own station registry
(`https://wis2.meteo.gov.gh/oapi/collections/stations/items`), which is what a
wis2box node publishes about the stations it operates. The envelope is as
returned; the feature list is a nine-station subset of the 39 available, with
`numberMatched`/`numberReturned` set to that subset.

Covered: stations the operator gives a traditional identifier and stations it
does not (32 of the 39 leave it empty), a station on the Greenwich meridian
whose longitude is `0.0`, stations declaring no `topic` property, and the
`barometer_height` the station CSV export reports.

Every station in the registry carries a WIGOS station identifier and a
three-dimensional position, so the skipped and unpositioned cases are asserted
against hand-written features rather than captured ones.

A registry longer than the page size links to its next page under `rel: next`,
the same as a Global Discovery Catalogue does; this capture fits on one page
and so carries no such link. The default page size of a station endpoint is
routinely ten, which is why the fetch asks for more.

## `node_messages_sc_seychelles_met.json` and `node_messages_gh_gmet.json`

Two centres' own archives of the notifications they published, which a wis2box
serves at `/oapi/collections/messages/items`. Both are unedited pages, exactly
as the node returned them — envelope, links and all.

**What makes these different from the captured broker traffic: there is no
topic.** Not omitted from the property the parser reads, but absent from the
payload entirely — nothing in a feature, at any level, says what topic the
message went out on. Everything the ingest reads off a topic therefore has to
come from somewhere else here: the centre from the address that was polled, the
vantage point because the archive is one, and the dataset from the
`metadata_id` the message carries. What is stored carries an empty topic,
because none was observed. Synthesising the dataset's declared topic would read
better and would destroy the evidence for a centre transmitting data no dataset
of its own claims — a message no topic would ever have named.

**Both carry a metadata notification mixed in with the data ones**, which is
what a centre publishing its own discovery metadata looks like from the
archive. It carries the WCMP2 record inline as base64, names **no**
`metadata_id` (the record it announces is the one that would be named), carries
no `wigos_station_identifier`, and advertises only a `rel: update` link — no
canonical link at all. Nothing in the payload says it is not a publication;
what says so is its `data_id`, which spells the topic it went out on —
`{centre}/metadata/{record}`. That is what the ingest recognises it by here,
there being no topic, and it is set aside rather than stored, as the MQTT path
sets aside one on `origin/a/wis2/{centre}/metadata`.

| Capture | Retention at capture | The window asked for | Match count |
| --- | --- | --- | --- |
| `node_messages_sc_seychelles_met.json` | 451 messages, about 36 hours | `2026-08-12T14:30:00Z/2026-08-12T16:00:00Z` | 14 matched, 14 returned |
| `node_messages_gh_gmet.json` | 57,170 messages, back to 2026-05-06 | `2026-08-09T00:00:00Z/2026-08-11T23:59:59Z` | 1,757 matched, 10 returned |

Retention depth is a per-node property and it drifts, which is why both the
match count and the capture date are recorded here: Seychelles
(`https://wis2.meteo.sc`) holds barely a day and its whole archive fits on one
page at the page size the poll asks for, while Ghana
(`https://wis2.meteo.gov.gh`) holds three months and pages genuinely.

What each covers:

- **The shallow one** is a single page: no `rel: next` link, and
  `numberMatched` equal to `numberReturned`. Thirteen data notifications, one
  per station, and the metadata notification.
- **The deep one** is a page from the *middle* of a paging run —
  `offset=386&limit=10` of 1,757 — captured that way so that one page could
  carry both a metadata notification and a real `next` link. Its `next` link
  carries the `datetime` interval forward, which is why paging follows the
  server's own link rather than an offset we compute: a resumed page that
  dropped the interval would read on through the whole archive believing it was
  still inside the window.
- **Neither page is in publication order.** The deep capture's ten
  notifications run 17:09, 15:00, 17:09, … as returned. Anything reading the
  first or last row of a page as the edge of the window would be wrong on this
  very capture.

The `datetime` parameter matches on **`pubtime`**, not on the observation time
in `properties.datetime`: at capture, a window of `10:00Z/11:00Z` returned
notifications published between 10:09 and 10:24 whose observation times were
all 09:00. That is what makes the reply comparable with what the Global Broker
carried — the same claim, by the same publisher, about the same moment.

## Refreshing a fixture

Re-capture from the same source and keep the shapes listed above; the tests
assert on specific records by identifier, so replacing a record means updating
the test that names it.
