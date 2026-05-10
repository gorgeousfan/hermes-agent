import { mkdtempSync, readdirSync, readFileSync } from 'node:fs'
import { tmpdir } from 'node:os'
import { join } from 'node:path'
import { Readable } from 'node:stream'

import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest'

vi.mock('node:v8', () => ({
  getHeapSnapshot: () => Readable.from(['fake heap snapshot']),
  getHeapSpaceStatistics: () => [],
  getHeapStatistics: () => ({
    heap_size_limit: 1024,
    malloced_memory: 0,
    number_of_detached_contexts: 0,
    number_of_native_contexts: 1,
    peak_malloced_memory: 0
  })
}))

import { performHeapDump } from './memory.js'

describe('performHeapDump', () => {
  const originalAuto = process.env.HERMES_AUTO_HEAPDUMP
  const originalDir = process.env.HERMES_HEAPDUMP_DIR
  let dir: string

  beforeEach(() => {
    dir = mkdtempSync(join(tmpdir(), 'hermes-heapdump-test-'))
    process.env.HERMES_HEAPDUMP_DIR = dir
    delete process.env.HERMES_AUTO_HEAPDUMP
  })

  afterEach(() => {
    vi.restoreAllMocks()

    if (originalAuto === undefined) {
      delete process.env.HERMES_AUTO_HEAPDUMP
    } else {
      process.env.HERMES_AUTO_HEAPDUMP = originalAuto
    }

    if (originalDir === undefined) {
      delete process.env.HERMES_HEAPDUMP_DIR
    } else {
      process.env.HERMES_HEAPDUMP_DIR = originalDir
    }
  })

  it('writes diagnostics only for automatic triggers by default', async () => {
    const result = await performHeapDump('auto-high')
    const files = readdirSync(dir)

    expect(result.success).toBe(true)
    expect(result.heapPath).toBeUndefined()
    expect(result.diagPath).toMatch(/\.diagnostics\.json$/)
    expect(files).toHaveLength(1)
    expect(files[0]).toMatch(/auto-high/)
    expect(files[0]).toMatch(/\.diagnostics\.json$/)
  })

  it('allows automatic heap snapshots when HERMES_AUTO_HEAPDUMP is enabled', async () => {
    process.env.HERMES_AUTO_HEAPDUMP = '1'

    const result = await performHeapDump('auto-critical')
    const files = readdirSync(dir).sort()

    expect(result.success).toBe(true)
    expect(result.diagPath).toMatch(/\.diagnostics\.json$/)
    expect(result.heapPath).toMatch(/\.heapsnapshot$/)
    expect(files).toHaveLength(2)
    expect(files.some(file => file.endsWith('.diagnostics.json'))).toBe(true)
    expect(files.some(file => file.endsWith('.heapsnapshot'))).toBe(true)
  })

  it('keeps manual heap dumps unchanged', async () => {
    const result = await performHeapDump('manual')

    expect(result.success).toBe(true)
    expect(result.diagPath).toMatch(/\.diagnostics\.json$/)
    expect(result.heapPath).toMatch(/\.heapsnapshot$/)
    expect(readFileSync(result.heapPath!, 'utf8')).toBe('fake heap snapshot')
  })
})
