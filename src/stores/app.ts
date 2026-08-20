import { defineStore } from 'pinia'
import { api } from '@/api'
import type { Health } from '@/types'

export const useAppStore = defineStore('app', {
  state: () => ({
    health: null as Health | null,
    loading: false,
    error: '',
  }),
  getters: {
    online: (state) => state.health?.status === 'ok',
  },
  actions: {
    async refreshHealth() {
      this.loading = true
      try {
        this.health = await api<Health>('/health')
        this.error = ''
      } catch (error) {
        this.health = null
        this.error = error instanceof Error ? error.message : String(error)
      } finally {
        this.loading = false
      }
    },
  },
})
