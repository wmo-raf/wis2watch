import {describe, expect, it} from 'vitest'

import {
    REACHABILITY,
    REACHABILITY_ORDER,
    REACHABLE,
    UNASKED,
    UNDIALLED,
    UNREACHABLE,
    connectionReachability,
    sourceTypeLabel,
    worstReachability,
} from './reachability.js'

describe('the four states a centre can be watched in', () => {
    it('are each given a way of being shown', () => {
        [REACHABLE, UNREACHABLE, UNASKED, UNDIALLED].forEach((state) => {
            expect(REACHABILITY[state]).toMatchObject({
                label: expect.any(String),
                severity: expect.any(String),
                colour: expect.stringMatching(/^#[0-9a-f]{6}$/),
            })
        })
    })

    it('are all in the order the legend draws them, and nothing else is', () => {
        expect(REACHABILITY_ORDER.slice().sort()).toEqual(Object.keys(REACHABILITY).sort())
    })
})

describe('how a centre is watched, over the connections that name it', () => {
    const at = (...states) => states.map(is_reachable => ({is_reachable}))

    it('is undialled when nothing names it', () => {
        expect(worstReachability([])).toBe(UNDIALLED)
    })

    it('takes the state of its one connection', () => {
        expect(worstReachability(at(true))).toBe(REACHABLE)
        expect(worstReachability(at(false))).toBe(UNREACHABLE)
        expect(worstReachability(at(null))).toBe(UNASKED)
    })

    it('reports the worst of several, so a failure is not hidden', () => {
        expect(worstReachability(at(true, false))).toBe(UNREACHABLE)
        expect(worstReachability(at(null, false))).toBe(UNREACHABLE)
        expect(worstReachability(at(true, null))).toBe(REACHABLE)
    })
})

describe('a connection on its own', () => {
    it('is reachable, unreachable or unasked -- never undialled', () => {
        expect(connectionReachability({is_reachable: true})).toBe(REACHABLE)
        expect(connectionReachability({is_reachable: false})).toBe(UNREACHABLE)
        expect(connectionReachability({is_reachable: null})).toBe(UNASKED)
    })

    it('is named by the kind of vantage point it is', () => {
        expect(sourceTypeLabel({source_type: 'global_broker'})).toBe('Global Broker')
        expect(sourceTypeLabel({source_type: 'origin_broker'})).toBe('Origin Broker')
    })

    it('falls back to what the feed called it rather than showing nothing', () => {
        expect(sourceTypeLabel({source_type: 'something_new'})).toBe('something_new')
    })
})
