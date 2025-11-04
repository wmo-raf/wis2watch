<template>
  <div class="app-container">
    <MapSidebar
        :nodes="filteredNodes"
        :stats="stats"
        :current-filter="currentFilter"
        :selected-node-id="selectedNodeId"
        @filter-change="handleFilterChange"
        @node-select="handleNodeSelect"
    />
    <MQTTMap
        ref="mapRef"
        :nodes-by-country="nodesByCountry"
        :country-coordinates="countryCoordinates"
        :connection-status="connectionStatus"
        :selected-node-id="selectedNodeId"
        @node-click="handleNodeClick"
    />

    <Toast position="top-right"/>
  </div>
</template>

<script setup>
import {onMounted, ref} from 'vue'
import {useToast} from 'primevue/usetoast'
import MapSidebar from './MapSidebar.vue'
import MQTTMap from './MQTTMap.vue'
import Toast from 'primevue/toast'
import {useWebSocket} from '@/composables/useWebSocket'
import {useMapNodes} from '@/composables/useMapNodes'

import 'primeicons/primeicons.css';


const props = defineProps({
  nodesApiUrl: {
    type: String,
    required: true
  },
  languageCode: {
    type: String,
    required: false,
    default: 'en'
  },
});

const mapRef = ref(null)
const toast = useToast()

// WebSocket connection
const {connectionStatus, sendMessage, onMessage} = useWebSocket()

// Node management
const {
  nodesByCountry,
  countryCoordinates,
  selectedNodeId,
  currentFilter,
  stats,
  filteredNodes,
  fetchNodes,
  updateMonitoredNodes,
  setFilter,
  selectNode
} = useMapNodes()

onMounted(async () => {
  await fetchNodes()
})

/**
 * Handle incoming WebSocket messages
 * Backend sends different message types based on events
 */
onMessage((message) => {
  console.log('📨 Processing message:', message.type)

  switch (message.type) {
    case 'status':
      // Initial status or full status update
      // data: {node_id: {node_id, status, last_update, error}, ...}
      handleStatusUpdate(message.data)
      break

    case 'status_update':
      // Real-time status change for nodes
      // data: {node_id: {node_id, status, last_update, error}, ...}
      handleStatusUpdate(message.data)
      break

    case 'message':
      // MQTT message received notification
      // data: {node_id, topic, timestamp}
      handleMessageReceived(message.data)
      break

    case 'action_result':
      // Result of start/stop/restart action
      // {action, node_id, status: 'queued'}
      handleActionResult(message)
      break

    case 'error':
      // Error from backend
      // {error: string}
      handleError(message)
      break

    default:
      console.warn('⚠️ Unknown message type:', message.type)
  }
})

/**
 * Handle status updates from backend
 * Updates the monitored nodes state with latest status
 */
const handleStatusUpdate = (statusData) => {
  console.log('📊 Updating node status:', Object.keys(statusData).length, 'nodes')
  updateMonitoredNodes(statusData)
}

/**
 * Handle MQTT message received event
 * Shows notification and animates marker on map
 */
const handleMessageReceived = (data) => {
  console.log('📬 Message received for node:', data.node_id)

  if (mapRef.value) {
    mapRef.value.pulseMarker(data.node_id)

    // Show notification with topic info
    const topicInfo = data.topic ? ` (${data.topic})` : ''
    mapRef.value.showNotif(`Message received${topicInfo}`)
  }

  // Refresh status to get updated message count
  sendMessage({action: 'get_status'})
}

/**
 * Handle action result from backend
 * Shows confirmation that action was queued
 */
const handleActionResult = (result) => {
  const {action, node_id, status} = result

  console.log(`✅ Action result: ${action} for node ${node_id} - ${status}`)

  const actionLabels = {
    start: 'Start',
    stop: 'Stop',
    restart: 'Restart'
  }

  if (status === 'queued') {
    toast.add({
      severity: 'success',
      summary: 'Action Queued',
      detail: `${actionLabels[action] || action} command queued for processing`,
      life: 3000
    })
  }

  // Request status update to see changes
  setTimeout(() => {
    sendMessage({action: 'get_status'})
  }, 1000)
}

/**
 * Handle errors from backend
 */
const handleError = (message) => {
  console.error('❌ Backend error:', message.error)

  toast.add({
    severity: 'error',
    summary: 'Error',
    detail: message.error || 'An error occurred',
    life: 5000
  })
}

/**
 * Handle filter change in sidebar
 */
const handleFilterChange = (filter) => {
  setFilter(filter)
  // Request fresh status when filter changes
  sendMessage({action: 'get_status'})
}

/**
 * Handle node selection from sidebar
 */
const handleNodeSelect = (nodeId) => {
  selectNode(nodeId)
}

/**
 * Handle node click on map
 */
const handleNodeClick = (nodeId) => {
  selectNode(nodeId)
}
</script>

<style>
.app-container {
  display: flex;
  height: 100vh;
}
</style>