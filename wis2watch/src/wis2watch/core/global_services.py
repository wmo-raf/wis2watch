"""The WIS2 Global Services, and how a fresh install comes to know them.

Nothing in this tool works until one table has rows in it: with no Global
Discovery Catalogue there are no nodes, no datasets, no origin brokers, no
subscriptions and no reports. The Global Services are a known, finite,
slow-moving set, so they ship with the app rather than being typed in by
whoever installs it -- this module is that list, in the way
:mod:`wis2watch.core.countries` is the list of monitored countries.

**Seeded rows are operator-owned with a narrow re-assert.** A service is
created only where its centre ID is absent; a row that exists is never
modified. Absent means never seen, present means the operator has an opinion.
That one rule gives both properties worth having, without a flag to maintain: a
Global Service added in a later release appears on the next start because its
centre ID is not there yet, and a catalogue somebody deactivated stays
deactivated because it is. The remedy for an unwanted service is therefore
``is_active=False`` and not deletion -- a deleted row is one this module has
never seen, so it comes back.

What must not come back with it is authority the operator has since moved
elsewhere. Deleting the writing catalogue and promoting another one would,
under a plain re-create, restore the deleted catalogue as writer and silently
stand the promoted one down; the same deletion of the one active Global Broker
would leave two active, and the supervisor connects to every active one, so
every notification would be stored twice. Both flags are therefore conferred
only on a table where nobody holds them: the seed fills a vacancy, it never
takes a post from its current holder.
"""

from dataclasses import dataclass, field

from django.db import transaction

from .models import GlobalDiscoveryCatalogue, MessageSource, SyncLog

#: What every Global Broker publishes as its credentials. Public and
#: documented, which is why they are written here rather than configured.
PUBLIC_BROKER_CREDENTIALS = ("everyone", "everyone")


@dataclass(frozen=True)
class SeededCatalogue:
    """A Global Discovery Catalogue as it ships.

    ``base_url`` stops short of the collection path, which
    :func:`wis2watch.core.catalogue.discovery_metadata_url` appends.
    """

    centre_id: str
    name: str
    base_url: str
    is_writer: bool = False


@dataclass(frozen=True)
class SeededBroker:
    """A Global Broker as it ships."""

    centre_id: str
    name: str
    host: str
    port: int = 8883
    use_tls: bool = True
    is_active: bool = False


#: The three Global Discovery Catalogues, all seeded active.
#:
#: ECCC writes because it is the deployment WMO's own wis2box training treats
#: as canonical. The other two are read-only, which is what makes "indexed in
#: one catalogue but missing from another" a difference this tool can report
#: rather than a flap between two authorities disagreeing about a centre.
GLOBAL_DISCOVERY_CATALOGUES = (
    SeededCatalogue(
        centre_id="ca-eccc-msc-global-discovery-catalogue",
        name="Meteorological Service of Canada",
        base_url="https://wis2-gdc.weather.gc.ca",
        is_writer=True,
    ),
    SeededCatalogue(
        centre_id="de-dwd-global-discovery-catalogue",
        name="Deutscher Wetterdienst",
        base_url="https://wis2.dwd.de/gdc",
    ),
    SeededCatalogue(
        centre_id="cn-cma-global-discovery-catalogue",
        name="China Meteorological Administration",
        base_url="https://gdc.wis.cma.cn",
    ),
)

#: The four Global Brokers, exactly one of them active.
#:
#: Every Global Broker carries the whole world's traffic, so a second active
#: one would not widen coverage: it would store every notification twice, since
#: a stored message is unique on ``(notification_id, source)``. The other three
#: cost nothing switched off, and turn "watch the region from somewhere else"
#: into two checkboxes in the admin.
GLOBAL_BROKERS = (
    SeededBroker(
        centre_id="fr-meteofrance-global-broker",
        name="Meteo-France Global Broker",
        host="globalbroker.meteo.fr",
        is_active=True,
    ),
    SeededBroker(
        centre_id="br-inmet-global-broker",
        name="INMET Global Broker",
        host="globalbroker.inmet.gov.br",
    ),
    SeededBroker(
        centre_id="cn-cma-global-broker",
        name="CMA Global Broker",
        host="gb.wis.cma.cn",
    ),
    SeededBroker(
        centre_id="us-noaa-global-broker",
        name="NOAA Global Broker",
        host="wis2broker.globaldata.nws.noaa.gov",
    ),
)

#: The syncs a fresh install cannot afford to wait for. Beat gives a newly
#: registered periodic task its first run one whole interval away, which is six
#: hours for the catalogues and seven days for OSCAR -- long enough for a new
#: deployment to read as healthy and empty at the same time.
FIRST_SYNCS = (SyncLog.CATALOGUE, SyncLog.OSCAR_STATIONS)

#: What counts as a sync having landed. A partial run wrote what it could
#: reach, which is the thing the kick exists to bring about; only a run that
#: wrote nothing leaves the hole open.
LANDED_STATUSES = (SyncLog.SUCCESS, SyncLog.PARTIAL)


@dataclass
class SeedReport:
    """What one seeding run created, for the operator watching the start."""

    catalogues_created: list["GlobalDiscoveryCatalogue"] = field(default_factory=list)
    brokers_created: list["MessageSource"] = field(default_factory=list)

    @property
    def created_anything(self):
        return bool(self.catalogues_created or self.brokers_created)


def _seed_catalogues():
    """Every declared catalogue whose centre ID is not in the table yet."""
    created = []

    for declared in GLOBAL_DISCOVERY_CATALOGUES:
        if GlobalDiscoveryCatalogue.objects.filter(
            centre_id=declared.centre_id
        ).exists():
            continue

        created.append(
            GlobalDiscoveryCatalogue.objects.create(
                centre_id=declared.centre_id,
                name=declared.name,
                base_url=declared.base_url,
                is_writer=(
                    declared.is_writer
                    and not GlobalDiscoveryCatalogue.objects.filter(
                        is_writer=True
                    ).exists()
                ),
            )
        )

    return created


def _seed_brokers():
    """Every declared Global Broker whose centre ID is not in the table yet."""
    username, password = PUBLIC_BROKER_CREDENTIALS
    created = []

    for declared in GLOBAL_BROKERS:
        if MessageSource.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER,
            centre_id=declared.centre_id,
        ).exists():
            continue

        created.append(
            MessageSource.objects.create(
                name=declared.name,
                source_type=MessageSource.GLOBAL_BROKER,
                centre_id=declared.centre_id,
                host=declared.host,
                port=declared.port,
                use_tls=declared.use_tls,
                username=username,
                password=password,
                is_active=(
                    declared.is_active
                    and not MessageSource.objects.filter(
                        source_type=MessageSource.GLOBAL_BROKER,
                        is_active=True,
                    ).exists()
                ),
            )
        )

    return created


@transaction.atomic
def seed_global_services():
    """The Global Services, as far as they are missing from the database."""
    return SeedReport(
        catalogues_created=_seed_catalogues(),
        brokers_created=_seed_brokers(),
    )


def adopt_unnamed_global_brokers(message_source_model):
    """Give this release's identity to a Global Broker seeded before it existed.

    Until this release the Global Broker was created from a URL in the
    environment and carried no centre ID at all. The seed keys on the centre
    ID, so on upgrade it would see Meteo-France missing and create a second row
    for the broker already being dialled -- two rows, one address, and the
    older one holding whatever the operator had done to it.

    Matching on the host is what closes that: it is the one thing the old row
    and the declared service certainly agree on. A broker whose host matches
    nothing declared is somebody's own and keeps its silence, and a centre ID
    already spoken for is left to the row that holds it.

    Written for a migration, so the model is an argument: history must be
    applied against the shape the database had at the time.
    """
    declared_by_host = {broker.host: broker.centre_id for broker in GLOBAL_BROKERS}
    unnamed = message_source_model.objects.filter(
        source_type=MessageSource.GLOBAL_BROKER, centre_id=""
    ).order_by("pk")
    adopted = []

    for source in unnamed:
        centre_id = declared_by_host.get(source.host.strip().lower())

        if not centre_id:
            continue

        if message_source_model.objects.filter(
            source_type=MessageSource.GLOBAL_BROKER, centre_id=centre_id
        ).exists():
            continue

        source.centre_id = centre_id
        source.save(update_fields=["centre_id"])
        adopted.append(source)

    return adopted


def pending_first_syncs():
    """The syncs that have never yet landed, in the order they matter.

    Keyed on what the database has to show for itself rather than on what this
    run created, because the seed creates something exactly once, ever: an
    enqueued task that is lost -- no worker up, Redis restarted before the
    queue drained -- would otherwise leave the operator waiting six hours and
    seven days with no second chance. Asked this way the kick repeats each
    start until a sync has genuinely landed, and then stops for good.
    """
    landed = set(
        SyncLog.objects.filter(
            sync_type__in=FIRST_SYNCS,
            status__in=LANDED_STATUSES,
        ).values_list("sync_type", flat=True)
    )

    return tuple(sync_type for sync_type in FIRST_SYNCS if sync_type not in landed)
