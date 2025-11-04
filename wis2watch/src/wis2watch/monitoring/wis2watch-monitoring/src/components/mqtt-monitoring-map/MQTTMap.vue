<template>
  <div class="map-container">
    <div ref="mapContainer" class="map"></div>

    <Card class="ws-status">
      <template #content>
        <div class="status-content">
          <Badge
              :severity="statusSeverity"
              :value="statusText"
          >
            <i :class="statusIcon"></i>
          </Badge>
        </div>
      </template>
    </Card>

    <Card class="legend">
      <template #header>
        <div class="legend-title">
          <i class="pi pi-info-circle"></i>
          Node Status
        </div>
      </template>
      <template #content>
        <div class="legend-items">
          <div v-for="item in legendItems" :key="item.label" class="legend-item">
            <Badge :severity="item.severity"/>
            <span>{{ item.label }}</span>
          </div>
        </div>
      </template>
    </Card>

    <transition name="p-message">
      <Message v-if="showNotification" severity="success" :closable="false" class="notification">
        <i class="pi pi-check-circle"></i>
        {{ notificationMessage }}
      </Message>
    </transition>
  </div>
</template>

<script setup>
import {ref, onMounted, watch, computed} from 'vue'
import maplibregl from 'maplibre-gl'
import 'maplibre-gl/dist/maplibre-gl.css'
import Card from 'primevue/card'
import Badge from 'primevue/badge'
import Message from 'primevue/message'

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

const legendItems = [
  {label: 'Connected', severity: 'success'},
  {label: 'Connecting', severity: 'warning'},
  {label: 'Disconnected', severity: 'danger'},
  {label: 'Not Monitored', severity: 'secondary'}
]

const statusSeverity = computed(() => {
  switch (props.connectionStatus) {
    case 'connected':
      return 'success'
    case 'disconnected':
      return 'danger'
    case 'error':
      return 'danger'
    default:
      return 'warning'
  }
})

const statusIcon = computed(() => {
  switch (props.connectionStatus) {
    case 'connected':
      return 'pi pi-check-circle'
    case 'disconnected':
      return 'pi pi-times-circle'
    case 'error':
      return 'pi pi-exclamation-circle'
    default:
      return 'pi pi-spinner pi-spin'
  }
})

const statusText = computed(() => {
  switch (props.connectionStatus) {
    case 'connected':
      return 'Connected'
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

  map.value.addControl(new maplibregl.FullscreenControl(), "bottom-right")
  map.value.addControl(new maplibregl.NavigationControl({showCompass: false}), "bottom-right")

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

  const marker = new maplibregl.Marker({
    element: el,
    draggable: false
  })
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

  // Link to node base URL
  const nodeUrlHTML = node.base_url ? `
    <div class="popup-actions">
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

/**
 * Show a temporary pulsing data point at a specific location
 * Used to visualize incoming MQTT messages with geometry data
 *
 * @param {Object} geometry - GeoJSON geometry object with coordinates
 * Example: {type: "Point", coordinates: [lon, lat, elevation]}
 */
const showDataPoint = (geometry) => {
  if (!map.value) {
    console.warn('⚠️ Map not initialized yet')
    return
  }

  if (!geometry || geometry.type !== 'Point' || !geometry.coordinates) {
    console.warn('⚠️ Invalid geometry for data point:', geometry)
    return
  }

  const [lon, lat] = geometry.coordinates

  // Create temporary marker element
  const el = document.createElement('div')
  el.className = 'data-point'

  // Create marker
  const tempMarker = new maplibregl.Marker({
    element: el,
    draggable: false,
    anchor: 'center'
  })
      .setLngLat([lon, lat])
      .addTo(map.value)

  console.log(`📍 Data point shown at [${lon.toFixed(4)}, ${lat.toFixed(4)}]`)

  // Remove marker after animation completes (3 seconds)
  setTimeout(() => {
    tempMarker.remove()
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
  flyToNode,
  showDataPoint  // NEW: Expose the data point visualization function
})
</script>

<style scoped>
.map-container {
  flex: 1;
  position: relative;
  height: 100%;
}

.map {
  width: 100%;
  height: 100%;
}

.ws-status {
  position: absolute;
  top: 10px;
  right: 10px;
  z-index: 1000;
  min-width: 150px;
}

.ws-status :deep(.p-card-body) {
  padding: 0.75rem;
}

.ws-status :deep(.p-card-content) {
  padding: 0;
}

.status-content {
  display: flex;
  align-items: center;
  justify-content: center;
}

.status-content :deep(.p-badge) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
  padding: 0.5rem 0.75rem;
}

.legend {
  position: absolute;
  bottom: 30px;
  left: 10px;
  z-index: 1000;
  min-width: 200px;
}

.legend :deep(.p-card-body) {
  padding: 1rem;
}

.legend-title {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-weight: 600;
  font-size: 0.95rem;
  padding: 0.75rem 1rem;
  border-bottom: 1px solid var(--surface-border);
}

.legend-items {
  display: flex;
  flex-direction: column;
  gap: 0.5rem;
}

.legend-item {
  display: flex;
  align-items: center;
  gap: 0.75rem;
  font-size: 0.875rem;
}

.notification {
  position: absolute;
  top: 70px;
  right: 10px;
  z-index: 1000;
  min-width: 300px;
  animation: slideInRight 0.3s ease-out;
}

@keyframes slideInRight {
  from {
    transform: translateX(100%);
    opacity: 0;
  }
  to {
    transform: translateX(0);
    opacity: 1;
  }
}

/* Node markers */
.marker {
  width: 30px;
  height: 30px;
  border-radius: 50%;
  border: 3px solid white;
  box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  cursor: pointer;
  transition: box-shadow 0.3s, filter 0.3s;
}

.marker:hover {
  box-shadow: 0 4px 16px rgba(0, 0, 0, 0.5);
  filter: brightness(1.1);
}

.marker.pulse {
  animation: markerPulse 2s ease-out;
}

@keyframes markerPulse {
  0%, 100% {
    opacity: 1;
    box-shadow: 0 2px 8px rgba(0, 0, 0, 0.3);
  }
  50% {
    opacity: 0.6;
    box-shadow: 0 2px 20px rgba(0, 0, 0, 0.6);
  }
}

/* NEW: Temporary data point visualization */
.data-point {
  width: 16px;
  height: 16px;
  border-radius: 50%;
  background: rgba(34, 197, 94, 0.9); /* Bright green */
  border: 2px solid rgba(255, 255, 255, 1);
  box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
  0 0 30px rgba(34, 197, 94, 0.4);
  animation: dataPointPulse 3s ease-out forwards;
  pointer-events: none; /* Don't interfere with map interactions */
}

@keyframes dataPointPulse {
  0% {
    transform: scale(0);
    opacity: 0;
    box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
    0 0 30px rgba(34, 197, 94, 0.4);
  }
  15% {
    transform: scale(1.3);
    opacity: 1;
    box-shadow: 0 0 20px rgba(34, 197, 94, 1),
    0 0 40px rgba(34, 197, 94, 0.6);
  }
  25% {
    transform: scale(1);
    opacity: 1;
  }
  /* Hold visible with pulsing glow */
  75% {
    transform: scale(1);
    opacity: 1;
    box-shadow: 0 0 15px rgba(34, 197, 94, 0.8),
    0 0 30px rgba(34, 197, 94, 0.4);
  }
  /* Fade out and expand */
  100% {
    transform: scale(2);
    opacity: 0;
    box-shadow: 0 0 5px rgba(34, 197, 94, 0.2);
  }
}

/* Popup styles */
:deep(.prime-popup .maplibregl-popup-content) {
  padding: 0;
  border-radius: var(--border-radius);
  overflow: hidden;
  min-width: 300px;
}

:deep(.prime-popup .p-card) {
  border: none;
  box-shadow: none;
}

:deep(.popup-header) {
  padding: 1rem;
  border-bottom: 1px solid var(--surface-border);
  display: flex;
  align-items: center;
  gap: 0.75rem;
}

:deep(.popup-header h3) {
  margin: 0;
  font-size: 1.1rem;
  font-weight: 600;
}

:deep(.popup-body) {
  padding: 1rem;
  display: flex;
  flex-direction: column;
  gap: 0.75rem;
}

:deep(.info-row) {
  display: flex;
  align-items: center;
  gap: 0.5rem;
  font-size: 0.875rem;
}

:deep(.info-row i) {
  color: var(--primary-color);
  width: 20px;
}

:deep(.info-row label) {
  font-weight: 600;
  color: var(--text-color-secondary);
  min-width: 100px;
}

:deep(.popup-actions) {
  padding: 1rem;
  border-top: 1px solid var(--surface-border);
  display: flex;
  justify-content: center;
}

:deep(.popup-actions .p-button) {
  width: 100%;
  justify-content: center;
  gap: 0.5rem;
}

/* Button styles */
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
</style>