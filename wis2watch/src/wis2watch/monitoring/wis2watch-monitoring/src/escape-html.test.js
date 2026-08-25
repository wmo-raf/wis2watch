import {describe, expect, it} from 'vitest'

import {escapeHtml} from './escape-html.js'

describe('text put into markup', () => {
    it('comes through unchanged when it is ordinary', () => {
        expect(escapeHtml('Kenya Meteorological Department')).toBe('Kenya Meteorological Department')
    })

    it('is defused rather than dropped', () => {
        expect(escapeHtml('<script>alert(1)</script>')).toBe(
            '&lt;script&gt;alert(1)&lt;/script&gt;'
        )
    })

    it('is safe in an attribute as well as in text', () => {
        expect(escapeHtml('" onerror="alert(1)')).toBe('&quot; onerror=&quot;alert(1)')
    })

    it('has something to say about nothing', () => {
        expect(escapeHtml(null)).toBe('')
        expect(escapeHtml(undefined)).toBe('')
    })
})
