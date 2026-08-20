let connection: EngineConnection | null = null

export class ApiClientError extends Error {
  constructor(
    message: string,
    public readonly status: number,
    public readonly code = 'request_failed',
  ) {
    super(message)
  }
}

async function getConnection(): Promise<EngineConnection> {
  if (connection) return connection
  if (window.outocut) {
    connection = await window.outocut.engineConnection()
  } else {
    connection = { baseUrl: 'http://127.0.0.1:35006', token: 'development-token', ready: true }
  }
  return connection
}

export async function api<T>(path: string, init: RequestInit = {}): Promise<T> {
  const current = await getConnection()
  let response: Response
  try {
    response = await fetch(`${current.baseUrl}${path}`, {
      ...init,
      headers: {
        Authorization: `Bearer ${current.token}`,
        ...(init.body ? { 'Content-Type': 'application/json' } : {}),
        ...init.headers,
      },
    })
  } catch (error) {
    const reason = error instanceof Error ? error.message : String(error)
    console.error(`[api] 网络请求失败 ${path}：${reason}`)
    throw new ApiClientError(`网络请求失败：${reason}`, 0)
  }
  if (!response.ok) {
    const payload = await response.json().catch(() => ({}))
    const message = payload.message || payload.detail || `请求失败 (${response.status})`
    console.error(`[api] 请求失败 ${response.status} ${path}：${message}`)
    throw new ApiClientError(message, response.status, payload.code)
  }
  return response.status === 204 ? (undefined as T) : response.json()
}

export const post = <T>(path: string, body?: unknown) =>
  api<T>(path, { method: 'POST', body: body === undefined ? undefined : JSON.stringify(body) })

export const put = <T>(path: string, body: unknown) =>
  api<T>(path, { method: 'PUT', body: JSON.stringify(body) })

export const remove = <T>(path: string) => api<T>(path, { method: 'DELETE' })

export function resetConnection(): void {
  connection = null
}
