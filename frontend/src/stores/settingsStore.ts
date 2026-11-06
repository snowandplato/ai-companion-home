import { create } from 'zustand'
import { persist } from 'zustand/middleware'
import { DEFAULT_API_BASE_URL } from '../config'

export type ApiProvider = 'anthropic' | 'openai' | 'zenmux' | 'unknown'

export interface SavedApiAccount {
  id: string
  name: string
  apiKey: string
  provider: ApiProvider
  model: string
  models: string[]
}

interface SettingsState {
  baseUrl: string
  apiKey: string
  model: string
  savedApiAccounts: SavedApiAccount[]
  activeApiAccountId: string | null
  enablePromptCache: boolean
  showMessageTimestamps: boolean
  showTokenUsage: boolean
  showCacheUsage: boolean
  assistantMessageStyle: 'prose' | 'bubbles'
  setBaseUrl: (url: string) => void
  setApiKey: (key: string) => void
  setModel: (model: string) => void
  setShowMessageTimestamps: (show: boolean) => void
  setShowTokenUsage: (show: boolean) => void
  setShowCacheUsage: (show: boolean) => void
  setAssistantMessageStyle: (style: 'prose' | 'bubbles') => void
}

export const settingsStore = create<SettingsState>()(
  persist(
    (set) => ({
      baseUrl: DEFAULT_API_BASE_URL,
      apiKey: '',
      model: 'claude-sonnet-4-20250514',
      savedApiAccounts: [],
      activeApiAccountId: null,
      enablePromptCache: true,
      showMessageTimestamps: true,
      showTokenUsage: true,
      showCacheUsage: true,
      assistantMessageStyle: 'prose',
      setBaseUrl: (url) => set({ baseUrl: url }),
      setApiKey: (key) => set({ apiKey: key }),
      setModel: (model) => set({ model: model }),
      setShowMessageTimestamps: (show) => set({ showMessageTimestamps: show }),
      setShowTokenUsage: (show) => set({ showTokenUsage: show }),
      setShowCacheUsage: (show) => set({ showCacheUsage: show }),
      setAssistantMessageStyle: (style) => set({ assistantMessageStyle: style })
    }),
    {
      name: 'mav-settings-storage'
    }
  )
)
