import { describe, expect, it } from 'vitest'
import { formatSize } from './format'

describe('formatSize', () => {
  it('formats bytes without a decimal', () => {
    expect(formatSize(0)).toBe('0 B')
    expect(formatSize(1023)).toBe('1023 B')
  })

  it('formats KB, MB, and GB with one decimal', () => {
    expect(formatSize(1024)).toBe('1.0 KB')
    expect(formatSize(1024 * 1024)).toBe('1.0 MB')
    expect(formatSize(1024 * 1024 * 1024)).toBe('1.0 GB')
  })
})
