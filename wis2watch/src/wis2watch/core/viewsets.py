from django.urls import reverse
from django.utils.translation import gettext_lazy as _
from wagtail.admin.ui.tables import Column
from wagtail.admin.views import generic
from wagtail.admin.viewsets.model import ModelViewSet
from wagtail.admin.widgets import ListingButton
from wagtail.permission_policies.base import ModelPermissionPolicy
from wagtail.snippets.views.snippets import SnippetViewSet

from .models import (
    Dataset,
    GlobalDiscoveryCatalogue,
    HardFailure,
    MessageSource,
    OutgoingEmail,
    WIS2Node,
)


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


class ReadOnlyPermissionPolicy(ModelPermissionPolicy):
    """What a person may do to a record of something that already happened.

    Nothing. A row saying a message was sent on a morning is worth exactly as
    much as it cannot be changed afterwards, and deleting one is the retention
    policy this archive deliberately does not have, with a person's hand on it
    instead of a timer. Viewing is left, which is what the inspect view needs.
    """

    def user_has_permission(self, user, action):
        if action in {"add", "change", "delete"}:
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
    """The vantage points that carry an address of their own.

    A Global Cache pickup is read off a Global Broker connection's ``cache/``
    topics rather than reached at an address, so it has nothing to correct, no
    credential to set and nothing that deactivating it would stop. Listed here
    it would offer an operator three controls that do nothing. What it is
    carrying is on the node overview instead, per centre, which is the
    question anyone actually has about it.

    A centre's own message archive is listed, though nothing dials it: its
    address is a guess that only an operator can correct, and this is where
    they correct it.
    """

    def get_base_queryset(self):
        return super().get_base_queryset().connections()


class MessageSourceViewSet(ModelViewSet):
    """Where every vantage point's address is set, brokers and archives alike.

    Listed by address rather than by host and port, because the two kinds are
    not reached the same way and a column of ports would be inventing one for
    the kind that has none.
    """

    model = MessageSource
    index_view_class = MessageSourceIndexView
    base_url_path = "message-sources"
    icon = "site"
    menu_label = "Message sources"
    add_to_admin_menu = True
    menu_order = 110
    list_display = ["name", "source_type", "address", "is_active"]


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


class OutgoingEmailViewSet(ModelViewSet):
    """Everything this tool has tried to tell whoever runs it.

    Listed by subject first, because that is what a message is remembered as
    and because the first column is the one that opens the message itself. The
    summary beside it is the preview -- what the run came to, from whatever
    composed it -- and the recipients are the ones the message actually went
    to rather than the ones configured now, which is the difference the
    archive exists to keep.

    Read-only throughout, and the whole table is reachable in one click from
    the menu: the question it answers -- was I told, and what was I told -- is
    one somebody arrives with, and a page they had to already suspect the
    existence of would not answer it.
    """

    model = OutgoingEmail
    base_url_path = "outgoing-email"
    icon = "mail"
    menu_label = "Outgoing email"
    add_to_admin_menu = True
    menu_order = 140
    inspect_view_enabled = True
    # Wagtail builds the add and edit views whether or not anybody may reach
    # them, and refuses to start without being told what their form holds.
    # Nothing here is a person's to set, so the answer is only ever read by
    # the check that insists on asking.
    exclude_form_fields = []
    list_display = [
        "subject",
        Column("kind", label=_("Kind"), accessor="get_kind_display", sort_key="kind"),
        "summary",
        Column("recipients", label=_("Recipients"), accessor="recipient_list"),
        Column(
            "status",
            label=_("Status"),
            accessor="get_status_display",
            sort_key="status",
        ),
        Column(
            "attempted_at", label=_("Attempted at"), sort_key="attempted_at"
        ),
    ]
    list_filter = ["kind", "status"]
    search_fields = ["subject", "summary", "body"]

    @property
    def permission_policy(self):
        return ReadOnlyPermissionPolicy(self.model)


class HardFailureViewSet(ModelViewSet):
    """Every spell in which this tool could not answer for the region.

    The record of its own blindness, and the only page that can settle the
    question a reader arrives with after a bad week: was the region quiet, or
    was nobody listening? Most of what is here was never mailed to anybody and
    was never meant to be -- a connection that drops sixty times in a day is
    sixty rows and one alert -- so a page is the only place those rows are
    visible at all.

    Listed by kind and by when the spell began, with how long it lasted beside
    it, because the question is nearly always about a stretch of time rather
    than about one row. Whether anybody was told is a column of its own: an
    unannounced spell is the ordinary case here, and a reader who did not know
    that would take the blanks for a mail failure.

    Read-only for the same reason the outgoing email archive is. A record of
    when this tool was not working is worth what it cannot be tidied up to
    say, and the rows are the evidence the alerts are computed from -- editing
    one would not correct the past, it would change what the next check
    believes about it.
    """

    model = HardFailure
    base_url_path = "hard-failure"
    icon = "warning"
    menu_label = "Hard failures"
    add_to_admin_menu = True
    menu_order = 150
    inspect_view_enabled = True
    # Wagtail builds the add and edit views whether or not anybody may reach
    # them, and refuses to start without being told what their form holds.
    exclude_form_fields = []
    list_display = [
        Column("kind", label=_("Kind"), accessor="get_kind_display", sort_key="kind"),
        Column("started_at", label=_("Began"), sort_key="started_at"),
        Column("duration", label=_("Lasted")),
        Column("resolved_at", label=_("Cleared"), sort_key="resolved_at"),
        Column("notified_at", label=_("Announced"), sort_key="notified_at"),
        "detail",
    ]
    list_filter = ["kind"]
    search_fields = ["detail"]

    @property
    def permission_policy(self):
        return ReadOnlyPermissionPolicy(self.model)


admin_viewsets = [
    WIS2NodeViewSet(),
    MessageSourceViewSet(),
    GlobalDiscoveryCatalogueViewSet(),
    OutgoingEmailViewSet(),
    HardFailureViewSet(),
]
