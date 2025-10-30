from django.shortcuts import render
from django.urls import reverse
from wagtail.api.v2.utils import get_full_url


def mqtt_monitor_map(request):
    """Map view for MQTT monitoring"""
    
    context = {
        "nodes_api_url": get_full_url(request, reverse("mqtt_nodes_api")),
    }
    
    return render(request, 'wis2watchmonitoring/mqtt_monitor_map.html', context)
