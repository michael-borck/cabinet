import { describe, expect, it } from 'vitest'
import { cn } from './utils'

describe('cn', () => {
  it('drops falsy values', () => {
    expect(cn('text-sm', undefined, null, false)).toBe('text-sm')
  })

  it('lets later tailwind classes win', () => {
    expect(cn('px-2', 'px-4')).toBe('px-4')
  })
})
