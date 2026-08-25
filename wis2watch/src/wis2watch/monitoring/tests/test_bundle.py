"""The built Vue bundles, against the tree they were built from.

The islands are committed rather than built on deploy, which buys a stack
that needs no node to run and costs a way for a page to be served by code
nobody can find in the tree. Nothing here rebuilds anything -- that needs a
toolchain a test has no business assuming. What it checks is the handful of
things a stale or half-committed bundle gets wrong: an entry with no output,
an output no entry names, and the one path the bundle still spells out for
itself.
"""

import re
from pathlib import Path

from django.test import SimpleTestCase

from wis2watch.ws.routing import websocket_urlpatterns

MONITORING = Path(__file__).resolve().parent.parent

#: Where `npm run build` writes, and what is committed.
BUILT = MONITORING / "static" / "vue"

VITE_CONFIG = MONITORING / "wis2watch-monitoring" / "vite.config.js"

#: The one chunk rollup splits out for itself. Named in the Vite config as a
#: literal so that it does not move; see ADR 0001.
SHARED_CHUNK = BUILT / "assets" / "shared.js"


#: The two paths the old app was named after, which is what the rename and
#: the rebuild together were for. Named rather than searching the bundles for
#: "mqtt", which would also read a dependency's minified strings and fail on
#: something this repository did not write.
RETIRED_PATHS = ("ws/mqtt-status/", "/api/mqtt-nodes/")


def vite_entries():
    """The island entry points, read from the Vite config that builds them."""
    entries = set(re.findall(r'"([a-z0-9-]+)":\s*resolve\(', VITE_CONFIG.read_text()))

    # Read out of JavaScript by pattern, so a restyled config could match
    # nothing at all and leave every assertion below vacuously true.
    assert entries, f"No Vite entries found in {VITE_CONFIG}"

    return entries


def feed_path():
    """The path the ingest feed is actually served at, from the routing."""
    assert len(websocket_urlpatterns) == 1, "More than one websocket route; name the one meant here"

    pattern = str(websocket_urlpatterns[0].pattern).strip("^$")

    return f"/{pattern}"


class BuiltBundleTests(SimpleTestCase):
    def test_every_island_has_a_bundle_committed_for_it(self):
        for entry in vite_entries():
            with self.subTest(entry=entry):
                self.assertTrue((BUILT / f"{entry}.js").exists())

    def test_no_bundle_is_committed_that_no_island_asks_for(self):
        """An orphan is how a page ends up served by code nobody can find.

        Renaming an entry writes a new file beside the old one, and the old
        one keeps working until somebody notices the template stopped naming
        it. Which is exactly how ``mqtt-monitor-map.js`` outlived the app it
        was named after.
        """
        committed = {path.stem for path in BUILT.glob("*.js")}

        self.assertEqual(committed, vite_entries())

    def test_what_the_islands_share_is_committed_with_them(self):
        self.assertTrue(SHARED_CHUNK.exists())

    def test_the_map_dials_the_path_the_feed_is_served_at(self):
        """The one address the bundle spells out for itself.

        Every other path this island uses is reversed in Python and handed
        over as a data attribute. The websocket is opened from the browser's
        own location, so it cannot be, which makes it the one thing a rename
        on the Django side can silently leave behind.
        """
        self.assertIn(feed_path(), (BUILT / "ingest-monitor-map.js").read_text())

    def test_no_bundle_still_asks_for_a_path_the_old_app_was_named_after(self):
        """MQTT is a transport a vantage point may use, not what this watches.

        A bundle asking for a retired path is the failure the rename could
        have caused and the rebuild is what prevents: the Django side answers
        at the new address and the committed island keeps dialling the old
        one, which fails in a browser and nowhere else.
        """
        for path in BUILT.glob("*.js"):
            built = path.read_text()

            for retired in RETIRED_PATHS:
                with self.subTest(bundle=path.name, path=retired):
                    self.assertNotIn(retired, built)
