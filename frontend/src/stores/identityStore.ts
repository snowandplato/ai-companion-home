import { create } from 'zustand'
import { getBaseUrl } from '../services/api'

export interface AppIdentity {
  assistant_name: string
  user_name: string
}

const defaults: AppIdentity = { assistant_name: 'Mav', user_name: '岚' }

interface IdentityState extends AppIdentity {
  loaded: boolean
  load: () => Promise<void>
  save: (identity: AppIdentity) => Promise<void>
}

export const identityStore = create<IdentityState>((set) => ({
  ...defaults,
  loaded: false,
  load: async () => {
    const baseUrl = await getBaseUrl()
    const res = await fetch(`${baseUrl}/api/identity`, { cache: 'no-store' })
    if (!res.ok) throw new Error('读取称呼失败')
    set({ ...(await res.json() as AppIdentity), loaded: true })
  },
  save: async (identity) => {
    const baseUrl = await getBaseUrl()
    const res = await fetch(`${baseUrl}/api/identity`, {
      method: 'PUT', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(identity)
    })
    if (!res.ok) {
      const body = await res.json().catch(() => null)
      throw new Error(body?.detail || '保存称呼失败')
    }
    set({ ...(await res.json() as AppIdentity), loaded: true })
  }
}))

export function useIdentityLabel() {
  const assistant = identityStore((state) => state.assistant_name)
  const user = identityStore((state) => state.user_name)
  return (template: string) => template
    .replaceAll('Maverick', '\u0000ASSISTANT\u0000')
    .replaceAll('Mav', '\u0000ASSISTANT\u0000')
    .replaceAll('岚', '\u0000USER\u0000')
    .replaceAll('\u0000ASSISTANT\u0000', assistant)
    .replaceAll('\u0000USER\u0000', user)
}
