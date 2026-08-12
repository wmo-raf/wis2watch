# WIS2 Data Flow

A complete journey through the WIS2 network.

## Introduction

The WMO Information System 2.0 (WIS2) is a modern data sharing infrastructure that enables real-time exchange of
meteorological data globally.

## WIS2 Global Services

WIS2 relies on four Global Services that work together to ensure reliable, real-time data exchange:

| Global Service                   | Role                                                                                                                                                                                          |
|----------------------------------|-----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| Global Broker (GB)               | Receives notification messages from all WIS2 Nodes worldwide and redistributes them to all subscribers. Provides a single connection point for receiving data notifications from any country. |
| Global Cache (GC)                | Downloads and hosts copies of core data for reliable access. Provides redundancy and faster downloads from geographically distributed servers. Retains data for approximately 24 hours.       |
| Global Discovery Catalogue (GDC) | Indexes all dataset metadata using the WMO Core Metadata Profile (WCMP2). Enables search and discovery of available datasets and provides subscription information.                           |
| Global Monitor (GM)              | Monitors health and performance of the entire WIS2 network. Provides dashboards, metrics, and alerting mechanisms to ensure system reliability.                                               |

### Global Discovery Catalogues

| Centre Identifier                        | Provider                            | API Link                                                           |
|------------------------------------------|-------------------------------------|--------------------------------------------------------------------|
| `ca-eccc-msc-global-discovery-catalogue` | Meteorological Service of Canada    | https://wis2-gdc.weather.gc.ca/collections/wis2-discovery-metadata |
| `cn-cma-global-discovery-catalogue`      | China Meteorological Administration | https://gdc.wis.cma.cn/collections/wis2-discovery-metadata         |
| `de-dwd-global-discovery-catalogue`      | Deutscher Wetterdienst (Germany)    | https://wis2.dwd.de/gdc/collections/wis2-discovery-metadata        |

### Global Brokers

| Centre Identifier              | Provider                                                                                             | MQTT URI                                                                                                      |
|--------------------------------|------------------------------------------------------------------------------------------------------|---------------------------------------------------------------------------------------------------------------|
| `br-inmet-global-broker`       | Instituto Nacional de Meteorología (Brazil)                                                          | `mqtts://everyone:everyone@globalbroker.inmet.gov.br:8883`                                                    |
| `cn-cma-global-broker`         | China Meteorological Administration                                                                  | `mqtts://everyone:everyone@gb.wis.cma.cn:8883`                                                                |
| `fr-meteofrance-global-broker` | Météo-France (France)                                                                                | `mqtts://everyone:everyone@globalbroker.meteo.fr:8883`<br>`wss://everyone:everyone@globalbroker.meteo.fr:443` |
| `us-noaa-global-broker`        | National Oceanic and Atmospheric Administration, National Weather Service (United States of America) | `mqtts://everyone:everyone@wis2broker.globaldata.nws.noaa.gov:8883`                                           |

### Global Caches

| Centre Identifier                  | Provider                                                                                                                                                                            |
|------------------------------------|-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `cn-cma-global-cache`              | China Meteorological Administration                                                                                                                                                 |
| `data-metoffice-noaa-global-cache` | Met Office (Exeter) (United Kingdom of Great Britain and Northern Ireland) and National Oceanic and Atmospheric Administration, National Weather Service (United States of America) |
| `de-dwd-global-cache`              | Deutscher Wetterdienst (Germany)                                                                                                                                                    |
| `jp-jma-global-cache`              | Japan Meteorological Agency                                                                                                                                                         |
| `kr-kma-global-cache`              | Korea Meteorological Administration                                                                                                                                                 |
| `sa-ncm-global-cache`              | National Center for Meteorology (Saudi Arabia)                                                                                                                                      |

### Global Monitors

| Centre Identifier              | Provider                                        |
|--------------------------------|-------------------------------------------------|
| `cn-cma-global-monitor`        | China Meteorological Administration             |
| `ma-marocmeteo-global-monitor` | Direction Générale de la Météorologie (Morocco) |

## Part 1: Kenya Met publishes data

### Step 1: Data generation

The Kenya Meteorological Department (KMD) collects an observation from one of its weather stations. For example, a SYNOP
report from Nairobi JKIA station at 12:00 UTC containing temperature, humidity, wind speed, and pressure readings.

### Step 2: WIS2 Node processing

KMD operates a WIS2 Node, which consists of two key components:

- MQTT Broker for publishing notification messages
- HTTP Server for hosting the actual data files

The WIS2 Node performs the following actions:

1. Encodes the observation data (typically as BUFR format)
2. Stores the data file on its HTTP server
3. Creates a WIS2 Notification Message (WNM)

**WIS2 Notification Message contents**

The WIS2 Notification Message is a small JSON/GeoJSON document containing:

- Unique identifier (`id`)
- Publication timestamp (`pubtime`)
- Data identifier (`data_id`)
- Canonical link: the HTTP URL where data can be downloaded
- Geographic coordinates of the observation
- Integrity hash of the data file

### Step 3: Notification published to local broker

KMD's WIS2 Node publishes the notification message to its local MQTT broker on a topic following the WIS2 Topic
Hierarchy:

```
origin/a/wis2/ke-kmd/data/core/weather/surface-based-observations/synop
```

Topic structure breakdown:

- `origin/a/wis2/` indicates original publication
- `ke-kmd` is Kenya's centre identifier
- `data/core/` indicates this is core data (freely available per WMO Resolution 1)
- `weather/surface-based-observations/synop` specifies the data category

## Part 2: Global Broker receives and redistributes

### Step 4: Global Broker subscription

Multiple Global Brokers, operated by different organizations (Météo-France, NOAA, China Meteorological Administration,
and others), are permanently subscribed to all WIS2 Nodes worldwide. Each Global Broker subscribes to:

```
origin/a/wis2/#
```

### Step 5: Message redistribution

When the Global Broker receives KMD's notification, it:

1. Validates the message format
2. Republishes the notification to all its subscribers
3. Makes the message available globally with high reliability

The key benefit: instead of ACMAD needing to know and connect to every African NMS's individual broker, they connect to
one Global Broker and receive notifications from everyone.

## Part 3: Global Cache downloads and hosts data

### Step 6: Global Cache subscription

Global Caches (operated by organizations like Met Office UK and NOAA) subscribe to the Global Broker for all core data:

```
origin/a/wis2/+/data/core/#
```

### Step 7: Data download and caching

When a Global Cache receives KMD's notification, it:

1. Extracts the canonical link from the notification
2. Downloads the actual data file from KMD's HTTP server
3. Stores a copy on its own high-availability storage
4. Creates a new notification message with an updated canonical link pointing to the cached copy

### Step 8: Cache notification published

The Global Cache publishes its notification on a new topic:

```
cache/a/wis2/ke-kmd/data/core/weather/surface-based-observations/synop
```

Note the topic prefix changed from `origin/` to `cache/`, indicating the data is now available from a Global Cache.

**Benefits of the Global Cache**

- Provides redundancy (if KMD's server is down, data is still available)
- Reduces load on NMS infrastructure
- Offers faster downloads from geographically distributed caches
- Ensures 24-hour availability of recent data

## Part 4: Global Discovery Catalogue indexes metadata

### Step 9: Dataset registration (one-time)

Before publishing data, KMD registers their SYNOP dataset by publishing discovery metadata (WCMP2 format) describing:

- What the dataset contains
- Geographic coverage
- Temporal resolution
- How to subscribe (MQTT topic)
- Contact information

### Step 10: GDC indexing

The Global Discovery Catalogue ingests this metadata and then:

- Makes the dataset searchable via API
- Provides the MQTT subscription details
- Links to data access information

This allows ACMAD to discover what datasets exist across Africa before subscribing.

## Part 5: Global Monitor tracks everything

### Step 11: Continuous monitoring

The Global Monitor continuously:

- Tracks message flow through the system
- Monitors Global Broker and Cache health
- Detects if an NMS stops publishing
- Provides dashboards and alerts

## End-to-end summary

```
KMD Station observation
        |
        v
KMD WIS2 Node  ->  data file on HTTP server
        |
        |  publish WNM on origin/a/wis2/ke-kmd/data/core/...
        v
Global Broker  ->  redistributes to all subscribers
        |                                   \
        |                                    \
        v                                     v
Global Cache                              Subscribers (e.g. ACMAD)
  downloads data file                       download from origin or cache
  republishes on cache/a/wis2/...

Global Discovery Catalogue  ->  indexes WCMP2 metadata for discovery
Global Monitor              ->  observes the whole chain
```
