import { access, readFile } from 'node:fs/promises'
import { dirname, resolve } from 'node:path'
import { fileURLToPath } from 'node:url'

const scriptDir = dirname(fileURLToPath(import.meta.url))
const projectRoot = resolve(scriptDir, '..')
const distRoot = resolve(projectRoot, 'dist')
const indexPath = resolve(distRoot, 'index.html')
const html = await readFile(indexPath, 'utf8')
const references = [...html.matchAll(/(?:src|href)="([^"]+)"/g)].map((match) => match[1])
const localAssets = references.filter((reference) => reference.includes('assets/'))

if (localAssets.length === 0) {
  throw new Error('Production index.html does not reference any local assets.')
}

for (const reference of localAssets) {
  if (reference.startsWith('/')) {
    throw new Error(`Electron-incompatible absolute asset URL: ${reference}`)
  }
  const relativePath = reference.replace(/^\.\//, '')
  await access(resolve(distRoot, relativePath))
}

console.log(`Verified ${localAssets.length} file:// compatible production assets.`)
