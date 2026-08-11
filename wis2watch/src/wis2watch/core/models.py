from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField
from django_countries.widgets import CountrySelectWidget
from django_extensions.db.models import TimeStampedModel
from timescale.db.models.models import TimescaleModel
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.snippets.models import register_snippet

from .countries import monitored_country_code_for_centre_id


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

    @property
    def lock_key(self):
        return f"mqtt_node_{self.id}_lock"


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
    is_reachable = models.BooleanField(default=True)
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

    STATUS_CHOICES = [
        ("active", "Active"),
        ("inactive", "Inactive"),
        ("deleted", "Deleted"),
    ]

    node = models.ForeignKey(WIS2Node, on_delete=models.CASCADE, related_name="datasets")
    identifier = models.CharField(
        max_length=500,
        unique=True,
        help_text=_("URN identifier of the dataset"),
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
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default="active")
    metadata_created = models.DateTimeField(null=True, blank=True)
    metadata_updated = models.DateTimeField(null=True, blank=True)
    last_synced = models.DateTimeField(null=True, blank=True)

    class Meta:
        ordering = ["node", "title"]
        indexes = [
            models.Index(fields=["wmo_topic_hierarchy"]),
            models.Index(fields=["status"]),
        ]

    def __str__(self):
        return f"{self.title} ({self.identifier})"


@register_snippet
class Station(TimeStampedModel):
    """
    A station, keyed on its WIGOS station identifier.

    The WIGOS identifier is the identity authority: OSCAR/Surface, a node's own
    registry and observed traffic are three sources that may each declare the
    same physical station, and all three resolve to one record here.
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

    class Meta:
        ordering = ["name"]

    def __str__(self):
        return f"{self.name or self.wigos_id} ({self.wigos_id})"


class StationSourceQuerySet(models.QuerySet):
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

    error_message = models.TextField(blank=True)

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
