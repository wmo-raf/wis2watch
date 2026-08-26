"""What the admin home shows on login.

Wagtail's dashboard is a list of panels other people's code contributes to,
and this is ours. It is a module of its own rather than a class in
``wagtail_hooks``, which stays what it has always been here: wiring, and
nothing that has to be read to understand a page.
"""

from django.urls import reverse_lazy
from wagtail.admin.ui.components import Component

from .analysis import GAP_REPORTS


class NodeOverviewPanel(Component):
    """Every centre of the region, worst first, above everything else.

    The reason somebody logs in. It sits at the top because a health table
    below four panels about a page tree is a health table nobody scrolls to,
    and because the question it answers -- is anything wrong right now -- is
    the one the reader already has in their head when the page paints.

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
