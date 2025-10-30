import logging
import time

from django.core.management.base import BaseCommand, CommandError

from wis2watch.core.models import WIS2Node
from wis2watch.core.mqtt_client import mqtt_service

logger = logging.getLogger(__name__)


class Command(BaseCommand):
    help = 'Manage MQTT monitoring for WIS2 nodes'
    
    def add_arguments(self, parser):
        parser.add_argument(
            'action',
            type=str,
            choices=['start', 'stop', 'restart', 'status', 'start-all', 'stop-all'],
            help='Action to perform',
        )
        
        parser.add_argument(
            '--node-id',
            type=int,
            help='Node ID for start/stop/restart actions',
        )
    
    def handle(self, *args, **options):
        action = options['action']
        node_id = options.get('node_id')
        
        try:
            if action == 'start':
                self.handle_start(node_id)
            elif action == 'stop':
                self.handle_stop(node_id)
            elif action == 'restart':
                self.handle_restart(node_id)
            elif action == 'status':
                self.handle_status()
            elif action == 'start-all':
                self.handle_start_all()
            elif action == 'stop-all':
                self.handle_stop_all()
        except Exception as e:
            raise CommandError(f'Error: {e}')
    
    def handle_start(self, node_id):
        """Start monitoring for a specific node"""
        if not node_id:
            raise CommandError('--node-id is required for start action')
        
        try:
            node = WIS2Node.objects.get(pk=node_id)
        except WIS2Node.DoesNotExist:
            raise CommandError(f'Node with ID {node_id} does not exist')
        
        self.stdout.write(f'Starting MQTT monitoring for: {node.name} (ID: {node_id})')
        
        success = mqtt_service.start_node(node_id)
        
        if success:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Successfully started monitoring for {node.name}')
            )
        else:
            raise CommandError(f'Failed to start monitoring for {node.name}')
    
    def handle_stop(self, node_id):
        """Stop monitoring for a specific node"""
        if not node_id:
            raise CommandError('--node-id is required for stop action')
        
        success = mqtt_service.stop_node(node_id)
        
        if success:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Successfully stopped monitoring for node {node_id}')
            )
        else:
            self.stdout.write(
                self.style.WARNING(f'Node {node_id} was not running')
            )
    
    def handle_restart(self, node_id):
        """Restart monitoring for a specific node"""
        if not node_id:
            raise CommandError('--node-id is required for restart action')
        
        try:
            node = WIS2Node.objects.get(pk=node_id)
        except WIS2Node.DoesNotExist:
            raise CommandError(f'Node with ID {node_id} does not exist')
        
        self.stdout.write(f'Restarting MQTT monitoring for: {node.name} (ID: {node_id})')
        
        success = mqtt_service.restart_node(node_id)
        
        if success:
            self.stdout.write(
                self.style.SUCCESS(f'✓ Successfully restarted monitoring for {node.name}')
            )
        else:
            raise CommandError(f'Failed to restart monitoring for {node.name}')
    
    def handle_status(self):
        """Show status of all MQTT clients"""
        status = mqtt_service.get_status()
        
        self.stdout.write(self.style.HTTP_INFO('=== MQTT Monitoring Status ==='))
        self.stdout.write(f'Total active clients: {status["total_clients"]}')
        self.stdout.write('')
        
        if status['total_clients'] == 0:
            self.stdout.write(self.style.WARNING('No active MQTT clients'))
            self.stdout.write('')
            self.stdout.write('View the dashboard to start monitoring nodes.')
            return
        
        for client in status['clients']:
            state_style = self.style.SUCCESS if client['is_connected'] else self.style.WARNING
            
            self.stdout.write(f"Node: {client['node_name']} (ID: {client['node_id']})")
            self.stdout.write(f"  State: {state_style(client['state'].upper())}")
            self.stdout.write(f"  Connected: {'Yes' if client['is_connected'] else 'No'}")
            self.stdout.write(f"  Subscriptions: {client['subscription_count']}")
            
            if client.get('message_count') is not None:
                self.stdout.write(f"  Messages received: {client['message_count']}")
            
            self.stdout.write('')
    
    def handle_start_all(self):
        """Start monitoring for all active nodes"""
        active_nodes = WIS2Node.objects.filter(
            status='active'
        ).exclude(mqtt_host='')
        
        if not active_nodes.exists():
            self.stdout.write(self.style.WARNING('No active nodes found'))
            return
        
        self.stdout.write(
            f'Starting MQTT monitoring for {active_nodes.count()} active nodes...'
        )
        
        mqtt_service.start_all()
        
        # Wait a moment for connections to establish
        time.sleep(2)
        
        status = mqtt_service.get_status()
        
        self.stdout.write(
            self.style.SUCCESS(
                f'✓ Successfully started monitoring for {status["total_clients"]} nodes'
            )
        )
        self.stdout.write('')
        
        # Show status of each client
        for client in status['clients']:
            state_style = self.style.SUCCESS if client['is_connected'] else self.style.WARNING
            self.stdout.write(
                f'  • {client["node_name"]} (ID: {client["node_id"]}): '
                f'{state_style(client["state"].upper())}'
            )
        
        self.stdout.write('')
        self.stdout.write('Monitor status in real-time at: /mqtt-monitor/')
    
    def handle_stop_all(self):
        """Stop monitoring for all nodes"""
        status = mqtt_service.get_status()
        
        if status['total_clients'] == 0:
            self.stdout.write(self.style.WARNING('No active clients to stop'))
            return
        
        self.stdout.write(f'Stopping {status["total_clients"]} MQTT clients...')
        
        mqtt_service.stop_all()
        
        self.stdout.write(
            self.style.SUCCESS('✓ Successfully stopped all MQTT monitoring')
        )
