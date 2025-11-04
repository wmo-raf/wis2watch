from wagtail.admin.viewsets.model import ModelViewSet

from .models import WIS2Node


class WIS2NodeViewSet(ModelViewSet):
    model = WIS2Node
    base_url_path = "nodes"
    icon = "circle-nodes"
    menu_label = "Nodes"
    add_to_admin_menu = True
    menu_order = 100


admin_viewsets = [
    WIS2NodeViewSet(),
]
