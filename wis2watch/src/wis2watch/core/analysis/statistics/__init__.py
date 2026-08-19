"""What one centre's activity adds up to, for the statistics tab.

A package rather than a module because the tab asks three differently shaped
questions of the same node -- what is true now and over a window, one row per
station, and one station in full -- and they are answered by different queries
against different tables. Each gets its own module as it arrives.
"""

from .series import (
    Bucket,
    DailyActivity,
    HourlyActivity,
    WindowTotals,
    bucket_axis,
    daily_activity,
    hour_of_day_profile,
    hourly_activity,
    window_totals,
)
from .stations import (
    NodeStationStatistics,
    StationRow,
    node_station_statistics,
)
from .summary import (
    NodeStatisticsSummary,
    NowBlock,
    Vantage,
    WindowBounds,
    WindowOption,
    WindowStats,
    node_statistics_summary,
)

__all__ = [
    "Bucket",
    "DailyActivity",
    "HourlyActivity",
    "NodeStationStatistics",
    "NodeStatisticsSummary",
    "NowBlock",
    "StationRow",
    "Vantage",
    "WindowBounds",
    "WindowOption",
    "WindowStats",
    "WindowTotals",
    "bucket_axis",
    "daily_activity",
    "hour_of_day_profile",
    "hourly_activity",
    "node_station_statistics",
    "node_statistics_summary",
    "window_totals",
]
