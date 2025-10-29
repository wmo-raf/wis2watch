import logging
from datetime import timedelta

from django.utils import timezone as dj_timezone

from .models import StationMQTTMessageLog

logger = logging.getLogger(__name__)


def cleanup_old_station_message_logs(days=90):
    """
    Remove observations older than specified days to manage database size.
    
    Args:
        days: Number of days to retain (default: 90)
    """
    
    cutoff_date = dj_timezone.now() - timedelta(days=days)
    
    deleted_count = StationMQTTMessageLog.objects.filter(time__lt=cutoff_date).delete()[0]
    
    logger.info(f"Deleted {deleted_count} observations older than {days} days")
    
    return {'deleted_count': deleted_count, 'cutoff_date': cutoff_date}
