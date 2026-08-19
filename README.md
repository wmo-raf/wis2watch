# WIS2Watch

`Note`: Under development

WIS2Watch watches the WIS2 nodes of a region -- Africa by default -- from the
WIS2 Global Services: what each centre says it publishes, what it actually
publishes, and whether that traffic reaches the world.

## First start

Everything the tool needs to start watching ships with it. There is no broker
URL to find and no catalogue to choose.

```bash
git clone https://github.com/wmo-raf/wis2watch.git
cd wis2watch
cp .env.sample .env      # set SECRET_KEY and the database credentials at least
docker compose up -d
```

The web container migrates the database and then seeds the WIS2 Global
Services. Its startup output is the record of what it did:

- **Three Global Discovery Catalogues**, all active. ECCC is the writer -- the
  one catalogue allowed to create and update registry records. DWD and CMA are
  read-only, which is what makes "indexed in one catalogue but missing from
  another" something this tool can report.
- **Four Global Brokers**, exactly one of them active. Meteo-France is dialled;
  INMET, CMA and NOAA are seeded switched off, so changing vantage point is two
  checkboxes rather than a lookup.
- **The first catalogue sync and the first OSCAR station sync, enqueued.** Both
  are on slow schedules -- six hours and a week -- and a newly registered
  periodic task does not run until one whole interval has passed, so a fresh
  install would otherwise read healthy and empty until tomorrow. They are handed
  to the queue rather than run inline: a Global Discovery Catalogue that never
  answers must not be able to delay the web container's startup.

The catalogue sync is what populates everything downstream -- nodes, datasets,
origin brokers -- and the ingest supervisor re-reads the registry every minute,
so it starts subscribing to the new centres without a restart. Give the sync a
few minutes and the node overview has the region in it.

Then create yourself a login. This is the one step that is not automated, and
deliberately so:

```bash
docker compose exec wis2watch python manage.py createsuperuser
```

The admin is at the site root -- `http://localhost/` behind the bundled proxy --
and the node overview is the dashboard it opens on.

## Changing what is watched

The seed creates a Global Service only where its centre ID is absent, and never
modifies a row that exists. So anything edited in the admin stays edited, and a
Global Service added in a later release appears on the next start.

The corollary is that **deletion does not stick**: a deleted row is one the seed
has never seen, so it comes back on the next start. To stop using a catalogue or
a broker, clear its **is active** checkbox instead.

Two things the seed will not take back once somebody else holds them: the
writing catalogue and the one active Global Broker. Promote another catalogue to
writer, or switch the Global Broker, and the seed leaves that decision alone --
even where the row it would otherwise have created is the one that used to hold
the post.

The region itself is configuration rather than admin: `WIS2WATCH_MONITORED_COUNTRIES`
in `.env`, empty for all of Africa.

## Documentation

- [WIS2 data flow](docs/wis2-data-flow.md) -- the Global Services and the
  journey a notification takes through them.
