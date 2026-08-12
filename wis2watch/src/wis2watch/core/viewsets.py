from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.admin.widgets import ListingButton
from wagtail.permission_policies.base import ModelPermissionPolicy
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import Dataset, GlobalDiscoveryCatalogue, MessageSource, WIS2Node


class SyncManagedPermissionPolicy(ModelPermissionPolicy):
    """What a person may do to records a sync owns: edit them, nothing more.

    A dataset exists because a catalogue described it. Creating one by hand
    would produce a record with no discovery metadata behind it, and deleting
    one would take its history off the rollups that counted it -- while the
    next sync put it straight back. Refusing both here is what keeps the admin
    from offering an action that cannot end well.
    """

    def user_has_permission(self, user, action):
        if action in {"add", "delete"}:
            return False

        return super().user_has_permission(user, action)


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
    list_display = ["centre_id", "name", "country", "status"]


class MessageSourceIndexView(generic.IndexView):
    """The broker list, showing what is actually connected to.

    A Global Cache pickup is a vantage point read off a Global Broker
    connection's ``cache/`` topics rather than a broker of its own, so it has
    no address to correct, no credential to set and nothing that deactivating
    it would stop. Listed here it would offer an operator three controls that
    do nothing. What it is carrying is on the node overview instead, per
    centre, which is the question anyone actually has about it.
    """

    def get_base_queryset(self):
        return super().get_base_queryset().connections()


class MessageSourceViewSet(ModelViewSet):
    model = MessageSource
    index_view_class = MessageSourceIndexView
    base_url_path = "message-sources"
    icon = "site"
    menu_label = "Brokers"
    add_to_admin_menu = True
    menu_order = 110
    list_display = ["name", "source_type", "host", "port", "is_active"]


class GlobalDiscoveryCatalogueViewSet(ModelViewSet):
    model = GlobalDiscoveryCatalogue
    base_url_path = "catalogues"
    icon = "list-ul"
    menu_label = "Catalogues"
    add_to_admin_menu = True
    menu_order = 120
    list_display = ["name", "centre_id", "is_writer", "is_active"]


class DatasetViewSet(SnippetViewSet):
    """The datasets the catalogue knows, so an expectation can be set by hand.

    This is where a learned cadence gets corrected and where a dataset with
    too little history to learn from gets told what to expect -- the one thing
    about a sync-managed record that is a person's to say.
    """

    model = Dataset
    base_url_path = "datasets"
    icon = "list-ul"
    menu_label = "Datasets"
    add_to_admin_menu = True
    menu_order = 130
    list_display = ["title", "node", "status", "expected_interval_override_hours"]
    list_filter = ["status", "node"]
    search_fields = ["title", "identifier", "wmo_topic_hierarchy"]

    @property
    def permission_policy(self):
        return SyncManagedPermissionPolicy(self.model)


admin_viewsets = [
    WIS2NodeViewSet(),
    MessageSourceViewSet(),
    GlobalDiscoveryCatalogueViewSet(),
]
