from django.shortcuts import render
from django.urls import reverse
from wagtail.api.v2.utils import get_full_url


def ingest_monitor_map(request):
    """The map of what the ingestion process is seeing."""

    context = {
        "nodes_api_url": get_full_url(request, reverse("nodes_api")),
    }

    return render(request, 'wis2watchmonitoring/ingest_monitor_map.html', context)
