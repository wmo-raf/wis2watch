"""Who may ask the API what an admin page shows.

Everything these endpoints return is a view of the monitoring: which centres
are watched, where they are, which of their stations have stopped. All of it
is already behind the Wagtail admin, and none of it is public -- so the API in
front of it has to be behind the same door rather than merely behind a
password.
"""

from rest_framework.permissions import BasePermission

#: Wagtail's own "may open the admin at all" permission. Checked rather than
#: ``is_staff``, because Wagtail grants admin access through groups and a
#: reader who was given a group is not necessarily marked staff.
ADMIN_ACCESS = "wagtailadmin.access_admin"


class HasAdminAccess(BasePermission):
    """Whether the reader is one the admin would have let in."""

    message = "You do not have access to the monitoring admin."

    def has_permission(self, request, view):
        """Whether this request may be answered at all.

        Args:
            request: the request being judged.
            view: the view it was made to.

        Returns:
            bool: True where the reader could have opened the admin.
        """
        user = request.user

        return bool(user and user.is_authenticated and user.has_perm(ADMIN_ACCESS))
