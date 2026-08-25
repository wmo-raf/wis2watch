import {describe, expect, it} from 'vitest'

import {formatRelativeTime} from './relative-time.js'

const NOW = new Date('2026-08-25T12:00:00Z').getTime()
const ago = (seconds) => new Date(NOW - seconds * 1000).toISOString()

describe('how long ago something happened', () => {
    it('is nothing at all when it never happened', () => {
        expect(formatRelativeTime(null, NOW)).toBe('Never')
        expect(formatRelativeTime('', NOW)).toBe('Never')
    })

    it('is nothing at all when the timestamp cannot be read', () => {
        expect(formatRelativeTime('the other day', NOW)).toBe('Never')
    })

    it('rounds the last half minute down to just now', () => {
        expect(formatRelativeTime(ago(5), NOW)).toBe('Just now')
        expect(formatRelativeTime(ago(29), NOW)).toBe('Just now')
    })

    it('counts seconds, then minutes, then hours', () => {
        expect(formatRelativeTime(ago(45), NOW)).toBe('45s ago')
        expect(formatRelativeTime(ago(90), NOW)).toBe('1m ago')
        expect(formatRelativeTime(ago(60 * 90), NOW)).toBe('1h ago')
    })

    it('names yesterday rather than counting a day', () => {
        expect(formatRelativeTime(ago(60 * 60 * 30), NOW)).toBe('Yesterday')
        expect(formatRelativeTime(ago(60 * 60 * 24 * 3), NOW)).toBe('3 days ago')
    })

    it('gives up on words after a week and prints the date', () => {
        expect(formatRelativeTime(ago(60 * 60 * 24 * 30), NOW)).toMatch(/\d/)
        expect(formatRelativeTime(ago(60 * 60 * 24 * 30), NOW)).not.toMatch(/ago/)
    })
})
