import {computed, ref} from 'vue'

import {worstReachability} from '@/reachability.js'

/**
 * The centres the map draws, and the state of the connections watching them.
 *
 * Two different things arrive here, and they are not the same shape. The
 * centre listing is one row per publishing centre, and a centre is what gets
 * a marker. The ingest feed reports one row per *message source* -- the
 * broker connections the supervisor holds open -- and a connection is not a
 * centre: a single Global Broker carries the whole world's traffic and stands
 * over no country at all.
 *
 * So the two are joined on the centre a source names, which for a centre's
 * own broker is the node it belongs to, and for a Global Broker is the
 * broker's own centre, matching nothing on the map. That is the only field
 * the two payloads have in common that means the same thing in both. Joining
 * on a database id instead lines rows up by coincidence -- which is how the
 * map came to render every centre as not connected while saying nothing was
 * wrong.
 */
export function useMapNodes() {
    const allNodes = ref([])
    const connections = ref([])
    const selectedCentreId = ref(null)
    const currentFilter = ref('all')

    /** The connections that name a centre, gathered under it.
     *
     *  A Global Broker names a centre of its own -- its operator's -- which
     *  no node on the map publishes under, so it gathers under a key nothing
     *  ever looks up. That is correct rather than unfortunate: the Global
     *  Broker is not the centre it is being read for.
     */
    const connectionsByCentre = computed(() => {
        const gathered = {}

        connections.value.forEach((connection) => {
            if (!connection.centre_id) {
                return
            }

            gathered[connection.centre_id] = gathered[connection.centre_id] || []
            gathered[connection.centre_id].push(connection)
        })

        return gathered
    })

    /** Every centre, carrying the connections that watch it. */
    const centres = computed(() =>
        allNodes.value.map((node) => {
            const own = connectionsByCentre.value[node.centre_id] || []

            return {
                ...node,
                connections: own,
                reachability: worstReachability(own),
            }
        })
    )

    const filteredCentres = computed(() => {
        if (currentFilter.value === 'all') {
            return centres.value
        }

        return centres.value.filter(centre => centre.reachability === currentFilter.value)
    })

    /** The centres a marker is drawn for, gathered under the country they
     *  are placed in. Follows the filter, so that narrowing the listing
     *  narrows the map with it rather than leaving the two disagreeing. */
    const centresByCountry = computed(() => {
        const gathered = {}

        filteredCentres.value.forEach((centre) => {
            gathered[centre.country_code] = gathered[centre.country_code] || []
            gathered[centre.country_code].push(centre)
        })

        return gathered
    })

    /** Where each country's markers go. One point per country: a centre is
     *  placed by the country it publishes for, not by an address of its own. */
    const countryCoordinates = computed(() => {
        const points = {}

        filteredCentres.value.forEach((centre) => {
            if (centre.center_point && !points[centre.country_code]) {
                points[centre.country_code] = centre.center_point
            }
        })

        return points
    })

    const stats = computed(() => ({
        totalCentres: centres.value.length,
        totalConnections: connections.value.length,
        reachableConnections: connections.value.filter(c => c.is_reachable === true).length,
        unreachableConnections: connections.value.filter(c => c.is_reachable === false).length,
    }))

    /**
     * Read the feed's status payload, which is keyed by message source id.
     *
     * The ids are thrown away here. They key the payload so that a later
     * update can replace one entry, which this does not do -- every status
     * message is the whole picture -- and keeping them would invite a lookup
     * by a number that means nothing anywhere else on this page.
     */
    const updateConnections = (statusData) => {
        connections.value = statusData ? Object.values(statusData) : []
    }

    /**
     * Load the centre listing.
     *
     * The address is handed in rather than assembled here, because a path
     * spelled out inside a built bundle is a path nobody can rename from the
     * Django side.
     */
    const fetchNodes = async (nodesApiUrl) => {
        try {
            const response = await fetch(nodesApiUrl)
            allNodes.value = await response.json()
        } catch (error) {
            console.error('Error fetching centres:', error)
        }
    }

    /** One centre in full, by the centre id a message or a marker names. */
    const centreInfo = (centreId) =>
        centres.value.find(centre => centre.centre_id === centreId) || null

    const setFilter = (filter) => {
        currentFilter.value = filter
    }

    const selectCentre = (centreId) => {
        selectedCentreId.value = centreId
    }

    return {
        allNodes,
        connections,
        centres,
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
        selectCentre,
    }
}
