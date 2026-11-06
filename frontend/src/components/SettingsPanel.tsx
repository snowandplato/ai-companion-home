import { useEffect, useState } from 'react'
import type { CSSProperties } from 'react'
import { Icon } from './Icon'
import { listProviderModels } from '../services/api'
import { settingsStore, type ApiProvider, type SavedApiAccount } from '../stores/settingsStore'
import { DEFAULT_API_BASE_URL } from '../config'
import { identityStore, useIdentityLabel } from '../stores/identityStore'

interface SettingsPanelProps {
  isOpen: boolean
  onClose: () => void
}

type ModelLoadState = 'idle' | 'loading' | 'success' | 'error'

const providerNames: Record<ApiProvider, string> = {
  anthropic: 'Anthropic',
  openai: 'OpenAI',
  zenmux: 'ZenMux',
  unknown: '等待识别'
}

function detectProvider(apiKey: string): ApiProvider {
  const key = apiKey.trim()
  if (key.startsWith('sk-ant-')) return 'anthropic'
  if (key.startsWith('sk-payg-') || key.startsWith('sk-ai-v1-') || key.startsWith('sk-ss-v1-')) return 'zenmux'
  if (key.startsWith('sk-')) return 'openai'
  return 'unknown'
}

function defaultAccountName(provider: ApiProvider, apiKey: string) {
  const suffix = apiKey.trim().slice(-4)
  return suffix ? `${providerNames[provider]} · ••••${suffix}` : providerNames[provider]
}

function createAccountId() {
  if (typeof crypto !== 'undefined' && typeof crypto.randomUUID === 'function') {
    return crypto.randomUUID()
  }
  return `api-${Date.now()}`
}

function buildInitialAccounts(
  accounts: SavedApiAccount[],
  apiKey: string,
  model: string
): SavedApiAccount[] {
  const initialAccounts = [...accounts]
  if (apiKey && !initialAccounts.some((account) => account.apiKey === apiKey)) {
    const provider = detectProvider(apiKey)
    initialAccounts.push({
      id: 'legacy-default',
      name: defaultAccountName(provider, apiKey),
      apiKey,
      provider,
      model,
      models: []
    })
  }
  return initialAccounts
}

const inputStyle: CSSProperties = {
  width: '100%',
  padding: '12px',
  borderRadius: '8px',
  border: '1px solid #d1d5db',
  background: '#f9fafb',
  fontSize: '14px',
  color: '#1f2937',
  outline: 'none',
  boxSizing: 'border-box',
  fontFamily: 'inherit'
}

const smallButtonStyle: CSSProperties = {
  minHeight: '40px',
  padding: '0 12px',
  borderRadius: '8px',
  border: '1px solid #d9d7e1',
  background: '#f8f7fa',
  color: '#615e70',
  fontSize: '13px',
  cursor: 'pointer',
  whiteSpace: 'nowrap'
}

export function SettingsPanel({ isOpen, onClose }: SettingsPanelProps) {
  const label = useIdentityLabel()
  const assistantName = identityStore((state) => state.assistant_name)
  const userName = identityStore((state) => state.user_name)
  const [localAssistantName, setLocalAssistantName] = useState(assistantName)
  const [localUserName, setLocalUserName] = useState(userName)
  const [identitySaving, setIdentitySaving] = useState(false)
  const [identityError, setIdentityError] = useState('')
  const [identitySaved, setIdentitySaved] = useState(false)
  useEffect(() => {
    if (!isOpen) return
    setLocalAssistantName(assistantName)
    setLocalUserName(userName)
    setIdentityError('')
  }, [isOpen, assistantName, userName])

  const saveIdentity = async () => {
    setIdentitySaving(true)
    setIdentityError('')
    setIdentitySaved(false)
    try {
      await identityStore.getState().save({
        assistant_name: localAssistantName.trim(), user_name: localUserName.trim()
      })
      setIdentitySaved(true)
    } catch (error) {
      setIdentityError(error instanceof Error ? error.message : '保存称呼失败')
    } finally {
      setIdentitySaving(false)
    }
  }
  const baseUrl = settingsStore((state) => state.baseUrl)
  const apiKey = settingsStore((state) => state.apiKey)
  const model = settingsStore((state) => state.model)
  const savedApiAccounts = settingsStore((state) => state.savedApiAccounts)
  const activeApiAccountId = settingsStore((state) => state.activeApiAccountId)
  const enablePromptCache = settingsStore((state) => state.enablePromptCache)
  const showMessageTimestamps = settingsStore((state) => state.showMessageTimestamps)
  const showTokenUsage = settingsStore((state) => state.showTokenUsage)
  const showCacheUsage = settingsStore((state) => state.showCacheUsage)
  const assistantMessageStyle = settingsStore((state) => state.assistantMessageStyle)

  const initialAccounts = buildInitialAccounts(savedApiAccounts, apiKey, model)
  const initialAccount = initialAccounts.find((account) => account.id === activeApiAccountId)
    ?? initialAccounts.find((account) => account.apiKey === apiKey)
    ?? initialAccounts[0]

  const [showApiKey, setShowApiKey] = useState(false)
  const [localBaseUrl, setLocalBaseUrl] = useState(baseUrl)
  const [localAccounts, setLocalAccounts] = useState<SavedApiAccount[]>(initialAccounts)
  const [localActiveId, setLocalActiveId] = useState<string>(initialAccount?.id ?? 'new')
  const [localName, setLocalName] = useState(initialAccount?.name ?? '')
  const [localApiKey, setLocalApiKey] = useState(initialAccount?.apiKey ?? apiKey)
  const [localProvider, setLocalProvider] = useState<ApiProvider>(initialAccount?.provider ?? detectProvider(apiKey))
  const [localModel, setLocalModel] = useState(initialAccount?.model ?? model)
  const [availableModels, setAvailableModels] = useState<string[]>(initialAccount?.models ?? [])
  const [manualModel, setManualModel] = useState(Boolean(initialAccount && initialAccount.models.length === 0))
  const [modelLoadState, setModelLoadState] = useState<ModelLoadState>('idle')
  const [modelLoadMessage, setModelLoadMessage] = useState('')
  const [localEnablePromptCache, setLocalEnablePromptCache] = useState(enablePromptCache)
  const [localShowMessageTimestamps, setLocalShowMessageTimestamps] = useState(showMessageTimestamps)
  const [localShowTokenUsage, setLocalShowTokenUsage] = useState(showTokenUsage)
  const [localShowCacheUsage, setLocalShowCacheUsage] = useState(showCacheUsage)
  const [localAssistantMessageStyle, setLocalAssistantMessageStyle] = useState(assistantMessageStyle)
  const cacheTitle = localProvider === 'openai' && /^gpt-(?:5\.6|6)/i.test(localModel)
    ? '30分钟缓存'
    : '一小时缓存'
  const cacheDescription = localProvider === 'openai'
    ? (/^gpt-(?:5\.6|6)/i.test(localModel)
        ? 'OpenAI当前模型使用30分钟缓存；关闭后不设置缓存断点。'
        : '这个OpenAI模型使用服务商自己的缓存规则。')
    : localProvider === 'anthropic'
      ? 'Anthropic官方API使用一小时分层缓存。'
      : 'ZenMux暂不由这个开关控制缓存。'

  const loadModels = async () => {
    const key = localApiKey.trim()
    if (!key) {
      setModelLoadState('error')
      setModelLoadMessage('请先填写 API Key')
      return
    }

    setModelLoadState('loading')
    setModelLoadMessage('正在向服务商读取可用模型…')
    try {
      const result = await listProviderModels(localBaseUrl.trim(), key)
      setLocalProvider(result.provider)
      setAvailableModels(result.models)
      setManualModel(false)
      setLocalModel((current) => result.models.includes(current) ? current : result.models[0])
      setModelLoadState('success')
      setModelLoadMessage(`已识别为 ${providerNames[result.provider]}，读取到 ${result.models.length} 个模型`)
    } catch (error) {
      setAvailableModels([])
      setManualModel(true)
      setModelLoadState('error')
      setModelLoadMessage(error instanceof Error ? error.message : '读取模型列表失败')
    }
  }

  const selectAccount = (accountId: string) => {
    if (accountId === 'new') {
      setLocalActiveId('new')
      setLocalName('')
      setLocalApiKey('')
      setLocalProvider('unknown')
      setLocalModel('')
      setAvailableModels([])
      setManualModel(false)
      setModelLoadState('idle')
      setModelLoadMessage('')
      return
    }

    const account = localAccounts.find((item) => item.id === accountId)
    if (!account) return
    setLocalActiveId(account.id)
    setLocalName(account.name)
    setLocalApiKey(account.apiKey)
    setLocalProvider(account.provider)
    setLocalModel(account.model)
    setAvailableModels(account.models)
    setManualModel(account.models.length === 0)
    setModelLoadState('idle')
    setModelLoadMessage('')
  }

  const deleteCurrentAccount = () => {
    if (localActiveId === 'new') return
    const remaining = localAccounts.filter((account) => account.id !== localActiveId)
    setLocalAccounts(remaining)
    const next = remaining[0]
    if (next) {
      selectAccount(next.id)
    } else {
      selectAccount('new')
    }
  }

  const handleApiKeyChange = (nextKey: string) => {
    setLocalApiKey(nextKey)
    setLocalProvider(detectProvider(nextKey))
    setAvailableModels([])
    setManualModel(false)
    setModelLoadState('idle')
    setModelLoadMessage('')
  }

  const handleSave = () => {
    const trimmedKey = localApiKey.trim()
    const provider = localProvider === 'unknown' ? detectProvider(trimmedKey) : localProvider
    let nextAccounts = [...localAccounts]
    let nextActiveId: string | null = null
    let savedAccount: SavedApiAccount | null = null

    if (trimmedKey) {
      const duplicate = nextAccounts.find((account) => account.apiKey === trimmedKey)
      const accountId = localActiveId !== 'new' ? localActiveId : duplicate?.id ?? createAccountId()
      const account: SavedApiAccount = {
        id: accountId,
        name: localName.trim() || defaultAccountName(provider, trimmedKey),
        apiKey: trimmedKey,
        provider,
        model: localModel.trim(),
        models: availableModels
      }
      nextAccounts = nextAccounts.filter((item) => item.id !== accountId && item.apiKey !== trimmedKey)
      nextAccounts.push(account)
      nextActiveId = accountId
      savedAccount = account
    }

    settingsStore.setState({
      baseUrl: localBaseUrl.trim(),
      apiKey: trimmedKey,
      model: localModel.trim(),
      savedApiAccounts: nextAccounts,
      activeApiAccountId: nextActiveId,
      enablePromptCache: localEnablePromptCache,
      showMessageTimestamps: localShowMessageTimestamps,
      showTokenUsage: localShowTokenUsage,
      showCacheUsage: localShowCacheUsage,
      assistantMessageStyle: localAssistantMessageStyle
    })
    void identityStore.getState().load().catch(console.error)

    // SettingsPanel 关闭时组件本身不会卸载；同步它的草稿状态，确保下次打开时
    // 无需刷新网页也能立刻看到刚保存的账户。
    setLocalAccounts(nextAccounts)
    setLocalActiveId(nextActiveId ?? 'new')
    if (savedAccount) {
      setLocalName(savedAccount.name)
      setLocalApiKey(savedAccount.apiKey)
      setLocalProvider(savedAccount.provider)
      setLocalModel(savedAccount.model)
      setAvailableModels(savedAccount.models)
    }
    onClose()
  }

  if (!isOpen) return null

  return (
    <div style={{
      position: 'fixed', inset: 0, zIndex: 100, display: 'flex', alignItems: 'center',
      justifyContent: 'center', background: 'rgba(0,0,0,0.5)'
    }}>
      <div style={{
        background: '#fff', borderRadius: '16px', width: '100%', maxWidth: '500px',
        maxHeight: 'calc(100dvh - 32px)', overflowY: 'auto', margin: '16px',
        boxShadow: '0 25px 50px -12px rgba(0, 0, 0, 0.25)'
      }}>
        <div style={{
          display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '20px 24px',
          borderBottom: '1px solid #e5e5e5'
        }}>
          <h2 style={{ margin: 0, fontSize: '18px', fontWeight: 600, color: '#1f2937' }}>设置</h2>
          <button type="button" onClick={onClose} aria-label="关闭设置" style={{
            width: '32px', height: '32px', borderRadius: '8px', border: 'none', background: 'transparent',
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280'
          }}>
            <Icon name="close" size={20} />
          </button>
        </div>

        <div style={{ padding: '24px' }}>
          <div style={{ marginBottom: '24px' }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>称呼设置</div>
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.55, marginBottom: '10px' }}>
              保存在服务器，所有设备共用。只改变系统文案和后续提示；历史消息、画像、备忘及后台 System Core 的正文不会被改写。
            </div>
            <div style={{ display: 'flex', gap: '10px', marginBottom: '10px' }}>
              <label style={{ flex: 1, fontSize: '12px', color: '#4b5563' }}>助手称呼
                <input value={localAssistantName} maxLength={32} onChange={e => { setLocalAssistantName(e.target.value); setIdentitySaved(false) }} style={{ ...inputStyle, marginTop: '5px' }} />
              </label>
              <label style={{ flex: 1, fontSize: '12px', color: '#4b5563' }}>用户称呼
                <input value={localUserName} maxLength={32} onChange={e => { setLocalUserName(e.target.value); setIdentitySaved(false) }} style={{ ...inputStyle, marginTop: '5px' }} />
              </label>
            </div>
            <button type="button" onClick={() => void saveIdentity()} disabled={identitySaving || !localAssistantName.trim() || !localUserName.trim()} style={smallButtonStyle}>
              {identitySaving ? '保存中…' : '保存称呼'}
            </button>
            {identityError && <div role="alert" style={{ color: '#b56d67', fontSize: '12px', marginTop: '8px' }}>{identityError}</div>}
            {identitySaved && <div role="status" style={{ color: '#66765E', fontSize: '12px', marginTop: '8px' }}>称呼已保存</div>}
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.5, marginTop: '8px' }}>
              如果后台 System Core 正文写了旧称呼，请到后台自行检查。
            </div>
          </div>
          <div style={{ marginBottom: '20px' }}>
            <label style={{ display: 'block', fontSize: '14px', fontWeight: 500, color: '#374151', marginBottom: '8px' }}>
              API Base URL
            </label>
            <input type="text" placeholder={DEFAULT_API_BASE_URL} value={localBaseUrl}
              onChange={(event) => setLocalBaseUrl(event.target.value)} style={inputStyle} />
            <div style={{ fontSize: '12px', color: '#9ca3af', marginTop: '6px', lineHeight: 1.45 }}>
              这是 M-Home 网关地址，不是模型服务商地址。
            </div>
          </div>

          <div style={{ marginBottom: '20px', paddingTop: '2px' }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>API 账户</div>
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.55, marginBottom: '10px' }}>
              Key 只保存在当前浏览器。新增或修改后，请点击页面底部的“保存”。
            </div>

            <div style={{ display: 'flex', gap: '8px', marginBottom: '12px' }}>
              <select value={localActiveId} onChange={(event) => selectAccount(event.target.value)}
                aria-label="已保存的 API 账户" style={{ ...inputStyle, flex: 1, minWidth: 0 }}>
                {localAccounts.map((account) => (
                  <option key={account.id} value={account.id}>{account.name} · {providerNames[account.provider]}</option>
                ))}
                <option value="new">＋ 添加新账户</option>
              </select>
              <button type="button" onClick={() => selectAccount('new')} style={smallButtonStyle}>新增</button>
              {localActiveId !== 'new' && (
                <button type="button" onClick={deleteCurrentAccount}
                  style={{ ...smallButtonStyle, color: '#a45f5f' }}>删除</button>
              )}
            </div>

            <label style={{ display: 'block', fontSize: '13px', fontWeight: 500, color: '#4b5563', marginBottom: '7px' }}>
              账户名称
            </label>
            <input type="text" placeholder="留空会自动命名" value={localName}
              onChange={(event) => setLocalName(event.target.value)} style={{ ...inputStyle, marginBottom: '12px' }} />

            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '7px' }}>
              <label style={{ fontSize: '13px', fontWeight: 500, color: '#4b5563' }}>API Key</label>
              <span style={{
                padding: '3px 8px', borderRadius: '999px',
                background: localProvider === 'unknown' ? '#f3f4f6' : '#eeedf3',
                color: localProvider === 'unknown' ? '#8b8f97' : '#686477', fontSize: '11px', fontWeight: 600
              }}>{providerNames[localProvider]}</span>
            </div>
            <div style={{ position: 'relative' }}>
              <input type={showApiKey ? 'text' : 'password'} placeholder="sk-..." value={localApiKey}
                onChange={(event) => handleApiKeyChange(event.target.value)}
                onBlur={() => {
                  if (localApiKey.trim() && availableModels.length === 0 && modelLoadState !== 'loading') void loadModels()
                }}
                style={{ ...inputStyle, paddingRight: '42px' }} />
              <button type="button" onClick={() => setShowApiKey(!showApiKey)}
                aria-label={showApiKey ? '隐藏 API Key' : '显示 API Key'} style={{
                  position: 'absolute', right: '8px', top: '50%', transform: 'translateY(-50%)', width: '28px',
                  height: '28px', borderRadius: '6px', border: 'none', background: 'transparent', cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center', color: '#6b7280'
                }}>
                <Icon name={showApiKey ? 'eyeOff' : 'eye'} size={16} />
              </button>
            </div>
          </div>

          <div style={{ marginBottom: '24px' }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: '8px' }}>
              <label style={{ fontSize: '14px', fontWeight: 500, color: '#374151' }}>Model</label>
              <button type="button" onClick={() => void loadModels()} disabled={modelLoadState === 'loading'} style={{
                border: 'none', background: 'transparent', color: '#716E85', fontSize: '12px',
                cursor: modelLoadState === 'loading' ? 'wait' : 'pointer', padding: '3px 0'
              }}>{modelLoadState === 'loading' ? '读取中…' : '刷新模型列表'}</button>
            </div>

            {!manualModel && availableModels.length > 0 ? (
              <select value={localModel} onChange={(event) => setLocalModel(event.target.value)} style={inputStyle}>
                {availableModels.map((modelId) => <option key={modelId} value={modelId}>{modelId}</option>)}
              </select>
            ) : (
              <input type="text" placeholder="读取失败时可手动填写模型名" value={localModel}
                onChange={(event) => setLocalModel(event.target.value)} style={inputStyle} />
            )}

            {modelLoadMessage && (
              <div style={{
                fontSize: '12px', color: modelLoadState === 'error' ? '#b56d67' : '#7e7a8c',
                marginTop: '7px', lineHeight: 1.45
              }}>{modelLoadMessage}</div>
            )}
            {availableModels.length > 0 && (
              <button type="button" onClick={() => setManualModel((current) => !current)} style={{
                border: 'none', background: 'transparent', color: '#9a97a5', fontSize: '12px',
                cursor: 'pointer', padding: '7px 0 0'
              }}>{manualModel ? '返回模型列表' : '手动填写模型名'}</button>
            )}
          </div>

          <div style={{ marginBottom: '24px', paddingTop: '2px' }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>{label('Mav 的回复样式')}</div>
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.55, marginBottom: '10px' }}>
              气泡模式按自然段轻轻出现；切换只改变显示，不改变对话记录。
            </div>
            <div role="group" aria-label={label('Mav 的回复样式')} style={{ display: 'flex', gap: '8px' }}>
              {([
                { value: 'prose', label: '文段' },
                { value: 'bubbles', label: '气泡' }
              ] as const).map((option) => (
                <button key={option.value} type="button"
                  aria-pressed={localAssistantMessageStyle === option.value}
                  onClick={() => setLocalAssistantMessageStyle(option.value)}
                  style={{
                    flex: 1, padding: '10px 12px', borderRadius: '11px', cursor: 'pointer',
                    border: localAssistantMessageStyle === option.value ? '1px solid #a89ab8' : '1px solid #e5e7eb',
                    background: localAssistantMessageStyle === option.value ? '#f1edf5' : '#fafafa',
                    color: '#514a5d', fontSize: '13px', fontWeight: localAssistantMessageStyle === option.value ? 600 : 400
                  }}>
                  {option.label}
                </button>
              ))}
            </div>
          </div>

          <div style={{ marginBottom: '24px', paddingTop: '2px' }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>对话缓存</div>
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.55, marginBottom: '10px' }}>
              {cacheDescription}
            </div>
            <div style={{
              minHeight: '64px', padding: '11px 12px', boxSizing: 'border-box', display: 'flex',
              alignItems: 'center', gap: '14px', border: '1px solid #e5e7eb', borderRadius: '12px',
              background: '#fafafa'
            }}>
              <div style={{ flex: 1, minWidth: 0 }}>
                <div style={{ fontSize: '14px', fontWeight: 500, color: '#374151', marginBottom: '3px' }}>
                  {cacheTitle}
                </div>
                <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.4 }}>
                  {localEnablePromptCache
                    ? '已开启；适合接下来会持续聊天的时候。'
                    : '已关闭；新消息会按普通请求发送。'}
                </div>
              </div>
              <button type="button" role="switch" aria-checked={localEnablePromptCache}
                aria-label={`${cacheTitle}${localEnablePromptCache ? '已开启' : '已关闭'}`}
                onClick={() => setLocalEnablePromptCache((current) => !current)} style={{
                  width: '42px', height: '24px', padding: '2px', border: 'none', borderRadius: '999px',
                  background: localEnablePromptCache ? '#716E85' : '#d1d5db', cursor: 'pointer',
                  transition: 'background-color 0.18s ease', flexShrink: 0
                }}>
                <span style={{
                  display: 'block', width: '20px', height: '20px', borderRadius: '50%', background: '#fff',
                  boxShadow: '0 1px 3px rgba(31, 41, 55, 0.25)',
                  transform: localEnablePromptCache ? 'translateX(18px)' : 'translateX(0)',
                  transition: 'transform 0.18s ease'
                }} />
              </button>
            </div>
          </div>

          <div style={{ marginBottom: '24px', paddingTop: '2px' }}>
            <div style={{ fontSize: '14px', fontWeight: 600, color: '#374151', marginBottom: '4px' }}>消息下方信息</div>
            <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.55, marginBottom: '10px' }}>
              只改变显示方式，不影响消息保存与用量统计。
            </div>
            <div style={{ border: '1px solid #e5e7eb', borderRadius: '12px', overflow: 'hidden', background: '#fafafa' }}>
              {[
                { label: '消息时间', description: '显示用户消息发送时间', value: localShowMessageTimestamps, onChange: setLocalShowMessageTimestamps },
                { label: 'Token消耗', description: '显示每轮总token用量', value: localShowTokenUsage, onChange: setLocalShowTokenUsage },
                { label: '缓存信息', description: '显示缓存命中与缓存写入', value: localShowCacheUsage, onChange: setLocalShowCacheUsage }
              ].map((item, index) => (
                <div key={item.label} style={{
                  minHeight: '58px', padding: '10px 12px', boxSizing: 'border-box', display: 'flex',
                  alignItems: 'center', gap: '14px', borderTop: index ? '1px solid #ececf0' : 'none'
                }}>
                  <div style={{ flex: 1, minWidth: 0 }}>
                    <div style={{ fontSize: '14px', fontWeight: 500, color: '#374151', marginBottom: '2px' }}>{item.label}</div>
                    <div style={{ fontSize: '12px', color: '#9ca3af', lineHeight: 1.4 }}>{item.description}</div>
                  </div>
                  <button type="button" role="switch" aria-checked={item.value}
                    aria-label={`${item.label}${item.value ? '已开启' : '已关闭'}`}
                    onClick={() => item.onChange(!item.value)} style={{
                      width: '42px', height: '24px', padding: '2px', border: 'none', borderRadius: '999px',
                      background: item.value ? '#716E85' : '#d1d5db', cursor: 'pointer',
                      transition: 'background-color 0.18s ease', flexShrink: 0
                    }}>
                    <span style={{
                      display: 'block', width: '20px', height: '20px', borderRadius: '50%', background: '#fff',
                      boxShadow: '0 1px 3px rgba(31, 41, 55, 0.25)',
                      transform: item.value ? 'translateX(18px)' : 'translateX(0)', transition: 'transform 0.18s ease'
                    }} />
                  </button>
                </div>
              ))}
            </div>
          </div>

          <button type="button" onClick={handleSave} style={{
            width: '100%', padding: '12px', borderRadius: '8px', border: 'none', background: '#716E85',
            color: '#fff', fontSize: '14px', fontWeight: 500, cursor: 'pointer', transition: 'background-color 0.2s'
          }}>保存</button>
        </div>
      </div>
    </div>
  )
}
