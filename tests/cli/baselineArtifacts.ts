import { existsSync, readFileSync, writeFileSync } from 'node:fs'
import { join } from 'node:path'

/** Both receipt and copied process files cross the existing CI-upload boundary. */
export function redactDiagnostic(text: string): string {
  return text
    .replace(/([?&](?:t|token|access_token)=)[^\s"'\\&<>}]+/g, '$1<REDACTED>')
    .replace(/(Bearer\s+)[^\s"'\\<>]+/gi, '$1<REDACTED>')
    .replace(/((?:Cookie|Set-Cookie):\s*)[^\r\n"\\]+/gi, '$1<REDACTED>')
    .replace(/("(?:access_token|refresh_token|accessToken|refreshToken|password)"\s*:\s*")[^"<>]*(")/gi, '$1<REDACTED>$2')
}

/** A rescue failure must not interrupt persistence or the next repetition. */
export function saveTrialArtifacts(
  root: string, prefix: string, receipt: { passed: boolean; failure: string | null },
  log: string, rescue?: () => void,
): string | null {
  let rescueError: string | null = null
  if (rescue) {
    receipt.passed = false
    rescueError = 'identity rescue required (trial remains failed)'
    try {
      rescue()
    } catch (error) {
      rescueError = `identity rescue failed: ${error instanceof Error ? error.message : String(error)}`
    }
    receipt.failure = `${receipt.failure ?? ''}; ${rescueError}`
  }
  writeFileSync(`${prefix}.json`, redactDiagnostic(JSON.stringify(receipt, null, 2)))
  writeFileSync(`${prefix}.log`, redactDiagnostic(log))
  for (const name of [
    'resources.jsonl', 'identity.json', 'stores.json', 'refusals.jsonl',
    'first-identity.json', 'first-backend.log', 'first-scenario.json', 'first-trace.jsonl',
    'restart-identity.json', 'restart-backend.log', 'restart-scenario.json', 'restart-trace.jsonl',
  ]) {
    const source = join(root, name)
    if (existsSync(source)) writeFileSync(`${prefix}-${name}`, redactDiagnostic(readFileSync(source, 'utf8')))
  }
  return rescueError && redactDiagnostic(rescueError)
}
