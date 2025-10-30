import {computed, ref} from 'vue'

export function useMapNodes() {
    const allNodes = ref([])
    const monitoredNodes = ref({})
    const nodesByCountry = ref({})
    const countryCoordinates = ref({})
    const selectedNodeId = ref(null)
    const currentFilter = ref('all')

    const stats = computed(() => ({
        totalNodes: allNodes.value.length,
        connectedNodes: Object.values(monitoredNodes.value).filter(n => n.is_connected).length,
        disconnectedNodes: Object.values(monitoredNodes.value).filter(n => !n.is_connected).length,
    }))

    const filteredNodes = computed(() => {
        return allNodes.value
            .map(node => {
                // Merge node data with monitoring status
                const monitored = monitoredNodes.value[node.id]
                return {
                    ...node,
                    ...monitored,
                    is_monitored: !!monitored
                }
            })
            .filter(node => {
                switch (currentFilter.value) {
                    case 'connected':
                        return node.is_monitored && node.is_connected
                    case 'disconnected':
                        return node.is_monitored && !node.is_connected
                    case 'inactive':
                        return !node.is_monitored
                    default:
                        return true
                }
            })
    })

    const fetchNodes = async () => {
        try {
            const response = await fetch('/api/mqtt-nodes/')
            allNodes.value = await response.json()
            processNodes()
        } catch (error) {
            console.error('Error fetching nodes:', error)
        }
    }

    const updateMonitoredNodes = (statusData) => {
        monitoredNodes.value = {}
        if (statusData) {
            const clients = Object.values(statusData)
            clients.forEach(client => {
                monitoredNodes.value[client.node_id] = client
            })
        }

        processNodes()
    }

    const processNodes = () => {
        nodesByCountry.value = {}
        countryCoordinates.value = {}

        allNodes.value.forEach(node => {
            const monitored = monitoredNodes.value[node.id]
            const nodeInfo = {
                ...node,
                ...monitored,
                is_monitored: !!monitored
            }

            // Store country center point
            if (node.center_point && !countryCoordinates.value[node.country_code]) {
                countryCoordinates.value[node.country_code] = node.center_point
            }

            // Group by country
            if (!nodesByCountry.value[node.country_code]) {
                nodesByCountry.value[node.country_code] = []
            }
            nodesByCountry.value[node.country_code].push(nodeInfo)
        })

    }

    const getNodeInfo = (nodeId) => {
        const node = allNodes.value.find(n => n.id === nodeId)
        if (!node) return null

        const monitored = monitoredNodes.value[nodeId]
        return {
            ...node,
            ...monitored,
            is_monitored: !!monitored
        }
    }

    const setFilter = (filter) => {
        currentFilter.value = filter
    }

    const selectNode = (nodeId) => {
        selectedNodeId.value = nodeId
    }

    return {
        allNodes,
        monitoredNodes,
        nodesByCountry,
        countryCoordinates,
        selectedNodeId,
        currentFilter,
        stats,
        filteredNodes,
        fetchNodes,
        updateMonitoredNodes,
        getNodeInfo,
        setFilter,
        selectNode
    }
}