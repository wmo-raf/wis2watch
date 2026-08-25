import {describe, expect, it} from 'vitest'

import {centrePopupHTML} from './popup-html.js'

const NOW = new Date('2026-08-25T12:00:00Z').getTime()

const centre = (overrides = {}) => ({
    name: 'Kenya Meteorological Department',
    country: 'Kenya',
    centre_id: 'ke-kmd',
    reachability: 'undialled',
    connections: [],
    ...overrides,
})

const connection = (overrides = {}) => ({
    source_id: 20,
    name: 'Kenya Met origin broker',
    source_type: 'origin_broker',
    centre_id: 'ke-kmd',
    is_reachable: true,
    last_connected_at: '2026-08-25T11:00:00Z',
    last_error: '',
    ...overrides,
})

describe('what a marker says when it is opened', () => {
    it('names the centre and where it publishes from', () => {
        const html = centrePopupHTML(centre(), NOW)

        expect(html).toContain('Kenya Meteorological Department')
        expect(html).toContain('Kenya')
        expect(html).toContain('ke-kmd')
    })

    it('says how the centre is being watched', () => {
        const html = centrePopupHTML(centre({
            reachability: 'reachable',
            connections: [connection()],
        }), NOW)

        expect(html).toContain('Kenya Met origin broker')
        expect(html).toContain('Origin Broker')
        expect(html).toContain('last connected 1h ago')
    })

    it('explains a centre nothing dials rather than showing it as broken', () => {
        const html = centrePopupHTML(centre({reachability: 'undialled'}), NOW)

        expect(html).toContain('Global Broker')
        expect(html).not.toContain('Unreachable')
    })

    it('shows why a connection failed, in the words the broker used', () => {
        const html = centrePopupHTML(centre({
            reachability: 'unreachable',
            connections: [connection({is_reachable: false, last_error: 'Connection refused'})],
        }), NOW)

        expect(html).toContain('Unreachable')
        expect(html).toContain('Connection refused')
    })

    it('never lets a catalogue name or a broker error become markup', () => {
        const html = centrePopupHTML(centre({
            name: '<img src=x onerror=alert(1)>',
            reachability: 'unreachable',
            connections: [connection({is_reachable: false, last_error: '</div><script>x</script>'})],
        }), NOW)

        expect(html).not.toContain('<img')
        expect(html).not.toContain('<script>')
        expect(html).toContain('&lt;img')
    })
})
