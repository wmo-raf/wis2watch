<template>
  <div class="map-container">
    <div ref="mapContainer" class="map"></div>
    <div v-if="showNotification" class="notification">
      {{ notificationMessage }}
    </div>
    <div class="connection-indicator" :class="connectionStatus">
      <i class="pi" :class="{
        'pi-check-circle': connectionStatus === 'connected',
        'pi-spin pi-spinner': connectionStatus === 'connecting',
        'pi-times-circle': connectionStatus === 'disconnected' || connectionStatus === 'error'
      }"></i>
      {{ statusText }}
    </div>
  </div>
</template>

<script setup>
import {computed, onMounted, ref, watch} from 'vue'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'

const props = defineProps({
  nodesByCountry: {
    type: Object,
    required: true
  },
  countryCoordinates: {
    type: Object,
    required: true
  },
  connectionStatus: {
    type: String,
    default: 'disconnected'
  },
  selectedNodeId: {
    type: Number,
    default: null
  }
})

const emit = defineEmits(['node-click'])

const mapContainer = ref(null)
const map = ref(null)
const markers = ref({})
const showNotification = ref(false)
const notificationMessage = ref('')

const statusText = computed(() => {
  switch (props.connectionStatus) {
    case 'connected':
      return 'Connected'
    case 'connecting':
      return 'Connecting...'
    case 'disconnected':
      return 'Disconnected'
    case 'error':
      return 'Error'
    default:
      return 'Connecting...'
  }
})

onMounted(() => {
  initMap()
})

watch(() => props.nodesByCountry, (newVal) => {
  updateMarkers()
}, {deep: true})

watch(() => props.countryCoordinates, (newVal) => {
}, {deep: true})

watch(() => props.selectedNodeId, (nodeId) => {
  if (nodeId && markers.value[nodeId]) {
    flyToNode(nodeId)
  }
})

const initMap = () => {
  map.value = new maplibregl.Map({
    container: mapContainer.value,
    style: 'https://geoserveis.icgc.cat/contextmaps/icgc_mapa_base_gris_simplificat.json',
    center: [20, 10],
    zoom: 2
  })

  map.value.addControl(new maplibregl.NavigationControl())
  map.value.addControl(new maplibregl.FullscreenControl())

  map.value.on('load', () => {
    console.log('🗺️ Map loaded')
    updateMarkers()
  })
}

const updateMarkers = () => {
  // Clear existing markers
  Object.values(markers.value).forEach(({marker}) => {
    marker.remove()
  })
  markers.value = {}

  // Create markers for each node at its country's center
  Object.entries(props.nodesByCountry).forEach(([countryCode, nodes]) => {
    const centerPoint = props.countryCoordinates[countryCode]

    if (!centerPoint) {
      console.warn(`⚠️ No center point for country: ${countryCode}`)
      return
    }

    // Create a marker for each node at the country center
    nodes.forEach((node) => {
      createMarker(node, centerPoint)
    })
  })
}

const createMarker = (node, coords) => {
  const el = document.createElement('div')
  el.className = 'marker'

  // Set styles inline - MapLibre works better with inline styles
  el.style.width = '30px'
  el.style.height = '30px'
  el.style.borderRadius = '50%'
  el.style.border = '3px solid white'
  el.style.boxShadow = '0 2px 8px rgba(0, 0, 0, 0.3)'
  el.style.cursor = 'pointer'

  // Don't add inline styles that will conflict with CSS
  updateMarkerStyle(el, node)

  const marker = new maplibregl.Marker({element: el})
      .setLngLat(coords)
      .addTo(map.value)

  // Click handler
  el.addEventListener('click', (e) => {
    e.stopPropagation()
    showPopup(node, coords)
    emit('node-click', node.id)
  })

  markers.value[node.id] = {marker, element: el, coords}
}

const updateMarkerStyle = (element, node) => {
  let color
  if (!node.is_monitored) {
    color = '#9ca3af' // gray
  } else if (node.is_connected) {
    color = '#22c55e' // green
  } else if (node.state === 'connecting') {
    color = '#eab308' // yellow
  } else {
    color = '#ef4444' // red
  }

  element.style.backgroundColor = color
}

const showPopup = (node, coords) => {
  const isMonitored = node.is_monitored
  const isConnected = node.is_connected

  const severityClass = isMonitored
      ? isConnected
          ? 'success'
          : 'danger'
      : 'secondary'

  // Format last message time
  let lastMessageHTML = ''
  if (isMonitored && node.last_message_time) {
    const relativeTime = formatRelativeTime(node.last_message_time)
    lastMessageHTML = `
      <div class="info-row">
        <i class="pi pi-clock"></i>
        <label>Last Message:</label>
        <span class="p-badge p-badge-info">${relativeTime}</span>
      </div>
    `
  } else if (isMonitored) {
    lastMessageHTML = `
      <div class="info-row">
        <i class="pi pi-clock"></i>
        <label>Last Message:</label>
        <span class="p-badge p-badge-secondary">No messages yet</span>
      </div>
    `
  }

  // Create link to node base URL
  const nodeUrlHTML = node.base_url ? `
    <div class="popup-link">
      <a href="${node.base_url}" target="_blank" rel="noopener noreferrer" class="p-button p-button-outlined p-button-sm">
        <i class="pi pi-external-link"></i>
        <span>Visit Node</span>
      </a>
    </div>
  ` : ''

  const popup = new maplibregl.Popup({closeButton: true, className: 'prime-popup'})
      .setLngLat(coords)
      .setHTML(
          `
      <div class="p-card">
        <div class="popup-header">
          <span class="p-badge p-badge-${severityClass}">${isMonitored ? node.state : 'inactive'}</span>
          <h3>${node.name}</h3>
        </div>

        <div class="popup-body">
          <div class="info-row">
            <i class="pi pi-globe"></i>
            <label>Country:</label>
            <span>${node.country}</span>
          </div>
          <div class="info-row">
            <i class="pi pi-id-card"></i>
            <label>Centre ID:</label>
            <span>${node.centre_id || 'N/A'}</span>
          </div>
          ${
              isMonitored
                  ? `
            <div class="info-row">
              <i class="pi pi-info-circle"></i>
              <label>Status:</label>
              <span class="p-badge p-badge-${severityClass}">${node.state}</span>
            </div>
            <div class="info-row">
              <i class="pi pi-envelope"></i>
              <label>Messages:</label>
              <span class="p-chip p-chip-sm">${node.message_count || 0}</span>
            </div>
            <div class="info-row">
              <i class="pi pi-rss"></i>
              <label>Subscriptions:</label>
              <span class="p-chip p-chip-sm">${node.subscription_count || 0}</span>
            </div>
            ${lastMessageHTML}
          `
                  : `
            <div class="info-row">
              <i class="pi pi-info-circle"></i>
              <label>Status:</label>
              <span class="p-badge p-badge-secondary">Not monitored</span>
            </div>
          `
          }
        </div>

        ${nodeUrlHTML}
      </div>
    `
      )
      .addTo(map.value)
}

const flyToNode = (nodeId) => {
  if (markers.value[nodeId]) {
    const {coords} = markers.value[nodeId]
    map.value.flyTo({center: coords, zoom: 6})
  }
}

const pulseMarker = (nodeId) => {
  if (markers.value[nodeId]) {
    const {element} = markers.value[nodeId]
    element.classList.add('pulse')
    setTimeout(() => element.classList.remove('pulse'), 2000)
  }
}

const showNotif = (message) => {
  notificationMessage.value = message
  showNotification.value = true
  setTimeout(() => {
    showNotification.value = false
  }, 3000)
}

const formatRelativeTime = (timestamp) => {
  if (!timestamp) return 'Never'

  const now = Date.now()
  const messageTime = new Date(timestamp).getTime()
  const diffMs = now - messageTime
  const diffSec = Math.floor(diffMs / 1000)
  const diffMin = Math.floor(diffSec / 60)
  const diffHour = Math.floor(diffMin / 60)
  const diffDay = Math.floor(diffHour / 24)

  if (diffSec < 30) {
    return 'Just now'
  } else if (diffSec < 60) {
    return `${diffSec}s ago`
  } else if (diffMin < 60) {
    return `${diffMin}m ago`
  } else if (diffHour < 24) {
    return `${diffHour}h ago`
  } else if (diffDay === 1) {
    return 'Yesterday'
  } else if (diffDay < 7) {
    return `${diffDay} days ago`
  } else {
    return new Date(timestamp).toLocaleDateString()
  }
}

defineExpose({
  pulseMarker,
  showNotif,
  flyToNode
})
</script>

<style scoped>
.map-container {
  flex: 1;
  position: relative;
}

.map {
  width: 100%;
  height: 100%;
}

.notification {
  position: absolute;
  top: 20px;
  left: 50%;
  transform: translateX(-50%);
  background: var(--primary-color);
  color: white;
  padding: 1rem 2rem;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  z-index: 1000;
  animation: slideDown 0.3s ease;
}

@keyframes slideDown {
  from {
    opacity: 0;
    transform: translateX(-50%) translateY(-20px);
  }
  to {
    opacity: 1;
    transform: translateX(-50%) translateY(0);
  }
}

.connection-indicator {
  position: absolute;
  bottom: 20px;
  right: 20px;
  background: white;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.2);
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  z-index: 100;
}

.connection-indicator.connected {
  color: var(--green-600);
}

.connection-indicator.connecting {
  color: var(--yellow-600);
}

.connection-indicator.disconnected,
.connection-indicator.error {
  color: var(--red-600);
}

:deep(.marker) {
  transition: transform 0.2s;
}

:deep(.marker:hover) {
  transform: scale(1.2);
}

:deep(.marker.pulse) {
  animation: pulse 1s ease-out;
}

@keyframes pulse {
  0%, 100% {
    transform: scale(1);
    opacity: 1;
  }
  50% {
    transform: scale(1.5);
    opacity: 0.7;
  }
}

/* Popup styles */
:deep(.maplibregl-popup-content) {
  padding: 0;
  border-radius: 8px;
  box-shadow: 0 4px 12px rgba(0, 0, 0, 0.15);
  min-width: 300px;
}

:deep(.p-card) {
  border-radius: 8px;
  overflow: hidden;
}

:deep(.popup-header) {
  padding: 1rem;
  background: var(--surface-50);
  border-bottom: 1px solid var(--surface-200);
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

:deep(.popup-header h3) {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
  color: var(--text-color);
}

:deep(.popup-body) {
  padding: 1rem;
}

:deep(.info-row) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  margin-bottom: 0.75rem;
  font-size: 0.9rem;
}

:deep(.info-row:last-child) {
  margin-bottom: 0;
}

:deep(.info-row i) {
  color: var(--primary-color);
  width: 20px;
}

:deep(.info-row label) {
  font-weight: 600;
  color: var(--text-color-secondary);
  min-width: 90px;
}

:deep(.info-row span) {
  color: var(--text-color);
}

:deep(.popup-link) {
  padding: 1rem;
  border-top: 1px solid var(--surface-200);
  display: flex;
  justify-content: center;
}

:deep(.popup-link .p-button) {
  width: 100%;
  justify-content: center;
  gap: 0.5rem;
}

/* PrimeVue badge and chip overrides for popup */
:deep(.p-badge) {
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
}

:deep(.p-chip) {
  font-size: 0.75rem;
  padding: 0.25rem 0.5rem;
  background: var(--surface-100);
}

/* Button styles in popup */
:deep(.p-button) {
  font-size: 0.875rem;
  padding: 0.5rem 1rem;
  border-radius: 4px;
  display: inline-flex;
  align-items: center;
  gap: 0.5rem;
  cursor: pointer;
  transition: all 0.2s;
  text-decoration: none;
  border: 1px solid var(--primary-color);
  color: var(--primary-color);
  background: transparent;
}

:deep(.p-button:hover) {
  background: var(--primary-color);
  color: white;
  transform: translateY(-1px);
  box-shadow: 0 2px 4px rgba(0, 0, 0, 0.1);
}

:deep(.p-button i) {
  font-size: 1rem;
}
</style>