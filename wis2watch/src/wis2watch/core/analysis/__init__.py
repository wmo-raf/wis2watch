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

from .gaps import (
    GAP_REPORTS,
    GapReport,
    GapReportSummary,
    Notice,
    PropagationGapRow,
    SilentStationRow,
    UnattributedRateRow,
    UndeclaredStationRow,
    UnregisteredCentreRow,
    default_attribution_window_hours,
    gap_report,
    gap_report_summaries,
    propagation_gaps,
    propagation_gaps_left_out,
    stations_declared_but_silent,
    stations_transmitting_undeclared,
    unattributed_rates,
    unregistered_centres,
)
from .node_detail import (
    NodeDatasetRow,
    NodeDetail,
    OriginState,
    SyncRunRow,
    SyncScope,
    node_detail,
)
from .overview import (
    CachePickup,
    NodeOverviewRow,
    default_volume_hours,
    node_overview,
)
from .reachability import OriginReachability, OriginTransport, OriginWatch
from .silence import (
    DatasetSilenceRow,
    Expectation,
    NodeSilence,
    Silence,
    dataset_silence,
    silence_by_node,
)
from .staleness import Staleness
from .statistics import (
    Bucket,
    HourlyActivity,
    NodeStatisticsSummary,
    NowBlock,
    Vantage,
    WindowBounds,
    WindowOption,
    node_statistics_summary,
)
from .stations import NodeStationRow, StationStanding, node_stations
from .windows import Grain, UnknownWindow, Window

__all__ = [
    "GAP_REPORTS",
    "Bucket",
    "CachePickup",
    "DatasetSilenceRow",
    "Expectation",
    "GapReport",
    "GapReportSummary",
    "Grain",
    "HourlyActivity",
    "NodeDatasetRow",
    "NodeDetail",
    "NodeOverviewRow",
    "NodeSilence",
    "NodeStationRow",
    "NodeStatisticsSummary",
    "Notice",
    "NowBlock",
    "OriginReachability",
    "OriginState",
    "OriginTransport",
    "OriginWatch",
    "PropagationGapRow",
    "Silence",
    "SilentStationRow",
    "Staleness",
    "StationStanding",
    "SyncRunRow",
    "SyncScope",
    "UnattributedRateRow",
    "UndeclaredStationRow",
    "UnknownWindow",
    "UnregisteredCentreRow",
    "Vantage",
    "Window",
    "WindowBounds",
    "WindowOption",
    "dataset_silence",
    "default_attribution_window_hours",
    "default_volume_hours",
    "gap_report",
    "gap_report_summaries",
    "node_detail",
    "node_overview",
    "node_stations",
    "node_statistics_summary",
    "propagation_gaps",
    "propagation_gaps_left_out",
    "silence_by_node",
    "stations_declared_but_silent",
    "stations_transmitting_undeclared",
    "unattributed_rates",
    "unregistered_centres",
]
