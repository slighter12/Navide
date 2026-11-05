export type RuntimePlatform = 'darwin' | 'linux' | 'win32'
export function stagedRuntimeProblem(file: string, platform: RuntimePlatform, arch: 'arm64' | 'x64' | 'universal'): string | null
export function stagedRuntimePath(platform: RuntimePlatform, root?: string): string
export default function afterPack(context: {
  electronPlatformName: string
  arch: number
  appOutDir: string
  packager: { appInfo: { productFilename: string } }
}): void
