from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.fields import ArrayField
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField
from django_countries.widgets import CountrySelectWidget
from django_extensions.db.models import TimeStampedModel
from timescale.db.models.models import TimescaleModel
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.snippets.models import register_snippet

from .countries import monitored_country_code_for_centre_id
from .interpretation import OPERATIONAL


class GlobalDiscoveryCatalogue(TimeStampedModel):
    """
    A WIS2 Global Discovery Catalogue: the source of the node and dataset registry.

    Exactly one catalogue is designated the writer of registry records. The rest
    are fetched read-only so that divergence between catalogues is itself
    reportable, rather than letting records flap between disagreeing catalogues.
    """

    centre_id = models.CharField(
        max_length=200,
        unique=True,
        help_text=_("WIS2 centre ID of the catalogue"),
    )
    name = models.CharField(max_length=200)
    base_url = models.URLField(max_length=500)
    verify_ssl = models.BooleanField(default=True)
    is_writer = models.BooleanField(
        default=False,
        help_text=_("Only the writing catalogue may create or update registry records"),
    )
    is_active = models.BooleanField(default=True)
    last_sync = models.DateTimeField(null=True, blank=True)

    panels = [
        FieldPanel("name"),
        FieldPanel("centre_id"),
        FieldPanel("base_url"),
        FieldPanel("verify_ssl"),
        FieldPanel("is_writer"),
        FieldPanel("is_active"),
    ]

    class Meta:
        ordering = ["name"]
        verbose_name = _("Global Discovery Catalogue")
        verbose_name_plural = _("Global Discovery Catalogues")

    def __str__(self):
        return f"{self.name} ({self.centre_id})"

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)

        # "Sole writer" is the whole point of the flag, so designating one
        # catalogue stands the others down rather than leaving two authorities
        # to overwrite each other's view of a centre.
        if self.is_writer:
            GlobalDiscoveryCatalogue.objects.exclude(pk=self.pk).filter(
                is_writer=True
            ).update(is_writer=False)


class WIS2Node(TimeStampedModel):
    """
    A WIS2 node: one publishing centre, identified by its centre ID alone.

    The centre ID is globally unique in WIS2, so nothing else takes part in the
    node's identity. Country is derived from the centre ID prefix when that
    prefix names a monitored country, but is stored and editable so that bad
    data and non-country prefixes can be corrected without a code change.
    """

    NODE_TYPE_CHOICES = [
        ("wis2box", "WIS2Box"),
        ("other", "Other Software"),
    ]

    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("error", "Error"),
    ]

    centre_id = models.CharField(
        max_length=200,
        unique=True,
        help_text=_("WIS2 centre ID, e.g. ke-kmd"),
    )
    name = models.CharField(max_length=200, help_text=_("Friendly name for this node"))
    country = CountryField(
        blank=True,
        blank_label=_("Select Country"),
        verbose_name=_("Country"),
        help_text=_("Derived from the centre ID prefix when left blank"),
    )
    node_type = models.CharField(max_length=20, choices=NODE_TYPE_CHOICES, default="wis2box")

    base_url = models.URLField(max_length=500, blank=True, help_text=_("Base URL of the node"))

    discovery_metadata_url = models.URLField(
        max_length=500,
        blank=True,
        help_text=_("Custom URL for discovery metadata. Auto-generated for wis2box."),
    )

    stations_url = models.URLField(
        max_length=500,
        blank=True,
        help_text=_("Custom URL for stations list. Auto-generated for wis2box."),
    )

    verify_ssl = models.BooleanField(
        default=True,
        help_text=_("Verify SSL certificates when connecting to the node"),
    )

    is_manually_managed = models.BooleanField(
        default=False,
        help_text=_("Catalogue syncs never overwrite fields on a manually managed node"),
    )

    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")

    last_check = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    panels = [
        FieldPanel("centre_id"),
        FieldPanel("name"),
        FieldPanel("country", widget=CountrySelectWidget()),
        FieldPanel("node_type"),
        FieldPanel("base_url"),
        MultiFieldPanel(
            [
                FieldPanel("discovery_metadata_url"),
                FieldPanel("stations_url"),
            ],
            heading=_("API Endpoints"),
        ),
        FieldPanel("verify_ssl"),
        FieldPanel("is_manually_managed"),
    ]

    class Meta:
        ordering = ["country", "name"]
        verbose_name = _("WIS2 Node")
        verbose_name_plural = _("WIS2 Nodes")

    def __str__(self):
        return f"{self.name} ({self.centre_id})"

    def save(self, *args, **kwargs):
        # Centre IDs are lowercase by convention but are typed by hand as often
        # as they are synced. Normalising here is what makes uniqueness on the
        # centre ID actually hold, rather than admitting ke-meteo and KE-METEO
        # as two nodes for one centre.
        self.centre_id = self.centre_id.strip().lower()

        if not self.country:
            self.country = monitored_country_code_for_centre_id(self.centre_id)

        if self.node_type == "wis2box" and self.base_url:
            if not self.discovery_metadata_url:
                self.discovery_metadata_url = (
                    f"{self.base_url}/oapi/collections/discovery-metadata/items?f=json"
                )
            if not self.stations_url:
                self.stations_url = f"{self.base_url}/oapi/collections/stations/items?f=json"

        super().save(*args, **kwargs)

    @property
    def country_center_point(self):
        """The geographic centre of the node's country, or None if it has no country."""
        if not self.country:
            return None

        geo_extent = self.country.geo_extent
        if not geo_extent:
            return None

        centroid = Polygon.from_bbox(geo_extent).centroid
        return [centroid.x, centroid.y]

    @property
    def origin_source(self):
        """The node's own broker, as a message source, or None if it has none.

        Iterates in Python so that a prefetched ``message_sources`` is used
        rather than re-queried per node.
        """
        for source in self.message_sources.all():
            if source.source_type == MessageSource.ORIGIN_BROKER:
                return source

        return None

    def get_topics(self):
        return list(self.datasets.values_list("wmo_topic_hierarchy", flat=True))


class MessageSourceQuerySet(models.QuerySet):
    def connections(self):
        """The sources something actually dials.

        A vantage point carried by another source -- Global Cache pickup, read
        off a Global Broker connection's ``cache/`` topics -- is never
        connected to. It has no address of its own to correct, no reachability
        to report and nothing that deactivating it would stop, so anywhere the
        admin or the monitor is describing connections it is not one of them.
        """
        return self.filter(carried_by__isnull=True)

    def origin_brokers(self):
        """The nodes' own brokers this tool is currently meant to be watching.

        A broker switched off in the admin is not one of them: reachability is
        only ever what the last live connection recorded, so a source nothing
        is dialling any more carries an answer that has since gone stale.
        """
        return self.filter(
            source_type=MessageSource.ORIGIN_BROKER,
            is_active=True,
            node__isnull=False,
        )

    def watched_origins(self):
        """The origin brokers whose view of their centre can be trusted now.

        What decides whether a centre may be judged on the difference between
        its own broker and the Global Broker -- and so is asked both by the
        evaluation that records propagation gaps and by the report that lists
        them. Written once, because a gap recorded while a broker answered and
        then reported after it went dark is exactly the finding neither of them
        should stand behind.

        A null reachability is "not attempted yet", and is no more a licence to
        judge a centre than a failure is.
        """
        return self.origin_brokers().filter(is_reachable=True)


class MessageSource(TimeStampedModel):
    """
    A vantage point from which WIS2 notification messages are observed.

    A Global Broker, a node's own origin broker and the Global Caches' pickup
    of a centre's data are all message sources. Modelling them as one entity is
    what makes "published at origin but never seen on the Global Broker" -- and
    "carried by the Global Broker but never cached" -- expressible: the same
    notification observed from three vantage points is three rows that can be
    matched, not one row that overwrites the others.

    A vantage point is not always a connection of its own. Global Cache pickup
    is read off the ``cache/`` topics of a Global Broker connection, so its
    source records which connection carries it rather than an address anything
    dials; ``carried_by`` is what says so.
    """

    GLOBAL_BROKER = "global_broker"
    GLOBAL_CACHE = "global_cache"
    ORIGIN_BROKER = "origin_broker"

    SOURCE_TYPE_CHOICES = [
        (GLOBAL_BROKER, _("Global Broker")),
        (GLOBAL_CACHE, _("Global Cache")),
        (ORIGIN_BROKER, _("Origin Broker")),
    ]

    name = models.CharField(max_length=200)
    source_type = models.CharField(
        max_length=20,
        choices=SOURCE_TYPE_CHOICES,
        default=GLOBAL_BROKER,
    )
    centre_id = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("WIS2 centre ID of the broker, where it has one"),
    )
    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="message_sources",
        help_text=_("The node this broker belongs to, for origin brokers"),
    )
    carried_by = models.ForeignKey(
        "self",
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="carried_sources",
        help_text=_(
            "The broker connection this vantage point's traffic arrives on, "
            "for vantage points that are not dialled separately"
        ),
    )

    host = models.CharField(max_length=255)
    port = models.IntegerField(default=1883)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=255, blank=True)
    use_tls = models.BooleanField(default=False)

    is_active = models.BooleanField(default=True)

    # Reachability is diagnostic state, not an error condition: a broker that
    # cannot be reached from outside is a finding this tool exists to report.
    # Null until a connection has been attempted, because "we have not looked
    # yet" and "it does not answer" are different findings, and a broker a
    # catalogue sync has just advertised is in the first state, not the second.
    is_reachable = models.BooleanField(
        null=True,
        default=None,
        help_text=_("Null until a connection to this broker has been attempted"),
    )
    last_connected_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)

    objects = MessageSourceQuerySet.as_manager()

    panels = [
        FieldPanel("name"),
        FieldPanel("source_type"),
        FieldPanel("centre_id"),
        FieldPanel("node"),
        # Read-only because it is not a choice anyone makes: a carried vantage
        # point is created by the ingest against the connection that carries
        # it, and repointing it in the admin would say the traffic arrived
        # somewhere it did not.
        FieldPanel("carried_by", read_only=True),
        MultiFieldPanel(
            [
                FieldPanel("host"),
                FieldPanel("port"),
                FieldPanel("username"),
                FieldPanel("password"),
                FieldPanel("use_tls"),
            ],
            heading=_("Broker Connection"),
        ),
        FieldPanel("is_active"),
    ]

    class Meta:
        ordering = ["source_type", "name"]
        constraints = [
            # A node has one broker of any given kind. The source type takes
            # part so that a node can later gain a second vantage point (a
            # cache feed, say) without a schema change.
            models.UniqueConstraint(
                fields=["node", "source_type"],
                condition=models.Q(node__isnull=False),
                name="unique_source_type_per_node",
            ),
            # A connection carries one vantage point of any given kind. What
            # the Global Broker's ``cache/`` topics deliver is one thing seen
            # one way, and a second row for it would split a centre's cache
            # pickup across two sources that each looked complete.
            models.UniqueConstraint(
                fields=["carried_by", "source_type"],
                condition=models.Q(carried_by__isnull=False),
                name="unique_source_type_per_carrier",
            ),
        ]
        verbose_name = _("Message Source")
        verbose_name_plural = _("Message Sources")

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"

    @property
    def owning_centre_id(self):
        """The centre this source belongs to, or an empty string.

        A Global Broker names its own centre; an origin broker's centre is the
        node it belongs to. Resolving that here keeps callers from walking the
        node relation and guessing which of the two applies.
        """
        if self.centre_id:
            return self.centre_id

        return self.node.centre_id if self.node_id else ""


class Dataset(TimeStampedModel):
    """
    A dataset a node claims to publish, as described by a WCMP2 discovery
    metadata record.

    Registered for the admin by ``DatasetViewSet`` rather than here, because
    what a person may do to a sync-managed record is part of what the record
    is: its expectation is theirs to set, and everything else is the
    catalogue's.
    """

    CORE = "core"
    RECOMMENDED = "recommended"

    DATA_POLICY_CHOICES = [
        (CORE, "Core"),
        (RECOMMENDED, "Recommended"),
    ]

    ACTIVE = "active"
    INACTIVE = "inactive"
    DELETED = "deleted"

    STATUS_CHOICES = [
        (ACTIVE, "Active"),
        (INACTIVE, "Inactive"),
        (DELETED, "Deleted"),
    ]

    node = models.ForeignKey(WIS2Node, on_delete=models.CASCADE, related_name="datasets")
    identifier = models.CharField(
        max_length=500,
        unique=True,
        help_text=_("URN identifier of the dataset"),
    )
    expected_interval_override_hours = models.PositiveIntegerField(
        null=True,
        blank=True,
        verbose_name=_("Expected interval override (hours)"),
        help_text=_(
            "How long this dataset may be quiet before it is silent. Takes "
            "precedence over the interval learned from its own history; leave "
            "empty to use the learned one."
        ),
    )
    title = models.CharField(max_length=500)
    wmo_data_policy = models.CharField(max_length=20, choices=DATA_POLICY_CHOICES)
    wmo_topic_hierarchy = models.CharField(
        unique=True,
        max_length=500,
        help_text=_("MQTT topic hierarchy for this dataset"),
    )
    self_link = models.URLField(max_length=1000, blank=True)
    collection_link = models.URLField(max_length=1000, blank=True)
    raw_json = models.JSONField(help_text=_("Complete raw JSON from discovery metadata"))
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default=ACTIVE)
    metadata_created = models.DateTimeField(null=True, blank=True)
    metadata_updated = models.DateTimeField(null=True, blank=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    # A dataset is sync-managed, so the edit form offers the one field a person
    # is meant to set -- what this tool should expect of it -- and shows the
    # rest for identification only. Everything else is the catalogue's to say,
    # and a hand-edit would be overwritten by the next sync anyway.
    panels = [
        FieldPanel("title", read_only=True),
        FieldPanel("identifier", read_only=True),
        FieldPanel("wmo_topic_hierarchy", read_only=True),
        FieldPanel("status", read_only=True),
        FieldPanel("expected_interval_override_hours"),
    ]

    class Meta:
        ordering = ["node", "title"]
        indexes = [
            models.Index(fields=["wmo_topic_hierarchy"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.identifier})"


class StationQuerySet(models.QuerySet):
    def resolve(self, wigos_id, *also_known_as):
        """The one station these identifiers name, created if none knows it.

        A station routinely carries more than one WIGOS identifier -- OSCAR
        files some under a long synthetic primary while their centre transmits
        the traditional form, and others the other way about -- so resolving on
        one identifier alone would give a single physical station two records.
        Every identifier a source declares is therefore looked up, and the ones
        the record did not already carry are remembered, so that a source which
        knows the station by only one of them still finds it later.

        The identifier a station is already keyed on is never moved: everything
        else refers to the record, and the source that created it is not
        necessarily wrong about which identifier to call it by.
        """
        declared = [wigos_id, *also_known_as]

        station = self.filter(
            models.Q(wigos_id__in=declared)
            | models.Q(other_wigos_ids__overlap=declared)
        ).first()

        if station is None:
            return self.create(wigos_id=wigos_id, other_wigos_ids=list(also_known_as)), True

        unrecorded = [
            identifier
            for identifier in declared
            if identifier != station.wigos_id and identifier not in station.other_wigos_ids
        ]

        if unrecorded:
            station.other_wigos_ids = [*station.other_wigos_ids, *unrecorded]
            station.save(update_fields=["other_wigos_ids", "modified"])

        return station, False


@register_snippet
class Station(TimeStampedModel):
    """
    A station, keyed on its WIGOS station identifier.

    The WIGOS identifier is the identity authority: OSCAR/Surface, a node's own
    registry and observed traffic are three sources that may each declare the
    same physical station, and all three resolve to one record here -- by any of
    the identifiers it is known by, not only the one it is keyed on.
    """

    FACILITY_TYPE_CHOICES = [
        ("landFixed", "Land Fixed"),
        ("landMobile", "Land Mobile"),
        ("sea", "Sea"),
        ("airFixed", "Air Fixed"),
        ("airMobile", "Air Mobile"),
    ]

    wigos_id = models.CharField(
        max_length=100,
        unique=True,
        help_text=_("WIGOS station identifier"),
    )
    other_wigos_ids = ArrayField(
        models.CharField(max_length=100),
        default=list,
        blank=True,
        help_text=_("Further WIGOS station identifiers the same station is known by"),
    )
    name = models.CharField(max_length=200, blank=True)
    location = models.PointField(
        dim=3,
        null=True,
        blank=True,
        help_text=_("Location of the station, where it is known"),
    )
    facility_type = models.CharField(
        max_length=20,
        choices=FACILITY_TYPE_CHOICES,
        blank=True,
    )
    territory = models.CharField(max_length=100, blank=True)
    wmo_region = models.CharField(max_length=100, blank=True)
    operating_status = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Operational status as reported by OSCAR/Surface"),
    )

    objects = StationQuerySet.as_manager()

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name or self.wigos_id} ({self.wigos_id})"


class StationSourceQuerySet(models.QuerySet):
    def declared_in_oscar(self):
        """What the monitored countries officially declare and still operate.

        OSCAR is notoriously stale -- many of the stations it lists were
        decommissioned years ago -- so only a station it reports as fully
        operational counts as declared. Partly operational, closed, silent and
        unknown are all left out: the declared-but-silent report is only
        actionable if the stations in it are ones somebody expects to hear from.
        """
        return (
            self.filter(
                source_type=StationSource.OSCAR,
                station__operating_status=OPERATIONAL,
            )
            .select_related("station")
            .order_by("station__name")
        )

    def declared_by_node_registry(self, node):
        """A node's own registry declarations, ready to list or export."""
        return (
            self.filter(node=node, source_type=StationSource.NODE_REGISTRY)
            .select_related("station")
            .order_by("station__name")
        )


class StationSource(TimeStampedModel):
    """
    One source's declaration of a station.

    Comparing declarations is the point: a station declared in OSCAR but never
    observed, or observed but declared nowhere, are both findings.
    """

    OSCAR = "oscar"
    NODE_REGISTRY = "node_registry"
    OBSERVED = "observed"

    SOURCE_TYPE_CHOICES = [
        (OSCAR, _("Declared in OSCAR/Surface")),
        (NODE_REGISTRY, _("Declared by the node's station registry")),
        (OBSERVED, _("Observed in notification messages")),
    ]

    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name="sources")
    source_type = models.CharField(max_length=20, choices=SOURCE_TYPE_CHOICES)
    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="station_declarations",
        help_text=_("The node that declared or transmitted the station, where applicable"),
    )

    local_name = models.CharField(
        max_length=200,
        blank=True,
        help_text=_("Name the node assigns to this station"),
    )
    local_id = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("Identifier the node assigns to this station"),
    )

    raw_json = models.JSONField(null=True, blank=True)
    first_seen = models.DateTimeField(default=dj_timezone.now)
    last_seen = models.DateTimeField(null=True, blank=True)

    objects = StationSourceQuerySet.as_manager()

    class Meta:
        ordering = ["station", "source_type"]
        constraints = [
            models.UniqueConstraint(
                fields=["station", "source_type", "node"],
                name="unique_station_declaration_per_node",
            ),
            models.UniqueConstraint(
                fields=["station", "source_type"],
                condition=models.Q(node__isnull=True),
                name="unique_station_declaration_without_node",
            ),
        ]
        verbose_name = _("Station Source")
        verbose_name_plural = _("Station Sources")

    def __str__(self):
        return f"{self.station.wigos_id} - {self.get_source_type_display()}"


class NotificationMessage(TimescaleModel, TimeStampedModel):
    """
    A WIS2 notification message, as observed from one message source.

    The dataset is nullable and the raw topic is always kept, so that traffic on
    a topic no catalogue knows about is investigable rather than discarded. The
    station is likewise nullable: attribution comes only from the message's own
    WIGOS station identifier property, and a message that carries none is
    counted as unattributed rather than guessed at.

    ``time`` is the notification's own publication time, and doubles as the
    hypertable partitioning column.
    """

    source = models.ForeignKey(
        MessageSource,
        on_delete=models.CASCADE,
        related_name="messages",
    )
    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="messages",
    )

    notification_id = models.CharField(
        max_length=255,
        help_text=_("The notification's own UUID"),
    )
    topic = models.CharField(
        max_length=1000,
        help_text=_("Raw MQTT topic the message arrived on"),
    )
    wigos_station_id = models.CharField(
        max_length=100,
        blank=True,
        help_text=_("WIGOS station identifier declared by the message, when present"),
    )
    data_id = models.CharField(max_length=500, blank=True)
    metadata_id = models.CharField(
        max_length=500,
        blank=True,
        help_text=_("Discovery metadata identifier declared by the message"),
    )
    received_datetime = models.DateTimeField(
        default=dj_timezone.now,
        db_index=True,
        help_text=_("When this tool stored the message, as against when it was published"),
    )
    canonical_link = models.URLField(max_length=1000, blank=True)
    raw_json = models.JSONField()

    class Meta:
        constraints = [
            # A notification is stored once per source. `time` takes part
            # because TimescaleDB requires the partitioning column in every
            # unique index on a hypertable; it is the message's own publication
            # time, so it is fixed for a given notification and the constraint
            # de-duplicates on the source/UUID pair in practice.
            models.UniqueConstraint(
                fields=["source", "notification_id", "time"],
                name="unique_notification_per_source",
            ),
        ]
        indexes = [
            models.Index(fields=["notification_id"]),
            models.Index(fields=["topic"]),
        ]
        verbose_name = _("Notification Message")
        verbose_name_plural = _("Notification Messages")

    def __str__(self):
        return f"{self.notification_id} @ {self.time}"


class NodeLastSeen(TimeStampedModel):
    """When a node was last heard from, maintained as messages are stored.

    The headline question -- which centres have gone quiet -- is asked of every
    node at once, and answering it from the time series would mean scanning a
    hypertable that grows with the region's traffic. Maintaining the answer on
    ingest turns it into one indexed row per node.

    The time held is the notification's own publication time, and it only ever
    moves forward: a redelivery, or a message that took the long way round,
    says nothing new about when the centre was last publishing.

    One row per node, not per node and source. "When did this centre last
    publish" is a question about the centre; which vantage point saw it is a
    propagation question, and the raw rows carry the source for that.
    """

    node = models.OneToOneField(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="last_seen",
    )
    last_message_at = models.DateTimeField(
        db_index=True,
        help_text=_("Publication time of the most recent message seen from this node"),
    )

    class Meta:
        ordering = ["-last_message_at"]
        verbose_name = _("Node Last Seen")
        verbose_name_plural = _("Node Last Seen")

    def __str__(self):
        return f"{self.node.centre_id} @ {self.last_message_at}"


#: What an hourly rollup counts separately. Named once, because the count is
#: only meaningful against its grain: the constraint that keeps a bucket
#: unique and the query that derives it have to agree, and they are written in
#: different modules.
ROLLUP_GRAIN = ("hour", "source", "node", "dataset", "station")


class HourlyRollup(TimeStampedModel):
    """How many notifications one node published in one UTC hour.

    Raw messages are kept for a forensic window only, so the history of the
    region lives here instead: rollups are never expired, and are the only
    thing that still knows what a centre was doing last year.

    Counts are derived from stored rows rather than incremented on receipt.
    A notification can be delivered more than once -- a wildcard sweep runs
    alongside the per-centre subscriptions -- and per-source uniqueness makes
    that harmless for storage, while a receive-time counter would silently
    count it twice.

    The source takes part in the grain for the same reason. The same
    notification observed at a node's own broker and at the Global Broker is
    two rows on purpose, and summing them into one count would double every
    number the moment origin ingestion is switched on. Deriving per source and
    choosing a vantage point when reading keeps the counts meaning something.

    The hour is the start of a UTC hour, taken from the notification's own
    publication time.

    A dataset or station deleted outright takes its name off the counts it
    earned, which then join the unclaimed bucket for that hour; where that
    bucket already exists the delete is refused rather than allowed to
    duplicate it. Catalogue syncs mark datasets deleted rather than removing
    them, so this is the hand-deletion case, and a loud refusal is the right
    end of it while the history is still worth keeping.
    """

    hour = models.DateTimeField(
        db_index=True,
        help_text=_("Start of the UTC hour this count covers"),
    )
    source = models.ForeignKey(
        MessageSource,
        on_delete=models.CASCADE,
        related_name="rollups",
        help_text=_("The vantage point these messages were observed from"),
    )
    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="rollups",
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rollups",
        help_text=_("Null for traffic on a topic no dataset claims"),
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="rollups",
        help_text=_("Null for messages carrying no known station"),
    )

    message_count = models.PositiveIntegerField(default=0)

    class Meta:
        ordering = ["-hour"]
        constraints = [
            # ``nulls_distinct=False`` is what makes this a usable key: a
            # dataset or station of None is a real bucket -- unclaimed topics
            # and unattributed messages -- and Postgres's default would let one
            # be inserted twice over.
            models.UniqueConstraint(
                fields=ROLLUP_GRAIN,
                name="unique_rollup_per_hour_and_grain",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            models.Index(fields=["node", "-hour"]),
            # "When did this dataset last publish" is asked of every dataset in
            # the region at once, every time the overview is opened. Leading on
            # the dataset makes each of those answers one backwards walk of
            # this index rather than a scan of the node's whole history.
            models.Index(fields=["dataset", "-hour"]),
        ]
        verbose_name = _("Hourly Rollup")
        verbose_name_plural = _("Hourly Rollups")

    def __str__(self):
        return f"{self.node_id} @ {self.hour}: {self.message_count}"


class CadenceBaseline(models.Model):
    """How often a dataset has been observed to publish, learned from its own
    history.

    A single silence threshold across the region is not a thing that exists.
    One centre publishes surface observations in hourly bursts, another issues
    a climate summary once a month, and both are healthy; a fixed threshold
    either reports the second as broken or lets the first go quiet for weeks
    unremarked. So each dataset is judged against itself.

    The interval is a high percentile of the gaps between the hours the dataset
    was actually seen publishing in, taken from the rollups because they are
    what survives raw expiry. A percentile rather than a mean or a maximum: the
    mean of a bursty dataset is shorter than most of its real gaps, and the
    maximum is whatever its worst outage was, which would make silence
    unreportable ever after.

    Written down rather than derived when asked for, because it is a scan of
    months of buckets for every dataset in the region -- far too much to run
    behind a page -- and because it moves in weeks, not minutes.

    A baseline is never removed for want of history. A dataset that stops
    publishing altogether eventually has too few gaps left in the window to
    learn from, and that is precisely when its baseline is the thing that
    reports it: deleting it then would make the tool go quiet about the centre
    at the same moment the centre went quiet. ``learned_at`` is what says how
    old the answer is.
    """

    dataset = models.OneToOneField(
        Dataset,
        on_delete=models.CASCADE,
        related_name="cadence_baseline",
    )
    interval_hours = models.FloatField(
        help_text=_("How long this dataset normally goes between publications"),
    )
    observations = models.PositiveIntegerField(
        help_text=_("How many observed gaps the interval was learned from"),
    )
    learned_at = models.DateTimeField(
        help_text=_("When the run that produced this interval read the history"),
    )

    class Meta:
        ordering = ["dataset"]
        verbose_name = _("Cadence Baseline")
        verbose_name_plural = _("Cadence Baselines")

    def __str__(self):
        return f"{self.dataset_id}: every {self.interval_hours}h"


class PropagationGapQuerySet(models.QuerySet):
    def open(self):
        """Gaps the world is still not known to have seen.

        A gap closed by a late arrival stays on the record -- it is evidence of
        how slow that path was -- but it is not something anyone should be sent
        to investigate, so reading defaults to what is still missing.
        """
        return self.filter(resolved_at__isnull=True)

    def within_evidence(self, now=None):
        """Gaps something could still settle either way.

        The rows that would close a gap are the Global Broker's copies of the
        notification, and those are kept for the forensic window only. Inside
        it a gap is a standing question: a late arrival can still close it, and
        a run told to look further back can still find the answer.
        """
        return self.filter(published_at__gte=_evidence_horizon(now))

    def beyond_evidence(self, now=None):
        """Gaps nothing can check again.

        Past the horizon the Global Broker rows have been expired, so the
        notification's absence there no longer says the world never carried it
        -- only that this tool no longer holds either answer. Such a gap can
        never be closed and never be re-detected.

        It is kept rather than retired, because it is the last thing that
        holds the UUID and the rollups carry counts alone. What it stops being
        is something to send somebody to a centre about, which is why the
        reports ask this question rather than deleting the row.
        """
        return self.filter(published_at__lt=_evidence_horizon(now))


def _evidence_horizon(now):
    """The instant before which a gap's evidence is no longer held.

    The raw retention cutoff itself rather than a horizon of its own: the two
    disagreeing would mean either reporting gaps nothing can check, or
    withholding ones something still can.

    Imported here rather than at the top of the module: expiry is written in
    terms of the rows it removes, so importing it up here would close a
    circle.
    """
    from .retention import raw_retention_cutoff

    return raw_retention_cutoff(now=now)


class PropagationGap(models.Model):
    """A notification a centre published that the world never saw.

    This is the finding neither vantage point can make alone. The node's own
    broker says the notification exists; the Global Broker, past a grace period
    that absorbs ordinary latency, has never carried it. Matching is by the
    notification's own UUID, which Global Brokers preserve when republishing.

    A row per notification, not a count: "seventeen messages did not
    propagate" cannot be investigated, while a UUID, a topic and a publication
    time can be taken to the centre.

    Recorded rather than derived on demand, because the evidence expires. Raw
    messages are kept for a forensic window only and the rollups carry counts
    rather than UUIDs, so a gap not written down while its rows still exist is
    a finding that cannot be made again.

    Which is also why a gap outlives what could settle it. Once its evidence
    has expired nothing can close it and nothing can find it again, and a gap
    this tool merely stopped being able to check is not the same finding as
    one the world is still not carrying. Both stay on the record; which is
    which is ``within_evidence`` and ``beyond_evidence`` above.

    The origin broker record may be replaced by a later catalogue sync, and the
    dataset may be deleted by hand; neither unmakes the observation, so both are
    held loosely and the gap keeps the topic and the UUID it was found by.

    Its own timestamps rather than the usual created and modified pair: a gap
    is a statement about three instants that are not this row's history --
    when the centre published, when this tool saw that, and when the world was
    concluded not to have. ``detected_at`` is when the row was written, and is
    the one a created stamp would have duplicated.
    """

    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="propagation_gaps",
    )
    origin_source = models.ForeignKey(
        MessageSource,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="propagation_gaps",
        help_text=_("The vantage point that saw the notification published"),
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="propagation_gaps",
        help_text=_("Null where the topic belongs to no dataset the registry knows"),
    )

    notification_id = models.CharField(
        max_length=255,
        help_text=_("The notification's own UUID, as both vantage points carry it"),
    )
    topic = models.CharField(
        max_length=1000,
        help_text=_("Raw MQTT topic the notification was published on"),
    )

    published_at = models.DateTimeField(
        help_text=_("The notification's own publication time"),
    )
    observed_at_origin = models.DateTimeField(
        help_text=_("When this tool saw the notification at the node's own broker"),
    )
    detected_at = models.DateTimeField(
        help_text=_("When the evaluation concluded the world had not seen it"),
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the notification was finally seen on the Global Broker"),
    )

    objects = PropagationGapQuerySet.as_manager()

    class Meta:
        ordering = ["-published_at"]
        constraints = [
            # A notification UUID is unique in WIS2, so a centre's gap is
            # named once however many times the evaluation window is
            # recomputed over it, and however many broker records the centre
            # has had in the meantime.
            models.UniqueConstraint(
                fields=["node", "notification_id"],
                name="unique_propagation_gap_per_notification",
            ),
        ]
        indexes = [
            models.Index(fields=["node", "-published_at"]),
            models.Index(fields=["resolved_at"]),
        ]
        verbose_name = _("Propagation Gap")
        verbose_name_plural = _("Propagation Gaps")

    def __str__(self):
        return f"{self.notification_id} published @ {self.published_at}"


class LinkProbeQuerySet(models.QuerySet):
    def unretrievable(self):
        """Probes that found the advertised file was not there to be had.

        Everything the outcomes below do not excuse, so that an outcome added
        later is a finding until somebody decides otherwise -- the safe way
        round for a diagnostic, which should rather report something it cannot
        classify than quietly drop it.
        """
        return self.exclude(outcome__in=LinkProbe.NOT_THE_CENTRES_FAULT)


class LinkProbe(models.Model):
    """What became of one lightweight request for a file a notification
    advertised.

    Why this is asked at all, and how the sample is kept bounded, is
    :mod:`wis2watch.core.probes`. What matters about the row is the outcome.
    "Could not be fetched" is not a finding anyone can act on: a 404 goes to
    the centre's data publishing, an expired certificate to whoever runs its
    web server, a connection that never opens to its network. So the transport
    failures are held apart from the HTTP answers, and both from a server that
    declines headers-only requests -- which has said nothing about the file at
    all, and is this tool's limitation rather than the centre's.

    ``hour`` is the UTC hour the sampled notifications were published in, kept
    beside the probe rather than derived from it, because it is what the
    per-node bound is counted against: it is the sample that is bounded, and
    the sample belongs to an hour of a centre's traffic, not to whenever a run
    happened to get to it.

    Its own ``probed_at`` rather than the usual created and modified pair: a
    probe is one observation and is never revised. Another answer is another
    row, which is the point -- a file that was there at noon and gone at one
    is two facts.
    """

    RETRIEVABLE = "retrievable"
    MISSING = "missing"
    FORBIDDEN = "forbidden"
    SERVER_ERROR = "server_error"
    UNEXPECTED_STATUS = "unexpected_status"
    NOT_PROBEABLE = "not_probeable"
    TLS_ERROR = "tls_error"
    UNREACHABLE = "unreachable"
    TIMEOUT = "timeout"
    BAD_URL = "bad_url"

    OUTCOME_CHOICES = [
        (RETRIEVABLE, _("Retrievable")),
        (MISSING, _("Missing")),
        (FORBIDDEN, _("Access denied")),
        (SERVER_ERROR, _("Server error")),
        (UNEXPECTED_STATUS, _("Unexpected status")),
        (NOT_PROBEABLE, _("Server refuses headers-only requests")),
        (TLS_ERROR, _("Certificate or TLS failure")),
        (UNREACHABLE, _("Connection failed")),
        (TIMEOUT, _("Timed out")),
        (BAD_URL, _("Link is not a fetchable URL")),
    ]

    #: The two answers that say nothing against the centre: the file came back,
    #: or the server would not answer a headers-only request at all and so
    #: never spoke about the file. Named once because the counts a run reports
    #: and the rows a report reads have to agree on it, and they are decided in
    #: different modules.
    NOT_THE_CENTRES_FAULT = (RETRIEVABLE, NOT_PROBEABLE)

    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="link_probes",
    )
    dataset = models.ForeignKey(
        Dataset,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="link_probes",
        help_text=_("Null where the topic belongs to no dataset the registry knows"),
    )

    notification_id = models.CharField(
        max_length=255,
        help_text=_("The notification that advertised this link"),
    )
    url = models.URLField(
        max_length=1000,
        help_text=_("The canonical link, as the notification gave it"),
    )

    outcome = models.CharField(max_length=20, choices=OUTCOME_CHOICES)
    status_code = models.PositiveSmallIntegerField(
        null=True,
        blank=True,
        help_text=_("Null where no HTTP response came back at all"),
    )
    latency_ms = models.PositiveIntegerField(
        null=True,
        blank=True,
        help_text=_("How long the request took, including a failure"),
    )
    error = models.TextField(
        blank=True,
        help_text=_("What the transport failure said, in its own words"),
    )

    hour = models.DateTimeField(
        help_text=_("Start of the UTC hour the sampled notifications fall in"),
    )
    probed_at = models.DateTimeField(
        help_text=_("When the request was made"),
    )

    objects = LinkProbeQuerySet.as_manager()

    class Meta:
        ordering = ["-probed_at"]
        indexes = [
            models.Index(fields=["node", "-probed_at"]),
            # The bound is counted per node and hour before every run, so this
            # is on the read path of the sampling itself rather than only of
            # whatever reports the results.
            models.Index(fields=["node", "hour"]),
            models.Index(fields=["outcome"]),
        ]
        verbose_name = _("Link Probe")
        verbose_name_plural = _("Link Probes")

    def __str__(self):
        return f"{self.url}: {self.outcome}"


class UnregisteredCentreQuerySet(models.QuerySet):
    def unregistered(self):
        """The centres the registry still does not know about.

        A row is kept after the registry catches up, because "this centre was
        publishing for a fortnight before anyone registered it" is worth being
        able to say. What nobody should be sent to investigate is a gap that
        has since been closed, so reading defaults to the ones still open.
        """
        return self.filter(registered_at__isnull=True)


class UnregisteredCentre(TimeStampedModel):
    """A centre in the monitored region publishing without a registry record.

    The per-centre subscriptions are built from the registry, so they are
    structurally blind to a centre no catalogue has indexed -- including a
    newly onboarded country whose metadata registration is incomplete. The
    periodic wildcard sweep is what looks past them, and this is what it finds.

    Membership of the region is decided by the centre ID's ISO 3166 prefix
    alone. That is the one question about an unknown centre that can be
    answered without a registry, which is precisely the position the sweep is
    in: it is looking at traffic from a centre nothing has ever heard of.

    A row per centre rather than per message. What is worth reporting is that
    the centre exists at all; the traffic itself is stored like any other, with
    no node attached, and the sample topic here is only enough to say what kind
    of publishing was seen.
    """

    centre_id = models.CharField(
        max_length=200,
        unique=True,
        help_text=_("WIS2 centre ID observed publishing, e.g. ke-kmd"),
    )
    country = CountryField(
        blank=True,
        blank_label=_("Select Country"),
        verbose_name=_("Country"),
        help_text=_("Derived from the centre ID prefix"),
    )

    sample_topic = models.CharField(
        max_length=1000,
        blank=True,
        help_text=_("A topic this centre was last seen publishing on"),
    )

    first_seen_at = models.DateTimeField(
        help_text=_("When a sweep first saw this centre publishing"),
    )
    last_seen_at = models.DateTimeField(
        help_text=_("When a sweep last saw this centre publishing"),
    )
    registered_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the registry caught up with this centre"),
    )

    objects = UnregisteredCentreQuerySet.as_manager()

    class Meta:
        ordering = ["centre_id"]
        indexes = [
            models.Index(fields=["registered_at"]),
        ]
        verbose_name = _("Unregistered Centre")
        verbose_name_plural = _("Unregistered Centres")

    def __str__(self):
        return f"{self.centre_id} (last seen {self.last_seen_at})"


class SyncLog(models.Model):
    """
    One run of a synchronisation job, with its counts and any error.
    """

    CATALOGUE = "catalogue"
    DISCOVERY_METADATA = "discovery_metadata"
    LINK_PROBES = "link_probes"
    NODE_STATIONS = "node_stations"
    OSCAR_STATIONS = "oscar_stations"
    WILDCARD_SWEEP = "wildcard_sweep"

    SYNC_TYPE_CHOICES = [
        (CATALOGUE, _("Global Discovery Catalogue")),
        (DISCOVERY_METADATA, _("Discovery Metadata")),
        (LINK_PROBES, _("Canonical Link Probes")),
        (NODE_STATIONS, _("Node Stations")),
        (OSCAR_STATIONS, _("OSCAR Stations")),
        (WILDCARD_SWEEP, _("Wildcard Sweep")),
    ]

    SUCCESS = "success"
    PARTIAL = "partial"
    FAILED = "failed"

    STATUS_CHOICES = [
        (SUCCESS, _("Success")),
        (PARTIAL, _("Partial Success")),
        (FAILED, _("Failed")),
    ]

    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sync_logs",
    )
    catalogue = models.ForeignKey(
        GlobalDiscoveryCatalogue,
        on_delete=models.CASCADE,
        null=True,
        blank=True,
        related_name="sync_logs",
    )
    sync_type = models.CharField(max_length=50, choices=SYNC_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)

    items_found = models.IntegerField(default=0)
    items_created = models.IntegerField(default=0)
    items_updated = models.IntegerField(default=0)
    items_deleted = models.IntegerField(default=0)
    items_errored = models.IntegerField(
        default=0,
        help_text=_("Items the run could not store, having stepped over them"),
    )

    error_message = models.TextField(
        blank=True,
        help_text=_("Why the run failed as a whole, as opposed to a single item"),
    )

    started_at = models.DateTimeField(default=dj_timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["-started_at"]
        indexes = [
            models.Index(fields=["node", "-started_at"]),
            models.Index(fields=["sync_type", "-started_at"]),
        ]

    def __str__(self):
        target = self.node or self.catalogue or _("unknown")
        return f"{target} - {self.sync_type} - {self.status} ({self.started_at})"

    @property
    def summary(self):
        """What the run came to, in one line, for a log or a console."""
        return (
            f"{self.status} found={self.items_found} created={self.items_created} "
            f"updated={self.items_updated} errored={self.items_errored}"
        )


class ReportedFinding(models.Model):
    """One finding the digest has already told somebody about.

    The digest exists to carry what changed, which means something has to
    remember what was carried last time. This is that memory: a row per
    finding the last digest named, keyed by the report it came from and by
    whatever identifies it within that report -- a WIGOS identifier, a centre,
    a notification UUID.

    A finding that is no longer found has its row deleted rather than closed,
    so that the row's existence is the whole answer to "has this been
    mentioned". Nothing here is evidence -- the reports are derived from the
    observations whenever they are asked -- and keeping the row would make a
    problem that came back unmentionable, which a problem that came back is
    not: it is news.

    Which is exactly why ``last_seen_at`` is kept. A report can stop listing a
    finding without the problem having gone anywhere: propagation gaps are
    withheld for a centre whose own broker cannot currently be reached, and
    the unattributed share is worked out over a trailing window a quiet centre
    falls out of. Deleting on the first absence would announce those as
    cleared and then announce them again on their return, which is the one
    thing a digest must not do. So a finding is only let go once it has been
    absent for long enough that its absence means something.

    The summary is stored alongside because it is the only thing that can
    still describe a finding once it is gone. A digest that says a gap has
    closed has to name which, and by then the report no longer lists it.
    """

    report_slug = models.CharField(
        max_length=100,
        help_text=_("Which gap report this finding came from"),
    )
    key = models.CharField(
        max_length=500,
        help_text=_("What identifies the finding within its report"),
    )
    summary = models.CharField(
        max_length=1000,
        help_text=_("How the finding read when it was last reported"),
    )

    reported_at = models.DateTimeField(
        help_text=_("When a digest carried this finding"),
    )
    last_seen_at = models.DateTimeField(
        help_text=_("When a digest run last found the report still listing it"),
    )

    class Meta:
        ordering = ["report_slug", "key"]
        constraints = [
            # A finding is remembered once. Two rows for one finding would
            # make it new again on the run that read the second of them.
            models.UniqueConstraint(
                fields=["report_slug", "key"],
                name="unique_reported_finding",
            ),
        ]
        verbose_name = _("Reported Finding")
        verbose_name_plural = _("Reported Findings")

    def __str__(self):
        return f"{self.report_slug}: {self.key}"


class HardFailureQuerySet(models.QuerySet):
    def open(self):
        """The failures that have not been seen to clear.

        A failure stays on the record after it clears -- how long the region
        went unwatched is worth being able to ask -- but only an open one is
        anybody's to act on now.
        """
        return self.filter(resolved_at__isnull=True)


class HardFailure(models.Model):
    """A spell in which this tool itself stopped working.

    Everything else recorded here is a finding about the region. This is the
    other kind: the Global Broker connection lost, or nothing at all being
    ingested. Neither says anything about whether African centres are
    publishing, and both mean that nothing the tool goes on to say about them
    can be believed until it is fixed.

    A row per spell rather than per check, so that a failure lasting a day is
    one thing that happened rather than a thousand. ``notified_at`` is what
    keeps it to one message: an outage is announced once it has lasted past
    the threshold, and again only when it clears.

    The threshold is not stored. It is a first guess about what counts as more
    than a blip, and it is meant to be revised once the region's normal
    rhythms are known; a row that recorded the guess it was opened under would
    have to be reconciled with the setting on every read.
    """

    GLOBAL_BROKER_LOST = "global_broker_lost"
    INGESTION_STALLED = "ingestion_stalled"

    KIND_CHOICES = [
        (GLOBAL_BROKER_LOST, _("Global Broker connection lost")),
        (INGESTION_STALLED, _("Ingestion stalled")),
    ]

    kind = models.CharField(max_length=50, choices=KIND_CHOICES)
    detail = models.TextField(
        blank=True,
        help_text=_("What was found to be wrong, as it stood when last checked"),
    )

    started_at = models.DateTimeField(
        help_text=_("When the failure began, as closely as it can be told"),
    )
    notified_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the failure was announced, if it lasted long enough to be"),
    )
    resolved_at = models.DateTimeField(
        null=True,
        blank=True,
        help_text=_("When the failure was seen to have cleared"),
    )

    objects = HardFailureQuerySet.as_manager()

    class Meta:
        ordering = ["-started_at"]
        constraints = [
            # One open failure of each kind. The checks run on a beat, and two
            # open rows for one outage would announce it twice and clear it
            # once.
            models.UniqueConstraint(
                fields=["kind"],
                condition=models.Q(resolved_at__isnull=True),
                name="unique_open_hard_failure_per_kind",
            ),
        ]
        indexes = [
            models.Index(fields=["kind", "-started_at"]),
        ]
        verbose_name = _("Hard Failure")
        verbose_name_plural = _("Hard Failures")

    def __str__(self):
        return f"{self.get_kind_display()} since {self.started_at}"
