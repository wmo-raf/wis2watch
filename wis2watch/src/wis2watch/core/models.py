from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.contrib.postgres.fields import ArrayField
from django.core.exceptions import ValidationError
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
    is_active = models.BooleanField(
        default=True,
        help_text=_(
            "Switch a catalogue off here rather than deleting it: the Global "
            "Services this release ships with are recreated on the next start, "
            "and a deleted one comes back"
        ),
    )
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


#: What a wis2box serves under its own address, and the field each is kept in.
#: Held here rather than spelled where they are built, because they are written
#: twice -- derived when a node first learns its address, and re-derived when
#: that address is corrected under it -- and two copies of a path that drifted
#: apart would leave a node asking half its endpoints at a host it has left.
DERIVED_ENDPOINTS = {
    "discovery_metadata_url": "/oapi/collections/discovery-metadata/items?f=json",
    "stations_url": "/oapi/collections/stations/items?f=json",
}


class WIS2NodeQuerySet(models.QuerySet):
    def advertising_a_station_registry(self):
        """The centres there is somewhere to ask what stations they declare."""
        return self.exclude(stations_url="")

    def advertising_no_station_registry(self):
        """The centres there is nowhere to ask, so nothing knows what they declare.

        Named apart from the ones that answered and declared nothing, because
        every surface that reports on a centre's stations has to keep the two
        apart. A centre nobody asked has no registry declarations for the same
        reason it has no registry sync log: nothing ever went and looked.
        """
        return self.filter(stations_url="")


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

    advertised_base_url = models.URLField(
        max_length=500,
        blank=True,
        editable=False,
        help_text=_(
            "The address this centre's own catalogue records last pointed at. "
            "Kept beside the address in use so the two can be told apart: they "
            "agree while the catalogue's address is the one being asked, and "
            "differ once somebody has corrected it by hand."
        ),
    )

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

    objects = WIS2NodeQuerySet.as_manager()

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
            # Filled in only where nothing is there. An endpoint an operator
            # has corrected is theirs, and a base URL that moves under one
            # does not entitle this to undo the correction -- moving what was
            # derived is the catalogue sync's job, where it can tell which
            # were derived and which were typed.
            for field, path in DERIVED_ENDPOINTS.items():
                if not getattr(self, field):
                    setattr(self, field, f"{self.base_url}{path}")

        super().save(*args, **kwargs)

    @property
    def advertises_station_registry(self):
        """Whether there is anywhere to ask this centre what stations it declares.

        Every surface that reports on a centre's stations reads this before it
        says anything about what the centre declares. Without it, a centre
        whose catalogue records point at no address for it reads exactly as a
        centre that answered and named nothing -- which is a claim about the
        centre, when what is true is that nobody asked it.
        """
        return bool(self.stations_url)

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

    def dialled(self):
        """The sources a connection is actually opened to.

        Narrower than :meth:`connections` by one more kind: a centre's own
        message archive is read over HTTP on a schedule, so it has an address
        of its own to correct but nothing that holds a connection open. Asked
        wherever the question is how the connections are faring, since an
        archive counted among them would sit there for ever as one that never
        came up.
        """
        return self.connections().exclude(source_type=MessageSource.ORIGIN_API)

    def origin_vantages(self):
        """The centres' own vantage points this tool is meant to be watching.

        Both transports a centre offers on its own account: the broker it
        publishes to, and the archive of those notifications it serves over
        HTTP. Which of the two a centre was heard through does not change what
        being heard entitles this tool to say about it.

        A vantage point switched off in the admin is not one of them:
        reachability is only ever what the last attempt recorded, so a source
        nothing is asking any more carries an answer that has since gone stale.
        """
        return self.filter(
            source_type__in=MessageSource.ORIGIN_TRANSPORTS,
            is_active=True,
            node__isnull=False,
        )

    def watched_origins(self):
        """The origin vantage points whose view of their centre can be trusted now.

        What decides whether a centre may be judged on the difference between
        what it published itself and what the Global Broker carried -- and so
        is asked both by the evaluation that records propagation gaps and by
        the report that lists them. Written once, because a gap recorded while
        a centre answered and then reported after it went dark is exactly the
        finding neither of them should stand behind.

        A null reachability is "not attempted yet", and is no more a licence to
        judge a centre than a failure is.
        """
        return self.origin_vantages().filter(is_reachable=True)

    def archives_to_poll(self):
        """The centres' own archives worth asking on a schedule.

        The ones whose own broker will not answer, which are the shakiest
        centres in the region and the ones most worth watching. Until something
        polls them they have no origin witness at all, and the comparison this
        tool exists to make cannot be made for them.

        Confirmed reachable is the only thing that keeps a centre out. A broker
        never attempted is null rather than fine; a broker switched off carries
        an answer that has since gone stale; and a centre with no broker
        registered has never been heard from at all. None of those three can be
        judged on propagation until its archive is asked, which is precisely
        what makes them worth asking.

        A centre whose broker does answer is left alone. Its archive and its
        broker are the same witness -- the same notifications published by the
        same centre -- so polling it would buy a second copy of what is already
        held rather than any further evidence. The cost of that is accepted
        deliberately: a healthy centre gets no protection from this tool's own
        downtime, and recovering such a window is the management command's job
        rather than the schedule's.

        An archive with no address is not asked either. Where a centre serves
        its notifications is inferred rather than advertised, so a poll of one
        this tool has nowhere to ask would record the centre as unreachable
        over a hole in our own registry.

        The broker is looked for through the manager rather than through
        ``self``, because what it asks is a different question of the same
        table: whichever archives this queryset has been narrowed to, the
        broker that speaks for one of their centres is not among them.
        """
        broker_that_answers = MessageSource.objects.filter(
            node_id=models.OuterRef("node_id"),
            source_type=MessageSource.ORIGIN_BROKER,
            is_active=True,
            is_reachable=True,
        )

        return (
            self.origin_vantages()
            .filter(source_type=MessageSource.ORIGIN_API)
            .exclude(api_url="")
            .exclude(models.Exists(broker_that_answers))
        )


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
    dials; ``carried_by`` is what says so. Nor is it always a connection at
    all: a centre's own notification archive is an HTTP endpoint asked on a
    schedule, and what it carries is the same centre's traffic seen a second
    way rather than a second kind of finding.
    """

    GLOBAL_BROKER = "global_broker"
    GLOBAL_CACHE = "global_cache"
    ORIGIN_BROKER = "origin_broker"
    ORIGIN_API = "origin_api"

    SOURCE_TYPE_CHOICES = [
        (GLOBAL_BROKER, _("Global Broker")),
        (GLOBAL_CACHE, _("Global Cache")),
        (ORIGIN_BROKER, _("Origin Broker")),
        (ORIGIN_API, _("Origin API")),
    ]

    #: The ways a centre offers its own traffic on its own account. Both are
    #: the centre speaking for itself, which is what a propagation finding is
    #: entitled to be judged against; how it was reached is transport.
    ORIGIN_TRANSPORTS = (ORIGIN_BROKER, ORIGIN_API)

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

    host = models.CharField(max_length=255, blank=True)
    port = models.IntegerField(default=1883)
    username = models.CharField(max_length=100, blank=True)
    password = models.CharField(max_length=255, blank=True)
    use_tls = models.BooleanField(default=False)

    # Nothing advertises where a centre's notification archive lives: no WCMP2
    # link relation names it, and serving one at all is a wis2box convention
    # rather than a WIS2 requirement. So the address is worked out from the
    # node's base URL, which is itself inferred from a canonical link -- and a
    # centre that publishes those into separate object storage yields a base
    # URL the archive does not live under. Correcting it by hand has to be the
    # last word, which is why a sync offers this once and never writes over it.
    api_url = models.URLField(
        max_length=1000,
        blank=True,
        verbose_name=_("Message archive URL"),
        help_text=_(
            "Where this centre serves its own notification archive. Offered "
            "from the node's address, which is a guess; correct it here and "
            "the correction stands."
        ),
    )

    is_active = models.BooleanField(
        default=True,
        help_text=_(
            "Switch a broker off here rather than deleting it: the Global "
            "Brokers this release ships with are recreated on the next start, "
            "and an origin broker by the next catalogue sync"
        ),
    )

    # Reachability is diagnostic state, not an error condition: a broker that
    # cannot be reached from outside is a finding this tool exists to report.
    # Null until a connection has been attempted, because "we have not looked
    # yet" and "it does not answer" are different findings, and a broker a
    # catalogue sync has just advertised is in the first state, not the second.
    is_reachable = models.BooleanField(
        null=True,
        blank=True,
        default=None,
        help_text=_("Empty until this vantage point has actually been asked"),
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
        # Editable, and the only field on this form that is meant to be
        # corrected by hand: the address is derived from an inference about
        # where the centre answers, and the operator is the one who can tell
        # that it is wrong.
        MultiFieldPanel(
            [FieldPanel("api_url")],
            heading=_("Message Archive"),
        ),
        FieldPanel("is_active"),
    ]

    class Meta:
        ordering = ["source_type", "name"]
        constraints = [
            # A node has one vantage point of any given kind: one broker of
            # its own, and one archive of its own. The source type takes part
            # so that a node can gain a further one without a schema change.
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
            # A centre publishes one broker of any given kind, and the seed
            # keys a Global Broker on exactly that pair. Neither constraint
            # above reaches a Global Broker -- its node and its carrier are
            # both null -- so without this one a second Meteo-France row would
            # be accepted, and every notification stored twice. Only rows that
            # name a centre take part: a carried vantage point names none.
            models.UniqueConstraint(
                fields=["centre_id", "source_type"],
                condition=~models.Q(centre_id=""),
                name="unique_source_type_per_centre",
            ),
        ]
        verbose_name = _("Message Source")
        verbose_name_plural = _("Message Sources")

    def __str__(self):
        return f"{self.name} ({self.get_source_type_display()})"

    def clean(self):
        """A vantage point is nothing without the address it is reached at.

        Which address that is depends on what kind it is, and neither field
        can be required of every kind: a broker has no archive to read and an
        archive has no host to dial. Checked here rather than on the fields so
        that the admin refuses the one that is actually missing, instead of
        asking an operator correcting an archive's URL for a broker host that
        would mean nothing.
        """
        super().clean()

        if self.source_type == self.ORIGIN_API:
            if not self.api_url:
                raise ValidationError(
                    {
                        "api_url": _(
                            "An origin API needs the address of the archive it "
                            "is read from."
                        )
                    }
                )
        elif not self.host:
            raise ValidationError({"host": _("A broker needs a host to dial.")})

    @property
    def address(self):
        """Where this vantage point is reached, however it is reached.

        A listing that showed the host and port of every row would print a
        blank host and a port of 1883 against a centre's archive -- a broker
        address for something that is not a broker, and an invitation to
        correct a field that means nothing here. One column that says where
        the row actually points is the honest form of the same information.
        """
        if self.source_type == self.ORIGIN_API:
            return self.api_url

        return f"{self.host}:{self.port}"

    @property
    def owning_centre_id(self):
        """The centre this source belongs to, or an empty string.

        A Global Broker names its own centre; a centre's own vantage points
        take their centre from the node they belong to. Resolving that here
        keeps callers from walking the node relation and guessing which of the
        two applies.
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
        constraints = [
            # A centre names each of its datasets once, and that is the whole
            # key. The topic is deliberately not part of it: a wis2box makes
            # one dataset per station group and every one of them publishes on
            # the centre's single surface-based-observations topic, so a
            # centre sharing a topic between datasets is the ordinary case
            # rather than a catalogue error. Which dataset a message on such a
            # topic belongs to is settled by the message, not by the schema --
            # see ``RegistryLookup.dataset``.
            models.UniqueConstraint(
                fields=["node", "identifier"],
                name="unique_dataset_identifier_per_node",
            ),
        ]
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
            # The station rides along on the node's index rather than earning
            # one of its own. Everything asked of a node over a range of hours
            # -- what it published, which of its stations were heard, and one
            # named station's own hours -- filters on the node and the hour
            # first, so those stay the leading columns; carrying the station as
            # well means those answers are read off the index rather than
            # fetching every row to find out which station it was. A strict
            # extension of the plain ``(node, -hour)`` this replaced, so nothing
            # that used to walk that walks anything longer now.
            #
            # Nothing leads on the station. Reading one station across every
            # centre would want that, and a station transmitting under two
            # centres is a real finding -- but it is not a surface anything
            # asks for yet, and this table is never expired, so an index it
            # does not need is a cost it pays on every write for ever.
            models.Index(fields=["node", "-hour", "station"]),
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


#: What a daily station rollup counts separately. Named here for the same
#: reason ``ROLLUP_GRAIN`` is: the constraint that keeps a bucket unique and the
#: query that derives it live in different modules and have to agree.
DAILY_STATION_GRAIN = ("day", "source", "node", "station")


class DailyStationRollup(TimeStampedModel):
    """Which stations of a node were heard on one UTC day, and how much.

    A summary of :class:`HourlyRollup`, kept because the questions the
    statistics surfaces ask are station questions over long windows, and the
    hourly table is not shaped for them. Counting the distinct stations a node
    was heard from over ninety days means reading every hour of those days for
    every dataset the node publishes -- the dataset multiplies the rows and
    contributes nothing to the answer. Collapsing the day and dropping the
    dataset removes both multipliers at once.

    The dataset is dropped rather than kept because no station question is
    asked per dataset. The one place the breakdown is wanted -- a single
    station's drilldown -- is narrow enough to go back to the hourly rows,
    which now carry an index for exactly that.

    The source is kept, for the same reason the hourly grain keeps it: the same
    notification seen at a centre's own broker and at the Global Broker is two
    rows on purpose, and summing them would double every number. Reading is
    where a vantage point gets chosen -- distinct-station counts take all of
    them and let ``DISTINCT`` absorb the overlap, message volumes filter to the
    Global Broker.

    Derived from the hourly rollups rather than from the raw messages, which is
    what makes the history reachable at all: raw notifications expire after a
    fortnight, so a day older than that could never be computed a second time.
    Every day here is a pure function of hourly rows that are never expired, so
    any day can be rebuilt at any time and a run that was missed costs nothing
    but the delay.

    Messages carrying no known station keep their own bucket here, as they do
    in the hourly rollups. Whether the statistics surfaces say anything about
    them is undecided; dropping them at this layer would decide it by making
    the question unanswerable.

    Unpartitioned and never expired, like the hourly rollups it summarises,
    and so growing for ever on the same terms -- but an order of magnitude
    slower. A row is earned per station per day per vantage point, where the
    hourly table earns one per station per dataset per hour per vantage point,
    so a centre publishing a handful of datasets writes tens of hourly rows for
    every one here. Whether either table eventually wants partitioning or a
    horizon is a question about both of them together, and is not settled by
    adding this one.
    """

    day = models.DateTimeField(
        db_index=True,
        help_text=_("Start of the UTC day this count covers"),
    )
    source = models.ForeignKey(
        MessageSource,
        on_delete=models.CASCADE,
        related_name="daily_rollups",
        help_text=_("The vantage point these messages were observed from"),
    )
    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="daily_rollups",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="daily_rollups",
        help_text=_("Null for messages carrying no known station"),
    )

    message_count = models.PositiveIntegerField(default=0)
    # How much of the day, as against how loudly. A cell saying only "reported"
    # cannot tell a station sending once from one sending every hour, and which
    # of those a day was is most of what an availability matrix is read for.
    # Carried here because it costs nothing to derive -- the hours are already
    # the group -- and cannot be recovered from a message count afterwards.
    active_hours = models.PositiveSmallIntegerField(
        default=0,
        help_text=_("How many of the day's 24 UTC hours this station was heard in"),
    )

    class Meta:
        ordering = ["-day"]
        constraints = [
            # ``nulls_distinct=False`` for the same reason the hourly grain
            # needs it: a station of None is a real bucket, and Postgres's
            # default would let it be inserted twice over.
            models.UniqueConstraint(
                fields=DAILY_STATION_GRAIN,
                name="unique_daily_station_rollup",
                nulls_distinct=False,
            ),
        ]
        indexes = [
            # A node's whole station population over a range of days: the
            # availability matrix, and the count of distinct stations behind
            # every headline figure. Leading on the node and the day makes the
            # window a contiguous range, and carrying the station means settled
            # history is read off the index alone. The last few days are rebuilt
            # every quarter of an hour, so those pages are rarely all-visible
            # and are read from the table until a vacuum catches up -- which is
            # the smallest part of any window this exists to serve.
            models.Index(fields=["node", "-day", "station"]),
        ]
        verbose_name = _("Daily Station Rollup")
        verbose_name_plural = _("Daily Station Rollups")

    def __str__(self):
        return f"{self.node_id}/{self.station_id} @ {self.day}: {self.message_count}"


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


class StationActivityBaseline(models.Model):
    """How much of a day a station is normally heard in, learned from its own
    history.

    The availability matrix draws a station's day pale when it was heard in
    only a little of it. "A little" was a share of the clock's 24 hours until
    #112, and the clock is the wrong yardstick: a station reporting three-hourly
    is heard in 8 hours of every day and is perfectly well, while an hourly
    station down to 8 hours has lost two thirds of its output. Both are 8 of 24,
    and no threshold against the clock can tell them apart. Measured over six
    days of real traffic, two thirds of every pale cell on the tab was a station
    sitting at its own normal level.

    So a station is judged against itself, which is the answer
    ``CadenceBaseline`` above already gives for datasets, for the same reason
    and with the same machinery.

    **Node-scoped**, unlike the dataset baseline. A station may transmit under
    more than one centre's topics, and every figure on the statistics tab is
    one centre's own observation of it; a baseline pooled across centres would
    judge a centre against traffic it never received.

    The figure is a percentile of the station's own daily ``active_hours``,
    taken from ``DailyStationRollup`` because that is the table the day grain
    reads anyway. A percentile rather than a mean, which one dead day drags
    down, or a maximum, which is whatever its best day ever was and would call
    a station thin for any ordinary one.

    Written down rather than derived when asked for, because it is a scan of
    months of buckets for every station in the region -- far too much to run
    behind a page -- and because how much of a day a station reports in moves
    in weeks, not minutes.

    A baseline is never removed for want of history, on the same reasoning as
    the dataset one: a station that stops reporting eventually has too few days
    left in the window to learn from, and that is exactly when its baseline is
    the thing that reports it. ``learned_at`` is what says how old the answer
    is.
    """

    node = models.ForeignKey(
        WIS2Node,
        on_delete=models.CASCADE,
        related_name="station_activity_baselines",
    )
    station = models.ForeignKey(
        Station,
        on_delete=models.CASCADE,
        related_name="activity_baselines",
    )
    active_hours = models.FloatField(
        help_text=_("How many hours of a day this station is normally heard in"),
    )
    observations = models.PositiveIntegerField(
        help_text=_("How many observed days the figure was learned from"),
    )
    learned_at = models.DateTimeField(
        help_text=_("When the run that produced this figure read the history"),
    )

    class Meta:
        ordering = ["node", "station"]
        constraints = [
            models.UniqueConstraint(
                fields=["node", "station"],
                name="unique_station_activity_baseline_per_node",
            )
        ]
        verbose_name = _("Station Activity Baseline")
        verbose_name_plural = _("Station Activity Baselines")

    def __str__(self):
        return f"{self.station_id} at {self.node_id}: {self.active_hours}h a day"


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
        return self.filter(published_at__gte=evidence_horizon(now))

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
        return self.filter(published_at__lt=evidence_horizon(now))


def one_line(message, limit):
    """As much of one line of a message as a reader has any use for.

    Sources and databases fail in prose. A refused connection is a phrase, a
    constraint violation quotes the row it would not take, and a proxy
    answering with a page of HTML is a screenful -- and every one of them
    arrives where something is about to hold twenty of them side by side, on a
    row, in a sync log or in a morning's mail.

    Said here, beside the fields that hold the trimmed text, because the run
    that records a reason and the report that quotes one are in two layers
    that do not import each other, and two copies of a rule about how long a
    line may be is how they come to disagree about it.

    Args:
        message: whatever went wrong, as it was reported.
        limit: how many characters of it may be kept, ellipsis included.

    Returns:
        str: the message on one line, cut to the limit if it ran past it.
    """
    excerpt = " ".join(str(message).split())

    if len(excerpt) <= limit:
        return excerpt

    return excerpt[: limit - 1].rstrip() + "\u2026"


def evidence_horizon(now=None):
    """The instant before which a gap's evidence is no longer held.

    The raw retention cutoff itself rather than a horizon of its own: the two
    disagreeing would mean either reporting gaps nothing can check, or
    withholding ones something still can. It is named here all the same,
    because the report that bounds itself at this instant has to print it, and
    two callers working it out separately is how they come to differ.

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
    """One run of a synchronisation job, with its counts and what went wrong.

    Two kinds of thing go wrong in a run and the log holds both, because they
    are two errands. ``error_message`` is what stopped the run as a whole -- a
    refused connection, a catalogue that never stopped offering pages -- and is
    somebody's to chase at the source or at the network. ``stepped_over`` is
    the records the run read and could not store, which is a data problem in
    the region or a fault in how this tool reads it, and is the difference
    between a run that says it errored on nine records and one that says which
    nine and what refused them.
    """

    CATALOGUE = "catalogue"
    DISCOVERY_METADATA = "discovery_metadata"
    LINK_PROBES = "link_probes"
    MESSAGE_ARCHIVE = "message_archive"
    NODE_STATIONS = "node_stations"
    OSCAR_STATIONS = "oscar_stations"
    WILDCARD_SWEEP = "wildcard_sweep"

    SYNC_TYPE_CHOICES = [
        (CATALOGUE, _("Global Discovery Catalogue")),
        (DISCOVERY_METADATA, _("Discovery Metadata")),
        (LINK_PROBES, _("Canonical Link Probes")),
        (MESSAGE_ARCHIVE, _("Centre Message Archive")),
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

    stepped_over = models.JSONField(
        default=list,
        blank=True,
        help_text=_("Which items the run could not store, and what refused each one"),
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

    @property
    def reasons_withheld(self):
        """How many stepped-over items the run kept no reason for.

        A run that steps over more items than a sync log will hold reasons for
        records the first of them and counts the rest, and the two numbers
        disagreeing is how it says so. Ordinarily nought: a run stepping over
        more than a page of records is a fault in this tool rather than a list
        of records to chase, and the reasons it did keep say which fault. A run
        recorded before runs kept reasons at all withholds every one of them,
        which is honest -- it never knew.

        Said once here rather than by each surface that shows it. Two pages
        and a digest read this, and three of them working it out separately is
        how one of them comes to disagree about what a run lost.
        """
        return max(self.items_errored - len(self.stepped_over), 0)


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

    def standing(self, kind):
        """The open failure of one kind, or nothing where that kind is fine.

        At most one row can ever come back -- one open failure per kind is a
        database constraint -- which is what lets this read as a question
        rather than as a list. Asked wherever something has to know whether a
        failure is standing this minute: the reconciliation, which is bringing
        that row up to date; the announcing, which holds a message back while
        a likelier cause is already the news; and the reports, one of which is
        worth nothing while the registry it reads against is frozen.
        """
        return self.open().filter(kind=kind).first()

    def overlapping(self, start, end):
        """The spells that stood at any point between two instants.

        The half-open comparison is what makes a spell still open count: a row
        with no ``resolved_at`` is excluded by nothing, so it is carried into
        every window that begins before now. Asked wherever the question is
        how much of a stretch of time a failure occupied rather than whether
        one is standing.
        """
        return self.filter(started_at__lt=end).exclude(resolved_at__lt=start)


class HardFailure(models.Model):
    """A spell in which this tool itself stopped working.

    Everything else recorded here is a finding about the region. This is the
    other kind: the Global Broker connection lost, that connection proving
    unreliable, nothing at all being ingested, or the one catalogue that
    writes the registry having stopped answering. None of them says anything
    about whether African centres are publishing, and each means that nothing
    the tool goes on to say about them can be believed until it is fixed.

    The last of them fails in the opposite direction to the others, which is
    why it belongs here rather than reading as a quieter kind of finding. A
    broker that stops delivering empties the picture; a registry that stops
    being rebuilt freezes it, and every surface goes on answering confidently
    about the region as it stood when the catalogue was last reachable.

    A row per spell rather than per check, so that a failure lasting a day is
    one thing that happened rather than a thousand. ``notified_at`` is what
    keeps it to one message: a failure is announced once, if its check decides
    it is worth announcing at all, and again only when it clears.

    The kinds are not all announced, and that is the point of keeping them
    apart. ``GLOBAL_BROKER_LOST`` is written on every drop and mailed on none
    of them: a broker that drops for seven minutes every quarter of an hour is
    not sixty pieces of news, and these rows are the evidence rather than the
    story. ``GLOBAL_BROKER_UNRELIABLE`` is the story -- one spell covering the
    whole stretch in which those drops added up to the tool not really
    watching -- and it is read out of the rows beneath it. Which of them
    reaches anybody is :mod:`wis2watch.core.alerts`'s to say; what is recorded
    here is what happened, announced or not.

    No threshold is stored, on any kind. They are guesses about what counts as
    more than a blip, meant to be revised once the region's normal rhythms are
    known, and a row that recorded the guess it was opened under would have to
    be reconciled with the setting on every read.
    """

    GLOBAL_BROKER_LOST = "global_broker_lost"
    GLOBAL_BROKER_UNRELIABLE = "global_broker_unreliable"
    INGESTION_STALLED = "ingestion_stalled"
    CATALOGUE_WRITER_STALE = "catalogue_writer_stale"

    KIND_CHOICES = [
        (GLOBAL_BROKER_LOST, _("Global Broker connection lost")),
        (GLOBAL_BROKER_UNRELIABLE, _("Global Broker unreliable")),
        (INGESTION_STALLED, _("Ingestion stalled")),
        (CATALOGUE_WRITER_STALE, _("Registry catalogue not syncing")),
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

    @property
    def duration(self):
        """How long the failure stood, or has stood so far.

        Left as a timedelta rather than formatted, because the two readers
        want it differently: a listing column renders it, and the summaries
        the alerts compose add several of them together.
        """
        return (self.resolved_at or dj_timezone.now()) - self.started_at


class OutgoingEmail(models.Model):
    """One attempt to put something in front of whoever runs this installation.

    Everything else this tool records is a finding about the region. This is
    the record of the tool having spoken: what was said, who it was addressed
    to, when, and whether it got there. Nothing kept it before -- a digest was
    a template rendered, sent and discarded, and the only trace it left was
    the findings it marked as reported, which say what was carried without
    saying that anybody was carried it.

    A row per attempt rather than per delivery, because the two attempts that
    are not deliveries are the ones worth having. An installation that has
    never set ``WIS2WATCH_DIGEST_RECIPIENTS`` goes on finding everything it
    would have said and telling nobody, and today that leaves a warning in a
    log; a mail host refusing leaves an exception in a worker. An archive that
    went blank in exactly the cases where the operator was not told would read
    "no mail yesterday" for a quiet morning and for a week of silent failure
    alike, which is the one thing it must not do.

    The summary is stored rather than taken from the body, because the body
    cannot yield it. Every digest opens with the same sentence about what the
    digest is, so a preview cut from the front of one would be a preview of
    the boilerplate; what a run came to is known to whatever composed it, and
    is written down here instead of only into a log.

    Nothing expires. Everything this tool drops on a schedule is dropped
    because it grows with the region's traffic, which nobody controls. This
    grows with the events an operator is told about -- one digest a day at
    most, and an alert per outage rather than per check -- so a retention
    setting here would be a control whose only possible effect is to destroy
    the record it was built to keep, quietly, on a timer. For the same reason
    the admin refuses every write: a row that can be edited is a record of
    what somebody is prepared to say was sent.
    """

    DAILY_DIGEST = "daily_digest"
    HARD_FAILURE = "hard_failure"

    KIND_CHOICES = [
        (DAILY_DIGEST, _("Daily digest")),
        (HARD_FAILURE, _("Hard failure alert")),
    ]

    SENT = "sent"
    NO_RECIPIENTS = "no_recipients"
    FAILED = "failed"

    STATUS_CHOICES = [
        (SENT, _("Sent")),
        (NO_RECIPIENTS, _("Nobody to send it to")),
        (FAILED, _("Failed")),
    ]

    kind = models.CharField(
        max_length=50,
        choices=KIND_CHOICES,
        help_text=_("Which of the things this tool sends this message was"),
    )

    subject = models.CharField(
        max_length=500,
        help_text=_("The subject as it was sent, prefix and all"),
    )
    summary = models.CharField(
        max_length=1000,
        blank=True,
        help_text=_(
            "What the message was about, in one line, from whatever composed it"
        ),
    )
    body = models.TextField(
        blank=True,
        help_text=_("The message itself, as it was rendered"),
    )
    recipients = ArrayField(
        models.EmailField(max_length=254),
        default=list,
        blank=True,
        help_text=_("Who it was addressed to, as configured at the time"),
    )

    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        help_text=_("How the attempt ended"),
    )
    error_message = models.TextField(
        blank=True,
        help_text=_("Why it did not get there, where it did not"),
    )

    attempted_at = models.DateTimeField(
        default=dj_timezone.now,
        help_text=_("When the message was composed and handed to the mail backend"),
    )

    class Meta:
        ordering = ["-attempted_at"]
        indexes = [
            models.Index(fields=["kind", "-attempted_at"]),
        ]
        verbose_name = _("Outgoing Email")
        verbose_name_plural = _("Outgoing Email")

    def __str__(self):
        return f"{self.subject} ({self.get_status_display()})"

    @property
    def recipient_list(self):
        """Who it was addressed to, for a column in a table.

        Empty where nobody was configured to receive it, which is the whole
        content of that row: the message was composed and had nowhere to go.
        """
        return ", ".join(self.recipients)
