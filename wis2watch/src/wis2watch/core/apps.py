import logging
import os
import sys
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    name = "wis2watch.core"
    label = "wis2watchcore"
    verbose_name = "WiS2Watch Core"
    
    _started = False
    # _lock = threading.Lock()
    
    # def ready(self):
    #     """Start MQTT monitoring once, only in main web process (not workers)."""
    #     if os.getenv("DJANGO_CONTEXT") != "web":
    #         return
    #
    #     # Skip management commands
    #     if len(sys.argv) > 1 and sys.argv[0].endswith("manage.py"):
    #         return
    #
    #     # Detect if running under gunicorn and skip worker processes
    #     if "gunicorn" in os.environ.get("SERVER_SOFTWARE", "").lower():
    #         try:
    #             import psutil
    #             parent = psutil.Process(os.getppid())
    #             if "gunicorn" in parent.name().lower():
    #                 logger.debug("Skipping MQTT start in gunicorn worker process.")
    #                 return
    #         except Exception:
    #             # Fallback: skip if PID differs from main process PID file
    #             if os.getenv("GUNICORN_WORKER"):
    #                 return
    #
    #     # Thread-safe singleton check
    #     with self._lock:
    #         if self._started:
    #             return
    #         self._started = True
    #
    #     # Start in a daemon thread
    #     # threading.Thread(target=self._start_mqtt_monitoring, daemon=True).start()
    #
    # def _start_mqtt_monitoring(self):
    #     """Actually start the monitoring service."""
    #     time.sleep(2)
    #     try:
    #         from .mqtt_client import mqtt_service
    #         logger.info("🚀 Starting MQTT monitoring service...")
    #         mqtt_service.start_all()
    #         status = mqtt_service.get_status()
    #         logger.info(f"✅ MQTT monitoring started for {status['total_clients']} nodes")
    #     except Exception:
    #         logger.exception("❌ Failed to start MQTT monitoring.")
