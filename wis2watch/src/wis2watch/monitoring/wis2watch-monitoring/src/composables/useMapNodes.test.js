import {beforeEach, describe, expect, it, vi} from 'vitest'

import {useMapNodes} from './useMapNodes.js'

/** A centre, as `/api/nodes/` lists it. */
const centre = (overrides = {}) => ({
    id: 1,
    name: 'Kenya Meteorological Department',
    country: 'Kenya',
    country_code: 'KE',
    centre_id: 'ke-kmd',
    status: 'active',
    broker_host: '',
    broker_port: null,
    center_point: [37.9, 0.5],
    ...overrides,
})

/**
 * One entry of what the ingest feed sends as its status: a connection, keyed
 * on the message source it belongs to, carrying no node of any kind.
 */
const connection = (overrides = {}) => ({
    source_id: 10,
    name: 'Meteo-France Global Broker',
    source_type: 'global_broker',
    centre_id: 'fr-meteofrance-global-broker',
    is_reachable: true,
    last_connected_at: '2026-08-25T09:00:00+00:00',
    last_error: '',
    ...overrides,
})

/** The feed sends a dict keyed by source id, not a list. */
const statusFeed = (...connections) =>
    Object.fromEntries(connections.map(c => [String(c.source_id), c]))

describe('the connections the feed reports', () => {
    it('are read out of a payload keyed on the message source', () => {
        const map = useMapNodes()

        map.updateConnections(statusFeed(connection()))

        expect(map.connections.value).toHaveLength(1)
        expect(map.connections.value[0].source_id).toBe(10)
    })

    it('survive a feed with nothing to report', () => {
        const map = useMapNodes()

        map.updateConnections(statusFeed(connection()))
        map.updateConnections(null)

        expect(map.connections.value).toEqual([])
    })

    it('are counted by reachability, with asked and unasked kept apart', () => {
        const map = useMapNodes()

        map.updateConnections(statusFeed(
            connection({source_id: 1, is_reachable: true}),
            connection({source_id: 2, is_reachable: false}),
            connection({source_id: 3, is_reachable: null}),
        ))

        expect(map.stats.value.totalConnections).toBe(3)
        expect(map.stats.value.reachableConnections).toBe(1)
        expect(map.stats.value.unreachableConnections).toBe(1)
    })

    it('include a Global Broker, which is nobody on the map', () => {
        const map = useMapNodes()

        map.allNodes.value = [centre()]
        map.updateConnections(statusFeed(connection()))

        expect(map.stats.value.totalConnections).toBe(1)
        expect(map.centres.value[0].reachability).toBe('undialled')
    })
})

describe('how a centre is being watched', () => {
    let map

    beforeEach(() => {
        map = useMapNodes()
        map.allNodes.value = [centre()]
    })

    const watchedBy = (overrides) =>
        map.updateConnections(statusFeed(connection({
            source_id: 20,
            name: 'Kenya Met origin broker',
            source_type: 'origin_broker',
            centre_id: 'ke-kmd',
            ...overrides,
        })))

    it('is reachable when its own connection answers', () => {
        watchedBy({is_reachable: true})

        expect(map.centres.value[0].reachability).toBe('reachable')
    })

    it('is unreachable when its own connection does not answer', () => {
        watchedBy({is_reachable: false})

        expect(map.centres.value[0].reachability).toBe('unreachable')
    })

    it('is unasked while nothing has tried it yet', () => {
        watchedBy({is_reachable: null})

        expect(map.centres.value[0].reachability).toBe('unasked')
    })

    it('is undialled when nothing connects to it at all', () => {
        map.updateConnections({})

        expect(map.centres.value[0].reachability).toBe('undialled')
    })

    it('carries the connections that name it, and no others', () => {
        map.updateConnections(statusFeed(
            connection({source_id: 20, centre_id: 'ke-kmd', source_type: 'origin_broker'}),
            connection({source_id: 21, centre_id: 'br-inmet-global-broker'}),
        ))

        const [watched] = map.centres.value

        expect(watched.connections).toHaveLength(1)
        expect(watched.connections[0].source_id).toBe(20)
    })

    it('is matched on the centre it names, never on a database id', () => {
        // Source ids and node ids count different things and overlap freely.
        // Merging on either lines rows up by accident.
        map.allNodes.value = [centre({id: 10, centre_id: 'ke-kmd'})]
        watchedBy({source_id: 10, centre_id: 'fr-meteofrance-global-broker'})

        expect(map.centres.value[0].reachability).toBe('undialled')
    })

    it('reports the worst of several connections, so a failure is not hidden', () => {
        map.updateConnections(statusFeed(
            connection({source_id: 20, centre_id: 'ke-kmd', is_reachable: true}),
            connection({source_id: 21, centre_id: 'ke-kmd', is_reachable: false}),
        ))

        expect(map.centres.value[0].reachability).toBe('unreachable')
    })
})

describe('the centres offered to the map', () => {
    let map

    beforeEach(() => {
        map = useMapNodes()
        map.allNodes.value = [
            centre({id: 1, centre_id: 'ke-kmd', country_code: 'KE'}),
            centre({id: 2, centre_id: 'ug-unma', name: 'UNMA', country_code: 'UG', center_point: [32.3, 1.3]}),
            centre({id: 3, centre_id: 'ke-icpac', name: 'ICPAC', country_code: 'KE'}),
        ]
        map.updateConnections(statusFeed(
            connection({source_id: 20, centre_id: 'ke-kmd', is_reachable: true}),
            connection({source_id: 21, centre_id: 'ug-unma', is_reachable: false}),
        ))
    })

    it('are grouped by country, which is where their markers go', () => {
        expect(Object.keys(map.centresByCountry.value).sort()).toEqual(['KE', 'UG'])
        expect(map.centresByCountry.value.KE).toHaveLength(2)
    })

    it('give each country the point its markers are placed at', () => {
        expect(map.countryCoordinates.value.UG).toEqual([32.3, 1.3])
    })

    it('are filtered by how they are watched', () => {
        map.setFilter('reachable')
        expect(map.filteredCentres.value.map(c => c.centre_id)).toEqual(['ke-kmd'])

        map.setFilter('unreachable')
        expect(map.filteredCentres.value.map(c => c.centre_id)).toEqual(['ug-unma'])

        map.setFilter('undialled')
        expect(map.filteredCentres.value.map(c => c.centre_id)).toEqual(['ke-icpac'])

        map.setFilter('all')
        expect(map.filteredCentres.value).toHaveLength(3)
    })

    it('narrow the map with the listing, so the two never disagree', () => {
        map.setFilter('unreachable')

        expect(Object.keys(map.centresByCountry.value)).toEqual(['UG'])
        expect(map.countryCoordinates.value.KE).toBeUndefined()
    })

    it('count themselves whole, however the listing is filtered', () => {
        map.setFilter('reachable')

        expect(map.stats.value.totalCentres).toBe(3)
    })

    it('can be found by the centre a message names', () => {
        expect(map.centreInfo('ug-unma').name).toBe('UNMA')
        expect(map.centreInfo('ug-unma').reachability).toBe('unreachable')
        expect(map.centreInfo('nothing-here')).toBeNull()
    })
})

describe('fetching the centres', () => {
    beforeEach(() => {
        vi.restoreAllMocks()
    })

    it('asks the path it was handed rather than one of its own making', async () => {
        const fetch = vi.fn().mockResolvedValue({json: async () => [centre()]})
        vi.stubGlobal('fetch', fetch)

        const map = useMapNodes()
        await map.fetchNodes('https://example.int/api/nodes/')

        expect(fetch).toHaveBeenCalledWith('https://example.int/api/nodes/')
        expect(map.allNodes.value).toHaveLength(1)
    })

    it('leaves the map empty rather than half drawn when the listing fails', async () => {
        vi.stubGlobal('fetch', vi.fn().mockRejectedValue(new Error('offline')))
        vi.spyOn(console, 'error').mockImplementation(() => {
        })

        const map = useMapNodes()
        await map.fetchNodes('/api/nodes/')

        expect(map.allNodes.value).toEqual([])
    })
})
