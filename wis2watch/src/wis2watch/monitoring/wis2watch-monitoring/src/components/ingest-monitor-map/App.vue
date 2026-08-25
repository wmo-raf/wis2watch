<template>
  <div class="app-container">
    <MapSidebar
        :centres="filteredCentres"
        :connections="connections"
        :stats="stats"
        :current-filter="currentFilter"
        :selected-centre-id="selectedCentreId"
        @filter-change="handleFilterChange"
        @centre-select="selectCentre"
    />
    <IngestMap
        ref="mapRef"
        :centres-by-country="centresByCountry"
        :country-coordinates="countryCoordinates"
        :connection-status="connectionStatus"
        :selected-centre-id="selectedCentreId"
        @centre-click="selectCentre"
    />

    <Toast position="top-right"/>
  </div>
</template>

<script setup>
import {onMounted, onUnmounted, ref} from 'vue'
import {useToast} from 'primevue/usetoast'
import MapSidebar from './MapSidebar.vue'
import IngestMap from './IngestMap.vue'
import Toast from 'primevue/toast'
import {useWebSocket} from '@/composables/useWebSocket'
import {useMapNodes} from '@/composables/useMapNodes'

import 'primeicons/primeicons.css';

/**
 * How often the state of the broker connections is asked for again.
 *
 * Reachability is written by the ingestion supervisor as it dials, and
 * nothing pushes the change here, so the only way this page learns that a
 * broker came back is to ask. Slow on purpose: a connection coming and going
 * is a minutes-long event, and each ask is a database query per open map.
 */
const STATUS_REFRESH_MS = 30000

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
  connections,
  centresByCountry,
  countryCoordinates,
  selectedCentreId,
  currentFilter,
  stats,
  filteredCentres,
  fetchNodes,
  updateConnections,
  centreInfo,
  setFilter,
  selectCentre
} = useMapNodes()

let refreshTimer = null

onMounted(async () => {
  await fetchNodes(props.nodesApiUrl)

  refreshTimer = setInterval(() => sendMessage({action: 'get_status'}), STATUS_REFRESH_MS)
})

onUnmounted(() => {
  clearInterval(refreshTimer)
  refreshTimer = null
})

onMessage((message) => {
  switch (message.type) {
    // The state of every broker connection, whether asked for on arrival or
    // pushed. Both carry the whole picture, so both are read the same way.
    case 'status':
    case 'status_update':
      updateConnections(message.data)
      break

    case 'message':
      handleMessageReceived(message.data)
      break

    case 'error':
      handleError(message)
      break

    default:
      console.warn('⚠️ Unknown message type:', message.type)
  }
})

/**
 * A message the ingest just stored: show where it came from.
 *
 * The feed names the centre that published it, which is what the map places.
 * A centre nothing on the map knows about -- one publishing under a centre ID
 * no node has been registered for -- pulses nothing, and that is the honest
 * outcome: there is no marker for it.
 */
const handleMessageReceived = (data) => {
  if (!mapRef.value) {
    return
  }

  mapRef.value.pulseCentre(data.centre_id)

  const centre = centreInfo(data.centre_id)
  mapRef.value.showNotif(`${centre ? centre.name : data.centre_id} — ${data.topic}`)

  if (data.geometry) {
    mapRef.value.showDataPoint(data.geometry)
  }
}

const handleError = (message) => {
  console.error('❌ Feed error:', message.error)

  toast.add({
    severity: 'error',
    summary: 'Error',
    detail: message.error || 'An error occurred',
    life: 5000
  })
}

const handleFilterChange = (filter) => {
  setFilter(filter)
  sendMessage({action: 'get_status'})
}
</script>

<style>
.app-container {
  display: flex;
  height: 100vh;
}
</style>
