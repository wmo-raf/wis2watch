from django.contrib.gis.db import models
from django.contrib.gis.geos import Polygon
from django.db.models.signals import post_save
from django.dispatch import receiver
from django.utils import timezone as dj_timezone
from django.utils.translation import gettext_lazy as _
from django_countries.fields import CountryField
from django_countries.widgets import CountrySelectWidget
from django_extensions.db.models import TimeStampedModel
from timescale.db.models.models import TimescaleModel
from wagtail.admin.panels import FieldPanel, MultiFieldPanel
from wagtail.snippets.models import register_snippet


class WIS2Node(TimeStampedModel):
    """
    Represents a WIS2 node instance
    """
    NODE_TYPE_CHOICES = [
        ('wis2box', 'WIS2Box'),
        ('other', 'Other Software'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('error', 'Error'),
    ]
    
    name = models.CharField(
        max_length=200,
        help_text="Friendly name for this node"
    )
    country = CountryField(blank_label=_("Select Country"), verbose_name=_("Country"))
    node_type = models.CharField(
        max_length=20,
        choices=NODE_TYPE_CHOICES,
        default='wis2box'
    )
    
    base_url = models.URLField(
        max_length=500,
        help_text="Base URL of the node"
    )
    
    # Discovery metadata endpoint
    discovery_metadata_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Custom URL for discovery metadata. Auto-generated for wis2box."
    )
    
    # Stations endpoint
    stations_url = models.URLField(
        max_length=500,
        blank=True,
        help_text="Custom URL for stations list. Auto-generated for wis2box."
    )
    
    verify_ssl = models.BooleanField(
        default=True,
        help_text="Verify SSL certificates when connecting to the node"
    )
    
    # MQTT Configuration
    mqtt_host = models.CharField(max_length=255, blank=True)
    mqtt_port = models.IntegerField(default=1883)
    mqtt_username = models.CharField(max_length=100, blank=True)
    mqtt_password = models.CharField(max_length=255, blank=True)
    mqtt_use_tls = models.BooleanField(default=False)
    
    # Status tracking
    status = models.CharField(
        max_length=20,
        choices=STATUS_CHOICES,
        default='active'
    )
    
    last_check = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    
    # Metadata
    centre_id = models.CharField(
        max_length=100,
        blank=True,
        help_text="WMO centre ID"
    )
    
    @property
    def country_center_point(self):
        """Returns the geographic center point of the country"""
        country = self.country
        geo_extent = country.geo_extent
        if geo_extent:
            centroid = Polygon.from_bbox(geo_extent).centroid
            return [centroid.x, centroid.y]
        return None
    
    panels = [
        FieldPanel('name'),
        FieldPanel("country", widget=CountrySelectWidget()),
        FieldPanel('node_type'),
        FieldPanel('base_url'),
        FieldPanel('centre_id'),
        
        MultiFieldPanel([
            FieldPanel('discovery_metadata_url'),
            FieldPanel('stations_url'),
        ], heading="API Endpoints"),
        
        FieldPanel('verify_ssl'),
        
        MultiFieldPanel([
            FieldPanel('mqtt_host'),
            FieldPanel('mqtt_port'),
            FieldPanel('mqtt_username'),
            FieldPanel('mqtt_password'),
            FieldPanel('mqtt_use_tls'),
        ], heading="MQTT Broker Configuration"),
    ]
    
    class Meta:
        ordering = ['country', 'name']
        unique_together = ['country', 'centre_id']
        verbose_name = 'WIS2 Node'
        verbose_name_plural = 'WIS2 Nodes'
    
    def __str__(self):
        return f"{self.name} ({self.country})"
    
    def save(self, *args, **kwargs):
        
        """Auto-generate URLs for wis2box nodes."""
        if self.node_type == 'wis2box':
            if not self.discovery_metadata_url:
                self.discovery_metadata_url = (
                    f"{self.base_url}/oapi/collections/discovery-metadata/items?f=json"
                )
            if not self.stations_url:
                self.stations_url = (
                    f"{self.base_url}/oapi/collections/stations/items?f=json"
                )
        super().save(*args, **kwargs)
    
    def get_topics(self):
        datasets = self.datasets.filter(status='active')
        topics = [dataset.wmo_topic_hierarchy for dataset in datasets]
        return topics


@receiver(post_save, sender=WIS2Node)
def sync_node_metadata(sender, instance, created, **kwargs):
    """
    Signal to trigger metadata synchronization when a WIS2Node is created or updated.
    """
    from wis2watch.core.tasks import run_sync_metadata
    run_sync_metadata.delay(instance.id)


@register_snippet
class Dataset(TimeStampedModel):
    """
    Represents a discovery metadata item/dataset from a WIS2 node.
    """
    
    DATA_POLICY_CHOICES = [
        ('core', 'Core'),
        ('recommended', 'Recommended'),
    ]
    
    STATUS_CHOICES = [
        ('active', 'Active'),
        ('inactive', 'Inactive'),
        ('deleted', 'Deleted'),
    ]
    
    node = models.ForeignKey(WIS2Node, on_delete=models.CASCADE, related_name='datasets')
    identifier = models.CharField(max_length=500, unique=True, help_text="URN identifier of the dataset")
    title = models.CharField(max_length=500)
    wmo_data_policy = models.CharField(max_length=20, choices=DATA_POLICY_CHOICES)
    wmo_topic_hierarchy = models.CharField(unique=True, max_length=500,
                                           help_text="MQTT topic hierarchy for this dataset")
    self_link = models.URLField(max_length=1000, blank=True)
    collection_link = models.URLField(max_length=1000, blank=True)
    raw_json = models.JSONField(help_text="Complete raw JSON from discovery metadata")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='active')
    metadata_created = models.DateTimeField(null=True, blank=True, help_text="Created timestamp from metadata")
    metadata_updated = models.DateTimeField(null=True, blank=True, help_text="Updated timestamp from metadata")
    last_synced = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['node', 'title']
        indexes = [
            models.Index(fields=['identifier']),
            models.Index(fields=['wmo_topic_hierarchy']),
            models.Index(fields=['status']),
        ]
    
    def __str__(self):
        return f"{self.title} ({self.identifier})"


@register_snippet
class Station(TimeStampedModel):
    """
    Represents a weather station that publishes data.
    """
    FACILITY_TYPE_CHOICES = [
        ('landFixed', 'Land Fixed'),
        ('landMobile', 'Land Mobile'),
        ('sea', 'Sea'),
        ('airFixed', 'Air Fixed'),
        ('airMobile', 'Air Mobile'),
    ]
    
    wigos_id = models.CharField(max_length=100, unique=True, help_text="WIGOS Identifier of the station")
    name = models.CharField(max_length=200)
    location = models.PointField(help_text="Location of the station", dim=3)
    datasets = models.ManyToManyField(Dataset, related_name='stations')
    facility_type = models.CharField(max_length=20, choices=FACILITY_TYPE_CHOICES, default='landFixed')
    raw_json = models.JSONField(help_text="Complete raw JSON from stations endpoint")
    last_synced = models.DateTimeField(null=True, blank=True)
    
    class Meta:
        ordering = ['name']
        indexes = [
            models.Index(fields=['wigos_id']),
        ]
    
    def __str__(self):
        return f"{self.name} ({self.wigos_id})"


@register_snippet
class StationMQTTMessageLog(TimescaleModel, TimeStampedModel, ):
    """
    Time-series model for storing MQTT messages/observations from stations.
    """
    station = models.ForeignKey(Station, on_delete=models.CASCADE, related_name='observations')
    dataset = models.ForeignKey(Dataset, on_delete=models.CASCADE, related_name='observations')
    message_id = models.CharField(max_length=255, help_text="UUID from the message")
    data_id = models.CharField(max_length=500, help_text="Data ID from message properties")
    publish_datetime = models.DateTimeField(db_index=True, help_text="When message was published")
    received_datetime = models.DateTimeField(default=dj_timezone.now, help_text="When we received the message")
    canonical_link = models.URLField(max_length=1000, blank=True)
    raw_json = models.JSONField(help_text="Complete raw MQTT message")
    
    def __str__(self):
        return f"{self.station.name} - {self.time}"


@register_snippet
class SyncLog(models.Model):
    """
    Tracks synchronization attempts for datasets and stations.
    """
    SYNC_TYPE_CHOICES = [
        ('discovery_metadata', 'Discovery Metadata'),
        ('stations', 'Stations'),
    ]
    
    STATUS_CHOICES = [
        ('success', 'Success'),
        ('partial', 'Partial Success'),
        ('failed', 'Failed'),
    ]
    
    node = models.ForeignKey(WIS2Node, on_delete=models.CASCADE, related_name='sync_logs')
    sync_type = models.CharField(max_length=50, choices=SYNC_TYPE_CHOICES)
    status = models.CharField(max_length=20, choices=STATUS_CHOICES)
    
    # Statistics
    items_found = models.IntegerField(default=0)
    items_created = models.IntegerField(default=0)
    items_updated = models.IntegerField(default=0)
    items_deleted = models.IntegerField(default=0)
    
    # Error tracking
    error_message = models.TextField(blank=True)
    
    # Timing
    started_at = models.DateTimeField(default=dj_timezone.now)
    completed_at = models.DateTimeField(null=True, blank=True)
    
    created_at = models.DateTimeField(auto_now_add=True)
    
    class Meta:
        ordering = ['-started_at']
        indexes = [
            models.Index(fields=['node', '-started_at']),
            models.Index(fields=['sync_type', '-started_at']),
        ]
    
    def __str__(self):
        return f"{self.node.name} - {self.sync_type} - {self.status} ({self.started_at})"
