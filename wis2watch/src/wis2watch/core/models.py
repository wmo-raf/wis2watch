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


class MessageSource(TimeStampedModel):
    """
    A vantage point from which WIS2 notification messages are observed.

    Both a Global Broker and a node's own origin broker are message sources.
    Modelling them as one entity is what makes "published at origin but never
    seen on the Global Broker" expressible: the same notification observed from
    two sources is two rows that can be matched, not one row that overwrites
    the other.
    """

    GLOBAL_BROKER = "global_broker"
    ORIGIN_BROKER = "origin_broker"

    SOURCE_TYPE_CHOICES = [
        (GLOBAL_BROKER, _("Global Broker")),
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

    panels = [
        FieldPanel("name"),
        FieldPanel("source_type"),
        FieldPanel("centre_id"),
        FieldPanel("node"),
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


@register_snippet
class Dataset(TimeStampedModel):
    """
    A dataset a node claims to publish, as described by a WCMP2 discovery
    metadata record.
    """

    DATA_POLICY_CHOICES = [
        ("core", "Core"),
        ("recommended", "Recommended"),
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
    # is meant to set: what this tool should expect of it. Everything else is
    # the catalogue's to say, and a hand-edit would be overwritten by the next
    # sync anyway.
    panels = [
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

    def with_last_transmitted(self):
        """Each declaration beside when its station was last heard transmitting.

        Declared and transmitting are different questions, and only the
        observation can answer the second: a registry declaration is confirmed
        by every sync run, so its own timestamp says when the node last
        repeated itself, never whether anything is still publishing. Reading
        the observation alongside is what makes a silent station nameable.
        """
        transmitted = (
            StationSource.objects.filter(
                station_id=models.OuterRef("station_id"),
                source_type=StationSource.OBSERVED,
                last_seen__isnull=False,
            )
            .order_by("-last_seen")
            .values("last_seen")[:1]
        )

        return self.annotate(last_transmitted=models.Subquery(transmitted))


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
    received_datetime = models.DateTimeField(default=dj_timezone.now)
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


class SyncLog(models.Model):
    """
    One run of a synchronisation job, with its counts and any error.
    """

    CATALOGUE = "catalogue"
    DISCOVERY_METADATA = "discovery_metadata"
    NODE_STATIONS = "node_stations"
    OSCAR_STATIONS = "oscar_stations"

    SYNC_TYPE_CHOICES = [
        (CATALOGUE, _("Global Discovery Catalogue")),
        (DISCOVERY_METADATA, _("Discovery Metadata")),
        (NODE_STATIONS, _("Node Stations")),
        (OSCAR_STATIONS, _("OSCAR Stations")),
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
