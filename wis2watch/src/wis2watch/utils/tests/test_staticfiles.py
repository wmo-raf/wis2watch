"""Tests for the project-wide utilities."""

import os
import tempfile

from django.test import SimpleTestCase

from ..staticfiles import ModuleAwareManifestStaticFilesStorage


class ModuleAwareStaticFilesTests(SimpleTestCase):
    """Fingerprinting has to reach what an ES module imports for itself.

    The islands are built as modules that import a shared chunk by relative
    path, and a template names only the entry. If fingerprinting stopped at
    the entry, the shared chunk would be the one file nothing could bust a
    stale cache of.
    """

    def collect(self, files):
        """Post-process a static tree, returning where each file ended up."""
        root = tempfile.mkdtemp()

        for path, content in files.items():
            os.makedirs(os.path.join(root, os.path.dirname(path)), exist_ok=True)
            with open(os.path.join(root, path), "w") as handle:
                handle.write(content)

        storage = ModuleAwareManifestStaticFilesStorage(
            location=root, base_url="/static/"
        )
        paths = {path: (storage, path) for path in files}

        for _ in storage.post_process(paths):
            pass

        return storage, root

    def read(self, storage, root, path):
        """What was written out under a path's fingerprinted name."""
        with open(os.path.join(root, storage.stored_name(path))) as handle:
            return handle.read()

    def test_a_shared_chunk_is_fingerprinted_where_the_entry_imports_it(self):
        storage, root = self.collect(
            {
                "vue/node-statistics.js": 'import{c}from"./assets/index.js";c();\n',
                "vue/assets/index.js": "export const c=()=>1;\n",
            }
        )

        entry = self.read(storage, root, "vue/node-statistics.js")
        chunk_name = os.path.basename(storage.stored_name("vue/assets/index.js"))

        self.assertIn(chunk_name, entry)
        self.assertNotEqual(chunk_name, "index.js")
