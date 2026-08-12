"""Findings: what the stored observations add up to.

Everything in here reads the database and returns findings -- structures a
view or an email can render without knowing how they were arrived at. Nothing
in here writes, fetches or connects.

This is the second of the project's two test seams. Its functions are tested
against a seeded database rather than hand-built inputs, because the mistakes
that matter here -- a bucket an hour out, a join that multiplies rows, a
centre missing from a table because it has nothing to join to -- produce a
confident wrong answer rather than an exception, and only real rows expose
them.
"""

from .overview import (
    NodeOverviewRow,
    OriginReachability,
    Staleness,
    default_volume_hours,
    node_overview,
)
from .silence import (
    DatasetSilenceRow,
    Expectation,
    NodeSilence,
    Silence,
    dataset_silence,
    silence_by_node,
)

__all__ = [
    "DatasetSilenceRow",
    "Expectation",
    "NodeOverviewRow",
    "NodeSilence",
    "OriginReachability",
    "Silence",
    "Staleness",
    "dataset_silence",
    "default_volume_hours",
    "node_overview",
    "silence_by_node",
]
