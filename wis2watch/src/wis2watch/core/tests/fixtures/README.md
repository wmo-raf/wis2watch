# Captured fixtures

Real payloads, captured from the sources WIS2Watch reads, committed so the
interpretation tests never touch the network. They double as documentation of
what these sources actually return, which is repeatedly not what their schemas
suggest.

The first three were captured on **2026-08-11**, the node station registry on
**2026-08-12**.

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

## Refreshing a fixture

Re-capture from the same source and keep the shapes listed above; the tests
assert on specific records by identifier, so replacing a record means updating
the test that names it.
