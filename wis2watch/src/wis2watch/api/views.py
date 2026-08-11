from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from wis2watch.core.models import WIS2Node


@api_view()
@permission_classes([IsAuthenticated])
def mqtt_nodes_api(request):
    """API endpoint to get all nodes with their details including country center points"""
    nodes = WIS2Node.objects.prefetch_related("message_sources").all()

    nodes_list = []
    for node in nodes:
        center_point = node.country_center_point
        origin_source = node.origin_source

        nodes_list.append({
            'id': node.id,
            'name': node.name,
            'country': node.country.name if node.country else '',
            'country_code': node.country.code if node.country else '',
            'centre_id': node.centre_id,
            'status': node.status,
            'mqtt_host': origin_source.host if origin_source else '',
            'mqtt_port': origin_source.port if origin_source else None,
            'center_point': center_point,
        })

    return Response(nodes_list)
