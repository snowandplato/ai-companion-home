import { DEFAULT_API_BASE_URL } from '../config'

export interface Conversation {
  id: string
  title: string
  created_at: string
  updated_at: string
  last_message_preview: string
  is_folded: number
}

export interface MessageImage {
  id: string
  messageId: string
  sortOrder: number
  mimeType: string
  width?: number | null
  height?: number | null
  visionType?: string
  visionSummary?: string
  visionStatus?: string
  url: string
  thumbUrl: string
}

export interface ImageUpload {
  base64: string
  mimeType: string
  preview: string
}

export type ArtJobStatus = 'drafting' | 'queued' | 'generating' | 'reviewing' | 'revising' | 'completed' | 'declined' | 'rejected' | 'prompt_failed' | 'failed'

export interface ArtJob {
  id: string
  assistantMessageId: string
  status: ArtJobStatus
  attempt: number
  maxAttempts: number
  caption?: string
  error?: string
  mode?: 'studio' | 'native'
  selectedImage?: MessageImage | null
  selectedImages?: MessageImage[]
  finalContent?: string
  updatedAt?: string
}

export interface Message {
  id: string
  role: 'user' | 'assistant'
  content: string
  thinking?: string | null
  model?: string | null
  created_at: string
  imagePreview?: string | null
  images?: MessageImage[]
  artJob?: ArtJob | null
  fileName?: string | null
  inputTokens?: number | null
  outputTokens?: number | null
  totalTokens?: number | null
  cacheCreationTokens?: number | null
  cacheReadTokens?: number | null
  cacheCreation1hTokens?: number | null
}

export interface MavNote {
  id: number
  author: string
  content: string
  created_at: string
}

export interface ListMessagesParams {
  conversationId: string
  limit?: number
  offset?: number
  order?: string
}

export interface ProviderModelsResponse {
  provider: 'anthropic' | 'openai' | 'zenmux'
  models: string[]
}

export async function listProviderModels(baseUrl: string, apiKey: string): Promise<ProviderModelsResponse> {
  const res = await fetch(`${baseUrl.replace(/\/$/, '')}/api/provider-models`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ api_key: apiKey })
  })

  if (!res.ok) {
    let message = `读取模型列表失败（${res.status}）`
    try {
      const data = await res.json()
      if (typeof data?.detail === 'string') message = data.detail
    } catch {
      // 非 JSON 错误响应沿用状态码提示。
    }
    throw new Error(message)
  }

  return res.json()
}


export async function getBaseUrl(): Promise<string> {
  if (typeof window !== 'undefined') {
    const { settingsStore } = await import('../stores/settingsStore')
    return settingsStore.getState().baseUrl
  }
  return DEFAULT_API_BASE_URL
}

export async function getApiKey(): Promise<string> {
  if (typeof window !== 'undefined') {
    const { settingsStore } = await import('../stores/settingsStore')
    return settingsStore.getState().apiKey
  }
  return ''
}

export async function getModel(): Promise<string> {
  if (typeof window !== 'undefined') {
    const { settingsStore } = await import('../stores/settingsStore')
    return settingsStore.getState().model
  }
  return 'claude-sonnet-4-20250514'
}

// 对话 CRUD
export async function listConversations(limit?: number, offset?: number): Promise<Conversation[]> {
  const baseUrl = await getBaseUrl()
  const url = new URL(`${baseUrl}/api/conversations`)
  if (limit !== undefined) url.searchParams.append('limit', limit.toString())
  if (offset !== undefined) url.searchParams.append('offset', offset.toString())

  const res = await fetch(url.toString())
  if (!res.ok) throw new Error(`Failed to fetch conversations: ${res.status}`)

  const data = await res.json()

  // 处理不同的响应格式
  // 格式1: { conversations: [] }
  // 格式2: { data: { conversations: [] } }
  // 格式3: 直接返回数组 [{}, {}]
  // 格式4: { data: [] }
  if (data?.conversations && Array.isArray(data.conversations)) {
    return data.conversations
  } else if (data?.data?.conversations && Array.isArray(data.data.conversations)) {
    return data.data.conversations
  } else if (Array.isArray(data)) {
    return data
  } else if (data?.data && Array.isArray(data.data)) {
    return data.data
  }

  // 如果都不是，返回空数组
  console.warn('Unexpected conversations response format:', data)
  return []
}

export async function createConversation(id: string, title?: string): Promise<{ status: string; id: string }> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ id, title })
  })
  if (!res.ok) throw new Error(`Failed to create conversation: ${res.status}`)
  return res.json()
}

export async function updateConversation(id: string, title: string): Promise<void> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ title })
  })
  if (!res.ok) throw new Error(`Failed to update conversation: ${res.status}`)
}

export async function setConversationFolded(id: string, folded: boolean): Promise<void> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${id}`, {
    method: 'PATCH',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ is_folded: folded })
  })
  if (!res.ok) throw new Error(`Failed to update conversation fold: ${res.status}`)
}

export async function deleteConversation(id: string): Promise<void> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${id}`, {
    method: 'DELETE'
  })
  if (!res.ok) throw new Error(`Failed to delete conversation: ${res.status}`)
}

export async function getConversation(id: string): Promise<Conversation | null> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${id}`)
  if (!res.ok) return null
  return res.json()
}

export interface MessagePage {
  messages: Message[]
  total: number
}

export interface MessageMapItem {
  id: string
  role: 'user' | 'assistant'
  content: string
  thinking?: string | null
  created_at: string
}

// 消息 CRUD
export async function listMessagesPage(conversationId: string, limit?: number, offset?: number, order?: string): Promise<MessagePage> {
  const baseUrl = await getBaseUrl()
  const url = new URL(`${baseUrl}/api/conversations/${conversationId}/messages`)
  if (limit !== undefined) url.searchParams.append('limit', limit.toString())
  if (offset !== undefined) url.searchParams.append('offset', offset.toString())
  if (order !== undefined) url.searchParams.append('order', order)

  const res = await fetch(url.toString(), { cache: 'no-store' })
  if (!res.ok) throw new Error(`Failed to fetch messages: ${res.status}`)

  const data = await res.json()
  const mediaBaseUrl = baseUrl.replace(/\/$/, '')

  // 处理不同的响应格式
  const resolveMediaUrl = (value: string) => {
    if (!value || /^https?:\/\//i.test(value) || value.startsWith('data:')) return value
    return `${mediaBaseUrl}${value.startsWith('/') ? value : `/${value}`}`
  }
  const mapImage = (image: any): MessageImage => ({
    id: image.id,
    messageId: image.message_id ?? image.messageId,
    sortOrder: image.sort_order ?? image.sortOrder ?? 0,
    mimeType: image.mime_type ?? image.mimeType ?? 'image/jpeg',
    width: image.width ?? null,
    height: image.height ?? null,
    visionType: image.vision_type ?? image.visionType ?? '',
    visionSummary: image.vision_summary ?? image.visionSummary ?? '',
    visionStatus: image.vision_status ?? image.visionStatus ?? 'pending',
    url: resolveMediaUrl(image.url),
    thumbUrl: resolveMediaUrl(image.thumb_url ?? image.thumbUrl ?? image.url)
  })
  const mapMessage = (msg: any): Message => ({
    ...msg,
    imagePreview: msg.image_preview ?? msg.imagePreview ?? null,
    images: Array.isArray(msg.images) ? msg.images.map(mapImage) : [],
    artJob: msg.art_job ? {
      id: msg.art_job.id,
      assistantMessageId: msg.art_job.assistant_message_id ?? msg.id,
      status: msg.art_job.status,
      attempt: msg.art_job.attempt ?? 0,
      maxAttempts: msg.art_job.max_attempts ?? 2,
      caption: msg.art_job.caption ?? '',
      error: msg.art_job.error ?? '',
      mode: msg.art_job.mode ?? 'studio',
      selectedImage: msg.art_job.selected_image ? mapImage(msg.art_job.selected_image) : null,
      selectedImages: Array.isArray(msg.art_job.selected_images)
        ? msg.art_job.selected_images.map(mapImage)
        : msg.art_job.selected_image ? [mapImage(msg.art_job.selected_image)] : [],
      finalContent: msg.art_job.final_content ?? msg.content ?? '',
      updatedAt: msg.art_job.updated_at
    } : null,
    fileName: msg.file_name ?? msg.fileName ?? null,
    inputTokens: msg.input_tokens ?? msg.inputTokens ?? null,
    outputTokens: msg.output_tokens ?? msg.outputTokens ?? null,
    totalTokens: msg.total_tokens ?? msg.totalTokens ?? null,
    cacheCreationTokens: msg.cache_creation_tokens ?? msg.cacheCreationTokens ?? null,
    cacheReadTokens: msg.cache_read_tokens ?? msg.cacheReadTokens ?? null,
    cacheCreation1hTokens: msg.cache_creation_1h_tokens ?? msg.cacheCreation1hTokens ?? null
  })

  if (Array.isArray(data)) {
    const messages = data.map(mapMessage)
    return { messages, total: messages.length }
  } else if (data?.messages && Array.isArray(data.messages)) {
    return { messages: data.messages.map(mapMessage), total: data.total ?? data.messages.length }
  } else if (data?.data?.messages && Array.isArray(data.data.messages)) {
    return { messages: data.data.messages.map(mapMessage), total: data.data.total ?? data.total ?? data.data.messages.length }
  } else if (data?.data && Array.isArray(data.data)) {
    const messages = data.data.map(mapMessage)
    return { messages, total: data.total ?? messages.length }
  }

  console.warn('Unexpected messages response format:', data)
  return { messages: [], total: 0 }
}

export async function listMessages(conversationId: string, limit?: number, offset?: number, order?: string): Promise<Message[]> {
  const page = await listMessagesPage(conversationId, limit, offset, order)
  return page.messages
}

export async function getConversationMessageMap(conversationId: string): Promise<MessageMapItem[]> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${conversationId}/message-map`, {
    cache: 'no-store'
  })
  if (!res.ok) throw new Error(`Failed to fetch conversation map: ${res.status}`)
  const data = await res.json()
  return Array.isArray(data?.messages) ? data.messages : []
}

export async function saveMessage(
  conversationId: string,
  message: Message,
  imageUploads: ImageUpload[] = []
): Promise<MessageImage[]> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${conversationId}/messages`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({
      ...message,
      imagePreview: imageUploads.length ? null : message.imagePreview,
      images: imageUploads.map(({ base64, mimeType }) => ({ base64, mimeType }))
    })
  })
  if (!res.ok) throw new Error(`Failed to save message: ${res.status}`)
  const data = await res.json()
  const mediaBaseUrl = baseUrl.replace(/\/$/, '')
  const resolveMediaUrl = (value: string) => {
    if (!value || /^https?:\/\//i.test(value)) return value
    return `${mediaBaseUrl}${value.startsWith('/') ? value : `/${value}`}`
  }
  return (data.images || []).map((image: any) => ({
    id: image.id,
    messageId: image.message_id ?? image.messageId,
    sortOrder: image.sort_order ?? image.sortOrder ?? 0,
    mimeType: image.mime_type ?? image.mimeType ?? 'image/jpeg',
    width: image.width ?? null,
    height: image.height ?? null,
    visionType: image.vision_type ?? '',
    visionSummary: image.vision_summary ?? '',
    visionStatus: image.vision_status ?? 'pending',
    url: resolveMediaUrl(image.url),
    thumbUrl: resolveMediaUrl(image.thumb_url ?? image.url)
  }))
}

export async function deleteMessagesAfter(conversationId: string, messageId: string, includeAnchor = false): Promise<void> {
  const baseUrl = await getBaseUrl()
  const url = new URL(`${baseUrl}/api/conversations/${conversationId}/messages/${messageId}/after`)
  if (includeAnchor) url.searchParams.append('include_anchor', 'true')

  const res = await fetch(url.toString(), { method: 'DELETE' })
  if (!res.ok) throw new Error(`Failed to delete message branch: ${res.status}`)
}

export async function saveMessagesBatch(conversationId: string, messages: Message[]): Promise<void> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/api/conversations/${conversationId}/messages/batch`, {
    method: 'POST',
    headers: { 'Content-Type': 'application/json' },
    body: JSON.stringify({ messages })
  })
  if (!res.ok) throw new Error(`Failed to save messages: ${res.status}`)
}

export async function listMavNotes(): Promise<MavNote[]> {
  const baseUrl = await getBaseUrl()
  const res = await fetch(`${baseUrl}/admin/notes`)
  if (!res.ok) throw new Error(`Failed to fetch assistant notes: ${res.status}`)
  const data = await res.json()
  return Array.isArray(data) ? data : []
}

export async function getArtJob(jobId: string): Promise<ArtJob> {
  const baseUrl = await getBaseUrl()
  const mediaBaseUrl = baseUrl.replace(/\/$/, '')
  const res = await fetch(`${baseUrl}/api/art-jobs/${jobId}`)
  if (!res.ok) throw new Error(`Failed to fetch art job: ${res.status}`)
  const data = await res.json()
  const resolveMediaUrl = (value: string) => {
    if (!value || /^https?:\/\//i.test(value) || value.startsWith('data:')) return value
    return `${mediaBaseUrl}${value.startsWith('/') ? value : `/${value}`}`
  }
  const selected = data.selected_image
  const mapSelectedImage = (image: any): MessageImage => ({
    id: image.id,
    messageId: image.message_id ?? data.assistant_message_id,
    sortOrder: image.sort_order ?? 0,
    mimeType: image.mime_type ?? 'image/jpeg',
    width: image.width ?? null,
    height: image.height ?? null,
    visionType: image.vision_type ?? '',
    visionSummary: image.vision_summary ?? '',
    visionStatus: image.vision_status ?? 'ready',
    url: resolveMediaUrl(image.url),
    thumbUrl: resolveMediaUrl(image.thumb_url ?? image.url)
  })
  const selectedImages = Array.isArray(data.selected_images)
    ? data.selected_images.map(mapSelectedImage)
    : selected ? [mapSelectedImage(selected)] : []
  return {
    id: data.id,
    assistantMessageId: data.assistant_message_id,
    status: data.status,
    attempt: data.attempt ?? 0,
    maxAttempts: data.max_attempts ?? 2,
    caption: data.caption ?? '',
    error: data.error ?? '',
    mode: data.mode ?? 'studio',
    finalContent: data.final_content ?? '',
    updatedAt: data.updated_at,
    selectedImage: selectedImages[0] ?? null,
    selectedImages
  }
}

// 流式聊天
export interface StreamChatParams {
  messages: Array<{ role: string; content: any; message_id?: string }>
  messageId?: string
  conversationId?: string
  assistantMessageId?: string
  onChunk: (text: string) => void
  onDone: () => void
  onError: (error: Error) => void
  signal?: AbortSignal
  image?: { base64: string; mimeType: string }
  directSend?: boolean
  enableThinking?: boolean
  enableSearch?: boolean
  enableImageGeneration?: boolean
  enablePromptCache?: boolean
  onThinkingStart?: () => void
  onThinkingChunk?: (text: string) => void
  onThinkingDone?: () => void
  onMavNoteSaved?: () => void
  onMavPortraitUpdated?: () => void
  onMavPortraitUpdateFailed?: () => void
  onArtJobStarted?: (jobId: string, status?: ArtJobStatus, mode?: 'studio' | 'native') => void
  onArtJobFailed?: (error: string) => void
  onTokenUsage?: (usage: {
    inputTokens: number
    outputTokens: number
    totalTokens: number
    cacheCreationTokens: number
    cacheReadTokens: number
    cacheCreation1hTokens: number
  }) => void
  onSearchToolUsed?: () => void
  onMemoryRecallStarted?: () => void
  onMemoryRecallFinished?: (found: boolean) => void
}

export async function streamChat(params: StreamChatParams): Promise<void> {
  const baseUrl = await getBaseUrl()
  const apiKey = await getApiKey()
  const model = await getModel()

  const res = await fetch(`${baseUrl}/v1/chat/completions`, {
    method: 'POST',
    headers: {
      'Content-Type': 'application/json',
      'Authorization': `Bearer ${apiKey}`
    },
    body: JSON.stringify({
      model,
      messages: params.messages,
      ...(params.messageId && { message_id: params.messageId }),
      ...(params.conversationId && { conversation_id: params.conversationId }),
      ...(params.assistantMessageId && { assistant_message_id: params.assistantMessageId }),
      stream: true,
      ...(params.image && { image: params.image }),
      ...(params.directSend !== undefined && { direct_send: params.directSend }),
      ...(params.enableThinking !== undefined && { enable_thinking: params.enableThinking }),
      ...(params.enableSearch !== undefined && { enable_search: params.enableSearch }),
      ...(params.enableImageGeneration !== undefined && { enable_image_generation: params.enableImageGeneration }),
      ...(params.enablePromptCache !== undefined && { enable_prompt_cache: params.enablePromptCache }),
    }),
    signal: params.signal
  })

  if (!res.ok) {
    throw new Error(`HTTP error! status: ${res.status}`)
  }

  const reader = res.body?.getReader()
  if (!reader) {
    throw new Error('No reader available')
  }

  const decoder = new TextDecoder()
  let buffer = ''
  let currentBlockType = ''

  try {
    while (true) {
      const { done, value } = await reader.read()
      if (done) break

      buffer += decoder.decode(value, { stream: true })
      const lines = buffer.split('\n')
      buffer = lines.pop() || ''

      for (const line of lines) {
        if (!line.startsWith('data:')) continue
        const data = line.slice(5).trim()
        if (data === '[DONE]') {
          params.onDone()
          return
        }
        try {
          const parsed = JSON.parse(data)

          if (parsed.type === 'mav_note_saved') {
            params.onMavNoteSaved?.()
            continue
          }

          if (parsed.type === 'mav_portrait_updated') {
            params.onMavPortraitUpdated?.()
            continue
          }

          if (parsed.type === 'mav_portrait_update_failed') {
            params.onMavPortraitUpdateFailed?.()
            continue
          }

          if (parsed.type === 'mav_art_job_started') {
            params.onArtJobStarted?.(
              parsed.job_id,
              parsed.status as ArtJobStatus | undefined,
              parsed.mode === 'native' ? 'native' : 'studio'
            )
            continue
          }

          if (parsed.type === 'mav_art_job_failed') {
            params.onArtJobFailed?.(parsed.error || '创作任务没有成功启动')
            continue
          }

          if (parsed.type === 'search_tool_used') {
            params.onSearchToolUsed?.()
            continue
          }
          if (parsed.type === 'memory_recall_started') {
            params.onMemoryRecallStarted?.()
            continue
          }
          if (parsed.type === 'memory_recall_finished') {
            params.onMemoryRecallFinished?.(parsed.found === true)
            continue
          }

          // Claude格式：block开始
          if (parsed.type === 'token_usage') {
            const usage = parsed.usage || {}
            params.onTokenUsage?.({
              inputTokens: usage.input_tokens ?? usage.inputTokens ?? 0,
              outputTokens: usage.output_tokens ?? usage.outputTokens ?? 0,
              totalTokens: usage.total_tokens ?? usage.totalTokens ?? 0,
              cacheCreationTokens: usage.cache_creation_input_tokens ?? usage.cacheCreationTokens ?? 0,
              cacheReadTokens: usage.cache_read_input_tokens ?? usage.cacheReadTokens ?? 0,
              cacheCreation1hTokens: usage.cache_creation_1h_input_tokens ?? usage.cacheCreation1hTokens ?? 0
            })
            continue
          }

          if (parsed.type === 'content_block_start') {
            currentBlockType = parsed.content_block?.type || ''
            if (currentBlockType === 'thinking') {
              params.onThinkingStart?.()
            }
          }

          // Claude格式：block结束
          if (parsed.type === 'content_block_stop') {
            if (currentBlockType === 'thinking') {
              params.onThinkingDone?.()
            }
            currentBlockType = ''
          }

          // Claude格式：delta
          if (parsed.type === 'content_block_delta') {
            if (parsed.delta?.type === 'thinking_delta') {
              params.onThinkingChunk?.(parsed.delta.thinking || '')
            } else if (parsed.delta?.type === 'text_delta') {
              params.onChunk(parsed.delta.text || '')
            }
          }

          // OpenAI格式
          const openaiContent = parsed.choices?.[0]?.delta?.content
          if (openaiContent) {
            params.onChunk(openaiContent)
          }

        } catch {
          // Ignore parse errors
        }
      }
    }
    params.onDone()
  } catch (error) {
    if (error instanceof Error && error.name !== 'AbortError') {
      params.onError(error)
    }
    throw error
  }
}
