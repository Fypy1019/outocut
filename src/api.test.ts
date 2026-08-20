import { beforeEach, describe, expect, it, vi } from 'vitest'
import { api, resetConnection } from './api'

describe('API client', () => {
  beforeEach(() => {
    resetConnection()
    vi.restoreAllMocks()
    vi.stubGlobal('window', {
      outocut: {
        engineConnection: vi.fn().mockResolvedValue({
          baseUrl: 'http://127.0.0.1:43210',
          token: 'session-token',
          ready: true,
        }),
      },
    })
  })

  it('adds the local session token', async () => {
    const fetchMock = vi.fn().mockResolvedValue(
      new Response(JSON.stringify({ status: 'ok' }), {
        status: 200,
        headers: { 'Content-Type': 'application/json' },
      }),
    )
    vi.stubGlobal('fetch', fetchMock)
    await expect(api('/health')).resolves.toEqual({ status: 'ok' })
    expect(fetchMock).toHaveBeenCalledWith(
      'http://127.0.0.1:43210/health',
      expect.objectContaining({ headers: expect.objectContaining({ Authorization: 'Bearer session-token' }) }),
    )
  })

  it('maps structured engine errors', async () => {
    vi.stubGlobal(
      'fetch',
      vi.fn().mockResolvedValue(
        new Response(JSON.stringify({ code: 'rate_limited', message: '请求过于频繁' }), {
          status: 429,
          headers: { 'Content-Type': 'application/json' },
        }),
      ),
    )
    await expect(api('/ai/rewrite')).rejects.toEqual(
      expect.objectContaining({ code: 'rate_limited', status: 429, message: '请求过于频繁' }),
    )
  })
})
