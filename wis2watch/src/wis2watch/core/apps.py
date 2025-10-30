import logging
import sys
import threading
import time

from django.apps import AppConfig

logger = logging.getLogger(__name__)


class CoreConfig(AppConfig):
    default_auto_field = 'django.db.models.BigAutoField'
    name = 'wis2watch.core'
    label = 'wis2watchcore'
    verbose_name = 'WiS2Watch Core'
    mqtt_started = False
    
    def ready(self):
        """Start MQTT monitoring when Django is ready"""
        
        # Prevent double-initialization
        if CoreConfig.mqtt_started:
            return
        
        # Skip for management commands
        skip_commands = [
            'migrate', 'makemigrations', 'collectstatic', 'createsuperuser',
            'shell', 'dbshell', 'test', 'check', 'showmigrations',
            'mqtt_monitor'
        ]
        
        if len(sys.argv) > 1 and sys.argv[1] in skip_commands:
            logger.info(f"Skipping MQTT auto-start for command: {sys.argv[1]}")
            return
        
        # Mark as started to prevent double initialization
        CoreConfig.mqtt_started = True
        
        # Start in a separate thread
        thread = threading.Thread(target=self.start_mqtt_monitoring, daemon=True)
        thread.start()
    
    def start_mqtt_monitoring(self):
        """Start MQTT monitoring in a separate thread"""
        # Small delay to ensure Django is fully initialized
        time.sleep(2)
        
        try:
            from .mqtt_client import mqtt_service
            
            logger.info("🚀 Starting MQTT monitoring service...")
            mqtt_service.start_all()
            
            status = mqtt_service.get_status()
            logger.info(f"✅ MQTT monitoring started for {status['total_clients']} nodes")
            
            for client in status['clients']:
                logger.info(
                    f"   • {client['node_name']} (ID: {client['node_id']}): "
                    f"{client['state']}"
                )
        
        except Exception as e:
            logger.error(f"❌ Failed to start MQTT monitoring: {e}", exc_info=True)
