import { expect, it } from 'vitest'
import { completeRuntimeReceipts, differenceReady } from './nativeDifferenceRecords'

it('native difference complete live records survive fragmentation without filtering', () => {
  const ready = { fixture_marker: 'SYNTHETIC_RECORD_SPLIT', content: 'x'.repeat(5000) }
  const wire = 'DIFFERENCE_READY ' + JSON.stringify(ready) + '\r\n'
  for (const length of [17, 2048, wire.length - 1]) {
    expect(differenceReady(wire.slice(0, length))).toBeUndefined()
  }
  expect(differenceReady(wire)).toEqual(ready)
  expect(() => differenceReady('DIFFERENCE_READY {broken}\r\n')).toThrow(SyntaxError)

  const first = { event: 'ready', synthetic: true }
  const last = { event: 'owned', content: 'x'.repeat(5000) }
  const partial = JSON.stringify(first) + '\n' + JSON.stringify(last).slice(0, 2048)
  expect(completeRuntimeReceipts(partial)).toEqual([first])
  expect(completeRuntimeReceipts(JSON.stringify(last))).toEqual([])
  expect(completeRuntimeReceipts(JSON.stringify(first) + '\n' + JSON.stringify(last) + '\n')).toEqual([first, last])
  expect(completeRuntimeReceipts('{"event":"unrecognized","content":"do not filter"}\n')).toEqual([
    { event: 'unrecognized', content: 'do not filter' },
  ])
  expect(() => completeRuntimeReceipts(JSON.stringify(first) + '\n{broken}\n')).toThrow(SyntaxError)
  expect(() => completeRuntimeReceipts('\n')).toThrow(SyntaxError)
})
