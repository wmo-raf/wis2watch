"""What the admin home shows on login.

Two kinds of thing, and they answer two different questions. The strip of
counts in the page header says how big the region is; the panel below it says
how the region is doing. Both live here because both are the same decision --
what the admin home is for -- and splitting them across two modules would let
that decision be made twice.

A module of its own rather than classes in ``wagtail_hooks``, which stays what
it has always been here: wiring, and nothing that has to be read to understand
a page.
"""

from django.urls import reverse, reverse_lazy
from django.utils.functional import cached_property
from django.utils.translation import ngettext_lazy
from wagtail.admin.site_summary import SummaryItem
from wagtail.admin.ui.components import Component

from .analysis import GAP_REPORTS
from .models import Dataset, Station
from .viewsets import wis2node_viewset


class ScopeSummaryItem(SummaryItem):
    """One tile in the header strip: how much of a thing is under watch.

    The strip answers the question the panel below it assumes and never
    states. A reader looking at seven silent rows cannot tell from the table
    alone whether that is seven centres out of nine or seven out of ninety,
    and the size of the region is not written anywhere else on the page.

    **Scope, not health.** Nothing here is a count of what is wrong. That is
    the panel's job, and a headline number over its own table would be a
    second verdict on the same question, free to disagree with it. These count
    what exists, which the panel never says.

    **A tile counts exactly what its page lists.** Every tile is a route, so
    its number is its destination's row count by construction: it asks the
    viewset that renders that listing, for the URL, for the count and for
    whether this reader may open it. A tile reading 1,204 above a page showing
    1,190 is the classic failure of a summary strip, and asking one object
    three questions is what forecloses it. The admin tests assert it against
    each listing's own paginator, which is what keeps it foreclosed when
    somebody narrows a listing later.

    Subclasses supply the viewset, an icon and a noun. The markup is written
    once, below, for the reason ``includes/all_nodes_panel.html`` already
    gives: two copies of a thing is how one of them drifts.
    """

    template_name = "wis2watchcore/home/scope_summary_item.html"

    #: The name of the listing route on the viewset. Model viewsets call it
    #: ``index`` and snippet viewsets call it ``list``, so it is named per tile
    #: rather than assumed.
    url_name = "index"

    #: Registered icon name. The tile carries its menu entry's icon, so the
    #: two read as the same thing rather than as two routes to one page.
    icon = None

    #: Singular and plural of the noun, picked by ``ngettext_lazy`` against the
    #: count. The number is rendered separately, by the template, so that it
    #: can be given its thousands separator -- which costs a translator the
    #: ability to reorder the two. Accepted: the alternative was a translatable
    #: string per tile, and a template per tile to hold it.
    label = None

    @cached_property
    def viewset(self):
        """The viewset that renders this tile's destination."""
        raise NotImplementedError

    def get_context_data(self, parent_context):
        """The count, the noun for it, and where the tile goes.

        Args:
            parent_context: the admin home's own context.

        Returns:
            dict: icon name, pluralised label, total and destination URL.
        """
        total = self.viewset.model.objects.count()

        return {
            "icon": self.icon,
            "label": self.label % total,
            "total": total,
            "link": reverse(self.viewset.get_url_name(self.url_name)),
        }

    def is_shown(self):
        """Whether this reader may open the page the tile leads to.

        Wagtail's own ``PagesSummaryItem`` gates itself the same way, and for
        the same reason: a tile is a link, and a link to a 403 is worse than
        no link. Asked of the viewset's policy rather than of a permission
        named here, so a tile cannot come to disagree with its own listing
        about who is allowed in.
        """
        return self.viewset.permission_policy.user_has_any_permission(
            self.request.user, ["add", "change", "delete", "view"]
        )


class NodesSummaryItem(ScopeSummaryItem):
    """How many publishing centres are registered.

    First, because it is the denominator for everything below it and for the
    panel beneath. Called "Nodes" rather than "Centres" -- which is the word
    most of this codebase's prose uses -- because the menu entry and the page
    it opens both say Nodes, and a tile that renames its destination makes a
    reader check whether they landed in the right place.

    Knowably incomplete, and deliberately so: a centre publishing that no
    catalogue has indexed is not in this number, which is what the unregistered
    centres gap report exists to say. The strip is not the surface for that
    caveat -- it counts what is watched, and a centre nobody registered is not
    being watched.
    """

    order = 100
    icon = "circle-nodes"
    label = ngettext_lazy("Node", "Nodes")

    @cached_property
    def viewset(self):
        return wis2node_viewset


class DatasetsSummaryItem(ScopeSummaryItem):
    """How many datasets those centres between them claim to publish.

    The unit the expectations are actually set on: silence is judged per
    dataset, not per centre, so this is the count of things that can go quiet.

    Carries ``list-ul`` because ``DatasetViewSet`` does, and generic though it
    is -- the Overview menu item and the catalogues listing wear it too --
    matching the menu matters more here than being distinctive.
    """

    order = 200
    icon = "list-ul"
    label = ngettext_lazy("Dataset", "Datasets")
    url_name = "list"

    @cached_property
    def viewset(self):
        return Dataset.snippet_viewset


class StationsSummaryItem(ScopeSummaryItem):
    """How many stations the region declares or has been heard from.

    Earns its place twice. It is the scope of the finest-grained thing this
    tool watches, and it is the only route to the station listing at all: the
    station is a snippet, and ``construct_main_menu`` hides the snippets menu,
    so without this tile the page is reachable only by typing its URL. It is
    the same trap ``DatasetViewSet`` was given a menu entry to escape.

    One number over three kinds of declaration -- OSCAR's, a node's own
    registry's, and traffic observed with nobody having declared it -- because
    the station record is one record however many sources named it. Which
    sources named which station is the drilldown's question, not the strip's.
    """

    order = 300
    icon = "map-pin"
    label = ngettext_lazy("Station", "Stations")
    url_name = "list"

    @cached_property
    def viewset(self):
        return Station.snippet_viewset


class TransmissionStatusPanel(Component):
    """Whether each centre's data is flowing, worst first, above everything else.

    The reason somebody logs in. It sits at the top because a health table
    below four panels about a page tree is a health table nobody scrolls to,
    and because the question it answers is the one the reader already has in
    their head when the page paints.

    **One question, and only one.** Whether a centre's own broker answers, and
    whether the Global Caches carried what it published, are both true and
    neither is what this panel is for: the first is how the tool is watching
    the centre, the second is what happened downstream after it published.
    Folding them in put twenty-one of thirty-two centres under "Archive only"
    and left exactly one row reading healthy -- on a panel whose job is to say
    whether data is flowing. It draws ``TransmissionStanding`` instead, and the
    plumbing is on the overview page, which is the page somebody opens to ask
    what is wrong rather than whether anything is.

    **A mount point and nothing else.** The rows arrive from
    ``/api/nodes/statistics/`` after the page has rendered, so a login never
    waits on the region's query. What the panel renders itself is what has to
    survive that fetch failing: the heading, so the reader knows something is
    meant to be here, and the gap reports, which are static links and are
    exactly what somebody needs when the table above them will not load.

    ``order`` is 100, which puts it above Wagtail's own panels -- though the
    hook that installs this removes those, so the number is what would happen
    rather than what does. It is set anyway: a panel whose position depends on
    nothing else being registered is a panel that moves the first time an app
    is added.
    """

    order = 100
    template_name = "wis2watchcore/panels/all_nodes.html"

    def get_context_data(self, parent_context):
        """What the panel's template needs to render its frame.

        Args:
            parent_context: the admin home's own context.

        Returns:
            dict: the mount point's URL, and the reports named beside it.
        """
        return {
            "statistics_url": reverse_lazy("nodes_statistics"),
            # Named here as well as on their own index and on the overview
            # page, for the reason that page already gives: a report nobody
            # arrives at reports nothing. This panel is now what somebody has
            # open when they start wondering what else is wrong -- and it has
            # a blind spot by construction, since it lists every *registered*
            # centre and a centre nobody registered is invisible on it. These
            # are the only thing that says so.
            "gap_reports": GAP_REPORTS,
            "gap_reports_url": reverse_lazy("gap_reports"),
        }
