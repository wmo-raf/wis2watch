"""How the built frontend bundles are fingerprinted for production."""

from django.contrib.staticfiles.storage import ManifestStaticFilesStorage


class ModuleAwareManifestStaticFilesStorage(ManifestStaticFilesStorage):
    """Manifest storage that follows an ES module's imports.

    The Vue islands are built as ES modules, and what two of them share is
    split into a chunk that each entry imports by a relative path. Django's
    stock manifest storage rewrites those paths in CSS but not in JavaScript,
    so the shared chunk would be served unfingerprinted -- the one file both
    dashboards depend on would also be the one file a browser is free to keep
    a stale copy of. Turning the aggregation on makes the import point at the
    hashed name like every other reference.
    """

    support_js_module_import_aggregation = True
