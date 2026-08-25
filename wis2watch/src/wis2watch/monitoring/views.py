from django.shortcuts import render


def ingest_monitor_map(request):
    """The map of what the ingestion process is seeing."""

    return render(request, 'wis2watchmonitoring/ingest_monitor_map.html')
