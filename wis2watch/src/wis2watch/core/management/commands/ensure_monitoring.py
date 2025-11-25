from django.core.management.base import BaseCommand

from wis2watch.mqtt.tasks import monitor_all_active_nodes


class Command(BaseCommand):
    help = 'Triggers the background task to ensure all active nodes are monitored'
    
    def handle(self, *args, **options):
        self.stdout.write("Triggering monitor scan via Celery...")
        # Send to Celery queue immediately
        monitor_all_active_nodes.delay()
        self.stdout.write(self.style.SUCCESS("Task queued."))
