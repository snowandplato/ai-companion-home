import { create } from 'zustand'
import { v4 as uuidv4 } from 'uuid'
import type { ArtJob, Conversation, ImageUpload, Message, MessageImage } from '../services/api'
import { settingsStore } from './settingsStore'
import {
  listConversations,
  createConversation,
  updateConversation,
  setConversationFolded,
  deleteConversation as deleteConversationApi,
  getConversation,
  getArtJob,
  listMessagesPage,
  saveMessage,
  deleteMessagesAfter,
  streamChat
} from '../services/api'

const MESSAGE_PAGE_SIZE = 40
const CHAT_CONTEXT_MESSAGE_LIMIT = 20
let conversationLoadVersion = 0

interface ChatState {
  // 对话列表
  conversations: Conversation[]
  currentConversationId: string | null

  // 当前对话的消息
  messages: Message[]
  messageTotal: number
  hasOlderMessages: boolean
  isLoadingConversation: boolean
  isLoadingOlderMessages: boolean

  // 输入和加载状态
  inputText: string
  isStreaming: boolean
  isThinkingActive: boolean
  isRecallingMemory: boolean

  // 中止控制器
  abortController: AbortController | null

  // Actions
  loadConversations: () => Promise<void>
  createConversation: () => Promise<string>
  selectConversation: (id: string) => Promise<void>
  loadOlderMessages: () => Promise<void>
  loadUntilMessage: (messageId: string) => Promise<boolean>
  deleteConversation: (id: string) => Promise<void>
  renameConversation: (id: string, title: string) => Promise<void>
  foldConversation: (id: string, folded: boolean) => Promise<void>

  setInputText: (text: string) => void
  sendMessage: (imageOptions?: { images?: ImageUpload[]; directSend?: boolean; fileContent?: string; fileName?: string; enableThinking?: boolean; enableSearch?: boolean; enableImageGeneration?: boolean }) => Promise<void>
  editUserMessageAndRegenerate: (messageId: string, content: string, options?: { enableThinking?: boolean; enableSearch?: boolean }) => Promise<void>
  stopStreaming: () => void
}

export const chatStore = create<ChatState>((set, get) => ({
  conversations: [],
  currentConversationId: null,
  messages: [],
  messageTotal: 0,
  hasOlderMessages: false,
  isLoadingConversation: false,
  isLoadingOlderMessages: false,
  inputText: '',
  isStreaming: false,
  isThinkingActive: false,
  isRecallingMemory: false,
  abortController: null,

  loadConversations: async () => {
    try {
      const conversations: Conversation[] = []
      const pageSize = 100
      for (let offset = 0; ; offset += pageSize) {
        const page = await listConversations(pageSize, offset)
        conversations.push(...page)
        if (page.length < pageSize) break
      }
      // 确保返回的是数组
      if (Array.isArray(conversations)) {
        set({ conversations })
      } else {
        console.warn('listConversations did not return an array:', conversations)
        set({ conversations: [] })
      }
    } catch (error) {
      console.error('[loadConversations] Failed to load conversations:', error)
      // 加载失败时确保 conversations 是空数组
      set({ conversations: [] })
    }
  },

  createConversation: async () => {
    const id = uuidv4()
    const title = 'New Conversation'
    await createConversation(id, title)
    const conversation = await getConversation(id)
    if (!conversation) throw new Error('Created conversation could not be loaded')
    set((state) => ({
      conversations: [{ ...conversation, last_message_preview: '', is_folded: 1 }, ...state.conversations]
    }))
    return id
  },

  selectConversation: async (id: string) => {
    const loadVersion = ++conversationLoadVersion
    set({
      currentConversationId: id,
      messages: [],
      messageTotal: 0,
      hasOlderMessages: false,
      isLoadingConversation: true,
      isLoadingOlderMessages: false,
      inputText: ''
    })

    try {
      const page = await listMessagesPage(id, MESSAGE_PAGE_SIZE, 0, 'desc')
      if (loadVersion !== conversationLoadVersion || get().currentConversationId !== id) return

      const messages = [...page.messages].reverse()
      set({
        messages,
        messageTotal: page.total,
        hasOlderMessages: messages.length < page.total,
        isLoadingConversation: false
      })
    } catch (error) {
      console.error('[selectConversation] Failed to load messages:', error)
      if (loadVersion !== conversationLoadVersion || get().currentConversationId !== id) return
      set({ messages: [], messageTotal: 0, hasOlderMessages: false, isLoadingConversation: false })
    }
  },

  loadOlderMessages: async () => {
    const state = get()
    const conversationId = state.currentConversationId
    if (!conversationId || state.isLoadingConversation || state.isLoadingOlderMessages || !state.hasOlderMessages || state.isStreaming) return

    const loadVersion = conversationLoadVersion
    const offset = state.messages.length
    set({ isLoadingOlderMessages: true })

    try {
      const page = await listMessagesPage(conversationId, MESSAGE_PAGE_SIZE, offset, 'desc')
      if (loadVersion !== conversationLoadVersion || get().currentConversationId !== conversationId) return

      const olderMessages = [...page.messages].reverse()
      set((current) => {
        const knownIds = new Set(current.messages.map((message) => message.id))
        const uniqueOlderMessages = olderMessages.filter((message) => !knownIds.has(message.id))
        const messages = [...uniqueOlderMessages, ...current.messages]
        return {
          messages,
          messageTotal: page.total,
          hasOlderMessages: messages.length < page.total,
          isLoadingOlderMessages: false
        }
      })
    } catch (error) {
      console.error('[loadOlderMessages] Failed to load older messages:', error)
      if (loadVersion === conversationLoadVersion && get().currentConversationId === conversationId) {
        set({ isLoadingOlderMessages: false })
      }
    }
  },

  loadUntilMessage: async (messageId: string) => {
    const conversationId = get().currentConversationId
    if (!conversationId || get().isStreaming) return false
    if (get().messages.some((message) => message.id === messageId)) return true

    while (
      get().currentConversationId === conversationId &&
      get().hasOlderMessages
    ) {
      const previousLength = get().messages.length
      await get().loadOlderMessages()
      const current = get()
      if (current.currentConversationId !== conversationId) return false
      if (current.messages.some((message) => message.id === messageId)) return true
      if (current.messages.length <= previousLength) return false
    }

    return get().messages.some((message) => message.id === messageId)
  },

  deleteConversation: async (id: string) => {
    if (get().currentConversationId === id) conversationLoadVersion += 1
    await deleteConversationApi(id)
    set((state) => ({
      conversations: state.conversations.filter((c) => c.id !== id),
      currentConversationId: state.currentConversationId === id ? null : state.currentConversationId,
      messages: state.currentConversationId === id ? [] : state.messages,
      messageTotal: state.currentConversationId === id ? 0 : state.messageTotal,
      hasOlderMessages: state.currentConversationId === id ? false : state.hasOlderMessages,
      isLoadingConversation: state.currentConversationId === id ? false : state.isLoadingConversation,
      isLoadingOlderMessages: state.currentConversationId === id ? false : state.isLoadingOlderMessages
    }))
  },

  renameConversation: async (id: string, title: string) => {
    await updateConversation(id, title)
    set((state) => ({
      conversations: state.conversations.map((c) => (c.id === id ? { ...c, title } : c))
    }))
  },

  foldConversation: async (id: string, folded: boolean) => {
    await setConversationFolded(id, folded)
    set((state) => ({
      conversations: state.conversations.map((c) => c.id === id ? { ...c, is_folded: folded ? 1 : 0 } : c)
    }))
  },

  setInputText: (text) => set({ inputText: text }),

  sendMessage: async (imageOptions?: { images?: ImageUpload[]; directSend?: boolean; fileContent?: string; fileName?: string; enableThinking?: boolean; enableSearch?: boolean; enableImageGeneration?: boolean }) => {
    const state = get()
    const imageUploads = imageOptions?.images || []
    if (!state.inputText.trim() && imageUploads.length === 0 && !imageOptions?.fileContent) return

    const abortController = new AbortController()
    set({ isStreaming: true, isRecallingMemory: false, abortController })

    try {
      // 如果没有当前对话，创建一个新的
      let currentConversationId = state.currentConversationId
      if (!currentConversationId) {
        currentConversationId = await get().createConversation()
        set({ currentConversationId })
        await get().selectConversation(currentConversationId)
      }

      // 创建用户消息
      const localImages: MessageImage[] = imageUploads.map((image, index) => ({
        id: `pending-${index}-${uuidv4()}`,
        messageId: '',
        sortOrder: index,
        mimeType: image.mimeType,
        visionStatus: 'uploading',
        url: image.preview,
        thumbUrl: image.preview
      }))
      const userMessage: Message = {
        id: uuidv4(),
        role: 'user',
        content: state.inputText,
        created_at: new Date().toISOString(),
        images: localImages,
        fileName: imageOptions?.fileName || null
      }

      // 添加到本地消息列表
      set((state) => ({
        messages: [...state.messages, userMessage],
        messageTotal: state.messageTotal + 1,
        inputText: ''
      }))

      // 保存用户消息到后端
      const savedImages = await saveMessage(currentConversationId, userMessage, imageUploads)
      if (savedImages.length) {
        set((state) => ({
          messages: state.messages.map((message) =>
            message.id === userMessage.id ? { ...message, images: savedImages } : message
          )
        }))
      }

      // 创建助手消息占位
      const currentSettings = settingsStore.getState()
      const currentModel = currentSettings.model
      const assistantMessage: Message = {
        id: uuidv4(),
        role: 'assistant',
        content: '',
        created_at: new Date().toISOString(),
        model: currentModel
      }

      set((state) => ({
        messages: [...state.messages, assistantMessage],
        messageTotal: state.messageTotal + 1
      }))

      // 准备聊天消息历史 - 获取添加后的状态，然后移除最后一个
      const stateWithAssistant = get()
      const messagesWithAssistant = stateWithAssistant.messages

      const contextMessages = messagesWithAssistant.slice(0, -1).slice(-CHAT_CONTEXT_MESSAGE_LIMIT)
      const chatMessages = contextMessages.map((msg, index, arr) => {
        if (index === arr.length - 1 && msg.role === 'user') {
          // 文件模式：API内容 = 文件内容 + 用户消息，气泡只显示用户消息
          if (imageOptions?.fileContent) {
            const fileHeader = `[文件：${imageOptions.fileName || '文件'}]\n\n${imageOptions.fileContent}`
            const apiContent = msg.content ? fileHeader + '\n\n' + msg.content : fileHeader
            return { role: msg.role, content: apiContent, message_id: msg.id }
          }
        }
        return {
          role: msg.role,
          content: msg.role === 'assistant'
            ? msg.content.replace(/\n*\[\[mav_memory_(?:recalled|recall_done|recall_pending)\]\]\n*/g, '')
            : msg.content,
          message_id: msg.id
        }
      })

      // 流式聊天
      let assistantContent = ''
      let thinkingContent = ''
      let mavNoteSaved = false
      let mavPortraitUpdated = false
      let mavPortraitUpdateFailed = false
      let artJobId: string | null = null
      let searchToolNoticeIndex: number | null = null
      let memoryRecallNoticeIndex: number | null = null
      let memoryRecallPending = false
      let memoryRecallSucceeded = false
      let tokenUsage: {
        inputTokens: number
        outputTokens: number
        totalTokens: number
        cacheCreationTokens: number
        cacheReadTokens: number
        cacheCreation1hTokens: number
      } | null = null
      const mavNoteNotice = '\n\n[[mav_note_saved]]'
      const mavPortraitNotice = '\n\n[[mav_portrait_updated]]'
      const mavPortraitFailureNotice = '\n\n[[mav_portrait_update_failed]]'
      const searchToolNotice = '[[mav_search_used]]'
      const memoryRecallPendingNotice = '[[mav_memory_recall_pending]]'
      const memoryRecallDoneNotice = '[[mav_memory_recall_done]]'
      const watchArtJob = async (jobId: string) => {
        for (let poll = 0; poll < 300; poll += 1) {
          try {
            const job = await getArtJob(jobId)
            set((state) => ({
              messages: state.messages.map((message) => {
                if (message.id !== assistantMessage.id) return message
                const selectedImages = job.selectedImages?.length
                  ? job.selectedImages
                  : job.selectedImage ? [job.selectedImage] : message.images
                return {
                  ...message,
                  artJob: job,
                  images: selectedImages,
                  content: job.finalContent || message.content
                }
              })
            }))
            if (['completed', 'declined', 'rejected', 'prompt_failed', 'failed'].includes(job.status)) return
          } catch (error) {
            console.error('[watchArtJob] Failed to poll art job:', error)
          }
          await new Promise((resolve) => window.setTimeout(resolve, 2000))
        }
      }
      const buildVisibleContent = () => {
        const thinkingStarted = assistantContent.includes('<thinking>') ||
                                assistantContent.includes('<think>') ||
                                assistantContent.includes('<small_thinking>')
        const thinkingEnded = /(<\/thinking>|<\/think>|<\/small_thinking>)/.test(assistantContent)
        const isInThinking = thinkingStarted && !thinkingEnded
        const thinkingMatch = assistantContent.match(/<(?:thinking|think|small_thinking)>([\s\S]*?)<\/(?:thinking|think|small_thinking)>/)
        const extractedThinking = thinkingMatch ? thinkingMatch[1] : null
        const cleanContent = assistantContent.replace(/<(?:thinking|think|small_thinking)>[\s\S]*?<\/(?:thinking|think|small_thinking)>/g, '')
        const displayContentBase = isInThinking
          ? assistantContent.replace(/<(?:thinking|think|small_thinking)>[\s\S]*/g, '').trim()
          : cleanContent

        return { displayContentBase, extractedThinking, isInThinking }
      }
      const applyToolNotices = (content: string, includePendingMemory: boolean) => {
        const notices: { index: number; marker: string }[] = []
        if (searchToolNoticeIndex !== null) {
          notices.push({ index: searchToolNoticeIndex, marker: searchToolNotice })
        }
        if (memoryRecallNoticeIndex !== null && (memoryRecallSucceeded || (includePendingMemory && memoryRecallPending))) {
          notices.push({
            index: memoryRecallNoticeIndex,
            marker: memoryRecallSucceeded ? memoryRecallDoneNotice : memoryRecallPendingNotice
          })
        }
        let result = ''
        let cursor = 0
        for (const notice of notices.sort((a, b) => a.index - b.index)) {
          const anchor = Math.max(cursor, Math.min(notice.index, content.length))
          result += content.slice(cursor, anchor)
          if (result.trim()) result = `${result.trimEnd()}\n\n`
          result += `${notice.marker}\n\n`
          cursor = anchor
        }
        return (result + content.slice(cursor).trimStart()).trim()
      }
      await streamChat({
        messages: chatMessages,
        messageId: userMessage.id,
        conversationId: currentConversationId,
        assistantMessageId: assistantMessage.id,
        signal: abortController.signal,
        directSend: imageOptions?.directSend,
        enableThinking: imageOptions?.enableThinking,
        enableSearch: imageOptions?.enableSearch,
        enableImageGeneration: imageOptions?.enableImageGeneration,
        enablePromptCache: currentSettings.enablePromptCache,
        onThinkingStart: () => {
          set({ isThinkingActive: true })
        },
        onThinkingChunk: (text) => {
          thinkingContent += text
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, thinking: thinkingContent }
                : msg
            )
          }))
        },
        onThinkingDone: () => {
          set({ isThinkingActive: false })
        },
        onMavNoteSaved: () => {
          mavNoteSaved = true
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, content: `${msg.content || ''}${mavNoteNotice}` }
                : msg
            )
            }))
        },
        onMavPortraitUpdated: () => {
          mavPortraitUpdated = true
          mavPortraitUpdateFailed = false
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, content: `${msg.content || ''}${mavPortraitNotice}` }
                : msg
            )
          }))
        },
        onMavPortraitUpdateFailed: () => {
          mavPortraitUpdateFailed = true
          mavPortraitUpdated = false
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, content: `${msg.content || ''}${mavPortraitFailureNotice}` }
                : msg
            )
          }))
        },
        onArtJobStarted: (jobId, status, mode) => {
          artJobId = jobId
          const queuedJob: ArtJob = {
            id: jobId,
            assistantMessageId: assistantMessage.id,
            status: status || 'queued',
            attempt: 0,
            maxAttempts: 2,
            mode: mode || 'studio'
          }
          set((state) => ({
            messages: state.messages.map((message) =>
              message.id === assistantMessage.id ? { ...message, artJob: queuedJob } : message
            )
          }))
        },
        onArtJobFailed: (error) => {
          const failedJob: ArtJob = {
            id: `failed-${assistantMessage.id}`,
            assistantMessageId: assistantMessage.id,
            status: 'failed',
            attempt: 0,
            maxAttempts: 2,
            error
          }
          set((state) => ({
            messages: state.messages.map((message) =>
              message.id === assistantMessage.id ? { ...message, artJob: failedJob } : message
            )
          }))
        },
        onSearchToolUsed: () => {
          if (searchToolNoticeIndex === null) {
            const { displayContentBase } = buildVisibleContent()
            searchToolNoticeIndex = displayContentBase.length
          }
          const { displayContentBase } = buildVisibleContent()
          const displayContent = applyToolNotices(displayContentBase, true)
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? { ...msg, content: displayContent }
                : msg
            )
          }))
        },
        onMemoryRecallStarted: () => {
          const { displayContentBase } = buildVisibleContent()
          memoryRecallNoticeIndex = displayContentBase.length
          memoryRecallPending = true
          set({ isRecallingMemory: true })
          const displayContent = applyToolNotices(displayContentBase, true)
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id ? { ...msg, content: displayContent } : msg
            )
          }))
        },
        onMemoryRecallFinished: (found) => {
          memoryRecallPending = false
          memoryRecallSucceeded = found
          set({ isRecallingMemory: false })
          const { displayContentBase } = buildVisibleContent()
          const displayContent = applyToolNotices(displayContentBase, false)
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id ? { ...msg, content: displayContent } : msg
            )
          }))
        },
        onTokenUsage: (usage) => {
          tokenUsage = usage
          set((state) => ({
            messages: state.messages.map((msg) =>
              msg.id === assistantMessage.id
                ? {
                    ...msg,
                    inputTokens: usage.inputTokens,
                    outputTokens: usage.outputTokens,
                    totalTokens: usage.totalTokens,
                    cacheCreationTokens: usage.cacheCreationTokens,
                    cacheReadTokens: usage.cacheReadTokens,
                    cacheCreation1hTokens: usage.cacheCreation1hTokens
                  }
                : msg
            )
          }))
        },
        onChunk: (text) => {
          if (text && memoryRecallPending) {
            memoryRecallPending = false
            set({ isRecallingMemory: false })
          }
          assistantContent += text

          const { displayContentBase, extractedThinking, isInThinking } = buildVisibleContent()
          if (isInThinking !== get().isThinkingActive) {
            set({ isThinkingActive: isInThinking })
          }

          const displayNotices = `${mavNoteSaved ? mavNoteNotice : ''}${mavPortraitUpdated ? mavPortraitNotice : ''}${mavPortraitUpdateFailed ? mavPortraitFailureNotice : ''}`
          const displayContent = `${applyToolNotices(displayContentBase, true)}${displayNotices}`

          set((state) => ({
            messages: state.messages.map((msg) => {
              if (msg.id === assistantMessage.id) {
                return {
                  ...msg,
                  content: displayContent,
                  thinking: thinkingContent || extractedThinking
                }
              }
              return msg
            })
          }))
        },
        onDone: async () => {
          set({ isStreaming: false, isThinkingActive: false, isRecallingMemory: false, abortController: null })

          // 最终处理content（兼容两种模式）
          const { displayContentBase, extractedThinking } = buildVisibleContent()
          const cleanContent = displayContentBase.trim()
          const finalThinking = thinkingContent || extractedThinking
          const finalNotices = `${mavNoteSaved ? mavNoteNotice : ''}${mavPortraitUpdated ? mavPortraitNotice : ''}${mavPortraitUpdateFailed ? mavPortraitFailureNotice : ''}`
          const finalContent = `${applyToolNotices(cleanContent, false)}${finalNotices}`

          if ((!finalContent || finalContent === '') && !artJobId) {
            console.warn('[sendMessage] Assistant message content is empty, skipping save')
          } else {
            const finalMessage = {
              ...assistantMessage,
              content: finalContent || ' ',
              thinking: finalThinking,
              inputTokens: tokenUsage?.inputTokens ?? null,
              outputTokens: tokenUsage?.outputTokens ?? null,
              totalTokens: tokenUsage?.totalTokens ?? null,
              cacheCreationTokens: tokenUsage?.cacheCreationTokens ?? null,
              cacheReadTokens: tokenUsage?.cacheReadTokens ?? null,
              cacheCreation1hTokens: tokenUsage?.cacheCreation1hTokens ?? null
            }
            await saveMessage(currentConversationId!, finalMessage)
            if (artJobId) void watchArtJob(artJobId)
          }

          // 如果是第一轮对话，更新标题
          const conv = await getConversation(currentConversationId!)
          if (conv && (!conv.title || conv.title === 'New Conversation')) {
            const title = state.inputText.slice(0, 20)
            await updateConversation(currentConversationId!, title)
            set((state) => ({
              conversations: state.conversations.map((c) =>
                c.id === currentConversationId ? { ...c, title } : c
              )
            }))
          }
        },
        onError: (error) => {
          console.error('Stream error:', error)
          set({ isStreaming: false, isThinkingActive: false, isRecallingMemory: false, abortController: null })
        }
      })
    } catch (error) {
      console.error('Failed to send message:', error)
      set({ isStreaming: false, isRecallingMemory: false, abortController: null })
    }
  },

  editUserMessageAndRegenerate: async (messageId, content, options) => {
    const state = get()
    const currentConversationId = state.currentConversationId
    const editedContent = content.trim()
    if (!currentConversationId || !editedContent || state.isStreaming) return

    const messageIndex = state.messages.findIndex((msg) => msg.id === messageId)
    const target = state.messages[messageIndex]
    if (messageIndex < 0 || !target || target.role !== 'user') return

    await deleteMessagesAfter(currentConversationId, messageId, true)
    const remainingMessages = state.messages.slice(0, messageIndex)
    const removedMessageCount = state.messages.length - remainingMessages.length
    set({
      messages: remainingMessages,
      messageTotal: Math.max(remainingMessages.length, state.messageTotal - removedMessageCount),
      inputText: editedContent
    })

    await get().sendMessage({
      enableThinking: options?.enableThinking,
      enableSearch: options?.enableSearch
    })
  },

  stopStreaming: () => {
    const { abortController } = get()
    if (abortController) {
      abortController.abort()
    }
    set({ isStreaming: false, isRecallingMemory: false, abortController: null })
  }
}))
