import { rm } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(scriptDir, '..')
const outputDirectory = resolve(projectRoot, 'dist-electron')

if (dirname(outputDirectory) !== projectRoot) {
  throw new Error(`Refusing to clean unexpected path: ${outputDirectory}`)
}

await rm(outputDirectory, { recursive: true, force: true })
console.log('Cleaned generated Electron output.')
