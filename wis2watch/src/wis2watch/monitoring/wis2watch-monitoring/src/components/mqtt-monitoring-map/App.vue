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
        @action="handleAction"
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

const {connectionStatus, sendMessage, onMessage} = useWebSocket()

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

onMessage((message) => {
  if (message.type === 'status') {
    updateMonitoredNodes(message.data)
  } else if (message.type === 'message') {
    handleMessageReceived(message.data)
  } else if (message.type === 'error') {
    toast.add({
      severity: 'error',
      summary: 'Error',
      detail: message.error,
      life: 5000
    })
  }
})

const handleFilterChange = (filter) => {
  setFilter(filter)
  sendMessage({action: 'get_status'})
}

const handleNodeSelect = (nodeId) => {
  selectNode(nodeId)
}

const handleNodeClick = (nodeId) => {
  selectNode(nodeId)
}

const handleAction = ({action, nodeId}) => {
  sendMessage({action, node_id: nodeId})

  const actionMessages = {
    start: 'Starting monitoring...',
    stop: 'Stopping monitoring...',
    restart: 'Restarting monitoring...'
  }

  toast.add({
    severity: 'info',
    summary: 'Action',
    detail: actionMessages[action] || 'Processing...',
    life: 3000
  })
}

const handleMessageReceived = (data) => {
  if (mapRef.value) {
    mapRef.value.pulseMarker(data.node_id)
    mapRef.value.showNotif(`Message received from ${data.node_name}`)
  }
}
</script>

<style>

.app-container {
  display: flex;
  height: 100vh;
}
</style>