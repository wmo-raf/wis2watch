from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.admin.widgets import ListingButton

from .models import WIS2Node


class WIS2NodeIndexView(generic.IndexView):
    def get_list_more_buttons(self, instance):
        buttons = super().get_list_more_buttons(instance)
        
        label = _("Details")
        url = reverse("node_details", args=[instance.id])
        icon_name = "list-ul"
        attrs = {}
        if label and url:
            buttons.append(
                ListingButton(
                    label,
                    url=url,
                    icon_name=icon_name,
                    attrs=attrs,
                )
            )
        
        return buttons


class WIS2NodeViewSet(ModelViewSet):
    model = WIS2Node
    base_url_path = "nodes"
    icon = "circle-nodes"
    menu_label = "Nodes"
    add_to_admin_menu = True
    menu_order = 100
    index_view_class = WIS2NodeIndexView


admin_viewsets = [
    WIS2NodeViewSet(),
]
