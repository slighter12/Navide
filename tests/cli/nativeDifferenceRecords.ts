/** Test-only live-record readers; never discard a malformed complete record. */
export function differenceReady(output: string): any | undefined {
  const start = output.indexOf('DIFFERENCE_READY ')
  if (start < 0) return undefined
  const end = output.indexOf('\r\n', start)
  if (end < 0) return undefined
  return JSON.parse(output.slice(start + 17, end))
}

export function completeRuntimeReceipts(output: string): any[] {
  const end = output.lastIndexOf('\n')
  if (end < 0) return []
  return output.slice(0, end).split('\n').map((line) => JSON.parse(line))
}
