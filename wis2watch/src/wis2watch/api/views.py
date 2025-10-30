from rest_framework.decorators import api_view, permission_classes
from rest_framework.permissions import IsAuthenticated
from rest_framework.response import Response

from wis2watch.core.models import WIS2Node


@api_view()
@permission_classes([IsAuthenticated])
def mqtt_nodes_api(request):
    """API endpoint to get all nodes with their details including country center points"""
    nodes = WIS2Node.objects.all()
    
    nodes_list = []
    for node in nodes:
        center_point = node.country_center_point
        
        nodes_list.append({
            'id': node.id,
            'name': node.name,
            'country': node.country.name,
            'country_code': node.country.code,
            'centre_id': node.centre_id,
            'status': node.status,
            'mqtt_host': node.mqtt_host,
            'mqtt_port': node.mqtt_port,
            'center_point': center_point,
        })
    
    return Response(nodes_list)
