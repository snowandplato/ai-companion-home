import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import React from 'react'
import { createPortal } from 'react-dom'
import { Icon } from './components/Icon'
import { SettingsPanel } from './components/SettingsPanel'
import { BookShelf } from './components/BookShelf'
import { BookReader } from './components/BookReader'
import { NotesView } from './components/NotesView'
import { ChatImageGallery } from './components/ChatImageGallery'
import { ArtJobStatus } from './components/ArtJobStatus'
import { PendingImageEditor } from './components/PendingImageEditor'
import { AssistantMessageSegments } from './components/AssistantMessageSegments'
import { chatStore } from './stores/chatStore'
import { settingsStore } from './stores/settingsStore'
import { identityStore, useIdentityLabel } from './stores/identityStore'
import { getConversationMessageMap, listMavNotes, type ImageUpload, type MavNote, type MessageMapItem } from './services/api'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { Settings, Sparkles, Globe, Palette, MapPlus, Moon, Sun, BotMessageSquare, BookHeart, Sticker, HeartPulse, Square, Send, Search, Maximize2, Minimize2, RefreshCw } from 'lucide-react'

// 内联SVG图标组件
const CopyIcon = ({ size = 16, color = '#716E85' }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <rect width="14" height="14" x="8" y="8" rx="2" ry="2" />
    <path d="M4 16c-1.1 0-2-.9-2-2V4c0-1.1.9-2 2-2h10c1.1 0 2 .9 2 2" />
  </svg>
)

const CheckIcon = ({ size = 16, color = '#E98E6B' }: { size?: number; color?: string }) => (
  <svg width={size} height={size} viewBox="0 0 24 24" fill="none" stroke={color} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
    <polyline points="20 6 9 17 4 12" />
  </svg>
)

// 登录门
const PASS_KEY = 'mhome_auth'
const CORRECT_PWD = import.meta.env.VITE_DEMO_PASSWORD || 'change-me'

function LoginGate({ children }: { children: React.ReactNode }) {
  const [authed, setAuthed] = useState(() => localStorage.getItem(PASS_KEY) === '1')
  const [input, setInput] = useState('')
  const [shake, setShake] = useState(false)

  if (authed) return <>{children}</>

  const submit = () => {
    if (input === CORRECT_PWD) {
      localStorage.setItem(PASS_KEY, '1')
      setAuthed(true)
    } else {
      setShake(true)
      setInput('')
      setTimeout(() => setShake(false), 500)
    }
  }

  return (
    <div style={{
      height: '100dvh', display: 'flex', flexDirection: 'column',
      alignItems: 'center', justifyContent: 'center',
      background: '#F5F3F7', gap: '16px'
    }}>
      <div style={{ fontSize: '32px' }}>🧡</div>
      <div style={{ fontSize: '17px', fontWeight: 600, color: '#2D2B35' }}>M-Home</div>
      <div style={{
        display: 'flex', gap: '8px',
        animation: shake ? 'shake 0.4s ease' : 'none'
      }}>
        <input
          autoFocus
          type="password"
          value={input}
          onChange={e => setInput(e.target.value)}
          onKeyDown={e => e.key === 'Enter' && submit()}
          placeholder="请输入密码"
          style={{
            padding: '10px 14px', borderRadius: '10px',
            border: '1px solid #D3D1DE', outline: 'none',
            fontSize: '15px', fontFamily: 'inherit',
            background: '#fff', color: '#2D2B35', width: '180px'
          }}
        />
        <button onClick={submit} style={{
          padding: '10px 18px', borderRadius: '10px',
          background: '#E98E6B', border: 'none',
          color: '#fff', fontSize: '15px', cursor: 'pointer'
        }}>进入</button>
      </div>
      <style>{`
        @keyframes shake {
          0%, 100% { transform: translateX(0); }
          20% { transform: translateX(-8px); }
          40% { transform: translateX(8px); }
          60% { transform: translateX(-6px); }
          80% { transform: translateX(6px); }
        }
      `}</style>
    </div>
  )
}

export default function App() {
  const label = useIdentityLabel()
  useEffect(() => { void identityStore.getState().load().catch(console.error) }, [])
  const [isMobile, setIsMobile] = useState(window.innerWidth < 768)
  const [drawerOpen, setDrawerOpen] = useState(false)
  const [theme, setTheme] = useState<'light' | 'dark'>('light')
  const [showBackToBottom, setShowBackToBottom] = useState(false)
  const [settingsOpen, setSettingsOpen] = useState(false)
  const [contextMenu, setContextMenu] = useState<{ x: number; y: number; conversationId: string; anchorTop: number; anchorBottom: number } | null>(null)
  const conversationMenuRef = useRef<HTMLDivElement>(null)
  const [historyExpanded, setHistoryExpanded] = useState(false)
  const longPressTimerRef = useRef<ReturnType<typeof setTimeout> | null>(null)
  const longPressOriginRef = useRef<{ x: number; y: number } | null>(null)
  const suppressConversationClickRef = useRef(false)
  const [editingId, setEditingId] = useState<string | null>(null)
  const [editingTitle, setEditingTitle] = useState('')
  const [editingMessageId, setEditingMessageId] = useState<string | null>(null)
  const [editingMessageText, setEditingMessageText] = useState('')
  const [expandedThinking, setExpandedThinking] = useState<Record<string, boolean>>({})
  const [copiedCode, setCopiedCode] = useState<string | null>(null)
  const [plusMenuOpen, setPlusMenuOpen] = useState(false)
  const [pendingImages, setPendingImages] = useState<ImageUpload[]>([])
  const [editingPendingImageIndex, setEditingPendingImageIndex] = useState<number | null>(null)
  const [directSend, setDirectSend] = useState(false)
  const [pendingFile, setPendingFile] = useState<{name: string, text: string} | null>(null)
  const [inputFocused, setInputFocused] = useState(false)
  const [focusMode, setFocusMode] = useState(false)
  const [activeTab, setActiveTab] = useState<'dialog' | 'reading' | 'note' | 'us'>('dialog')
  const [thinkingEnabled, setThinkingEnabled] = useState(false)
  const [searchEnabled, setSearchEnabled] = useState(false)
  const [imageGenerationEnabled, setImageGenerationEnabled] = useState(false)
  const [readingView, setReadingView] = useState<'shelf' | 'waiting' | 'reader'>('shelf')
  const [readerFocusMode, setReaderFocusMode] = useState(false)
  const [currentBook, setCurrentBook] = useState<any>(null)
  // @ts-ignore - unused for now
  const [waitingProgress, setWaitingProgress] = useState({ current: 0, total: 0 })
  const messagesEndRef = useRef<HTMLDivElement>(null)
  const messagesContainerRef = useRef<HTMLDivElement>(null)
  const shouldAutoScrollRef = useRef(true)
  const isPrependingHistoryRef = useRef(false)
  const isInitialBottomScrollRef = useRef(false)
  const [longConversationReminderDismissed, setLongConversationReminderDismissed] = useState(false)
  const mapListRef = useRef<HTMLDivElement>(null)
  const [mapOpen, setMapOpen] = useState(false)
  const [mapSearch, setMapSearch] = useState('')
  const [mapSearchActive, setMapSearchActive] = useState(false)
  const [mapMessages, setMapMessages] = useState<MessageMapItem[]>([])
  const [mapLoading, setMapLoading] = useState(false)
  const [mapError, setMapError] = useState('')
  const [mapJumpingMessageId, setMapJumpingMessageId] = useState<string | null>(null)
  const [mavNotes, setMavNotes] = useState<MavNote[]>([])
  const [notesLoading, setNotesLoading] = useState(false)
  const [notesError, setNotesError] = useState('')

  // ---------------- 新增状态 ----------------

  // 使用 Zustand 的状态订阅
  const conversations = chatStore((state) => state.conversations)
  const latestConversationId = conversations[0]?.id
  const activeConversations = conversations.filter((conv) => conv.id === latestConversationId || conv.is_folded === 0)
  const historyConversations = conversations.filter((conv) => conv.id !== latestConversationId && conv.is_folded !== 0)
  const orderedConversations = [...activeConversations, ...historyConversations]
  const menuConversation = conversations.find((conv) => conv.id === contextMenu?.conversationId)
  const currentConversationId = chatStore((state) => state.currentConversationId)
  const messages = chatStore((state) => state.messages)
  const messageTotal = chatStore((state) => state.messageTotal)
  const hasOlderMessages = chatStore((state) => state.hasOlderMessages)
  const isLoadingConversation = chatStore((state) => state.isLoadingConversation)
  const isLoadingOlderMessages = chatStore((state) => state.isLoadingOlderMessages)
  const inputText = chatStore((state) => state.inputText)
  const isStreaming = chatStore((state) => state.isStreaming)
  const isThinkingActive = chatStore((state) => state.isThinkingActive)

  const settings = settingsStore()
  const currentModel = settingsStore((state) => state.model)
  const isClaudeModel = /claude/i.test(currentModel)
  const currentApiKey = settings.apiKey.trim()
  const isOfficialOpenAI = currentApiKey.startsWith('sk-')
    && !currentApiKey.startsWith('sk-ant-')
    && !currentApiKey.startsWith('sk-payg-')
    && !currentApiKey.startsWith('sk-ai-v1-')
    && !currentApiKey.startsWith('sk-ss-v1-')
  const supportsDirectVision = isClaudeModel || isOfficialOpenAI
  const hasSendableContent = Boolean(inputText.trim() || pendingImages.length || pendingFile)

  useEffect(() => {
    if (!supportsDirectVision && directSend) setDirectSend(false)
  }, [supportsDirectVision, directSend])

  // 格式化时间
  const formatTime = (dateStr: string): string => {
    const date = new Date(dateStr)
    const now = new Date()
    const diff = now.getTime() - date.getTime()
    const minutes = Math.floor(diff / 60000)
    const hours = Math.floor(diff / 3600000)
    const days = Math.floor(diff / 86400000)

    if (minutes < 1) return '刚刚'
    if (minutes < 60) return `${minutes}分钟前`
    if (hours < 24) return `${hours}小时前`
    if (days < 7) return `${days}天前`
    return date.toLocaleDateString('zh-CN', { month: '2-digit', day: '2-digit' })
  }

  const formatNoteTime = (dateStr: string): string => {
    const date = new Date(dateStr)
    if (Number.isNaN(date.getTime())) return ''
    return date.toLocaleDateString('zh-CN', {
      month: '2-digit',
      day: '2-digit',
      hour: '2-digit',
      minute: '2-digit'
    })
  }

  const formatMessageTime = (dateStr: string): string => {
    return formatNoteTime(dateStr)
  }

  const formatTokenCount = (tokens?: number | null): string => {
    if (!tokens) return ''
    if (tokens >= 1000) {
      const value = tokens / 1000
      return `${value >= 10 ? value.toFixed(0) : value.toFixed(1)}k tokens`
    }
    return `${tokens} tokens`
  }

  const loadMavNotes = async () => {
    setNotesLoading(true)
    setNotesError('')
    try {
      const notes = await listMavNotes()
      setMavNotes(notes)
    } catch (error) {
      console.error('[App] Failed to load Mav notes:', error)
      setNotesError('小本本暂时打不开')
    } finally {
      setNotesLoading(false)
    }
  }

  useEffect(() => {
    const handleResize = () => setIsMobile(window.innerWidth < 768)
    window.addEventListener('resize', handleResize)
    return () => window.removeEventListener('resize', handleResize)
  }, [])

  useEffect(() => {
    if (activeTab === 'us') {
      loadMavNotes()
    }
  }, [activeTab])

  // 加载对话列表 - 等待settingsStore初始化后再加载
  useEffect(() => {
    setTimeout(() => {
      chatStore.getState().loadConversations().catch((error) => {
        console.error('[App] Failed to load conversations:', error)
      })
    }, 100)
  }, [])

  // 切换房间时，先标记为“首次定位”阶段，避免经过顶部时误触发历史分页。
  useLayoutEffect(() => {
    isInitialBottomScrollRef.current = Boolean(currentConversationId) && activeTab === 'dialog'
    shouldAutoScrollRef.current = true
  }, [activeTab, currentConversationId])

  // 最新一页加载完成后，在浏览器绘制前直接落到底部。
  useLayoutEffect(() => {
    if (
      activeTab !== 'dialog' ||
      !currentConversationId ||
      isLoadingConversation ||
      messages.length === 0 ||
      !isInitialBottomScrollRef.current
    ) return

    const container = messagesContainerRef.current
    if (!container) return

    const previousBehavior = container.style.scrollBehavior
    container.style.scrollBehavior = 'auto'
    container.scrollTop = container.scrollHeight
    container.style.scrollBehavior = previousBehavior
    shouldAutoScrollRef.current = true
    setShowBackToBottom(false)

    const releaseTimer = window.setTimeout(() => {
      isInitialBottomScrollRef.current = false
    }, 250)
    return () => window.clearTimeout(releaseTimer)
  }, [activeTab, currentConversationId, isLoadingConversation, messages.length])

  // 新消息到来时，仅在用户原本位于底部附近时继续跟随。
  useEffect(() => {
    if (activeTab !== 'dialog') return
    if (isPrependingHistoryRef.current || isInitialBottomScrollRef.current) return
    const container = messagesContainerRef.current
    if (!container) {
      scrollToBottom()
      return
    }
    const distanceFromBottom = container.scrollHeight - container.scrollTop - container.clientHeight
    const isNearBottom = distanceFromBottom < 120
    if (shouldAutoScrollRef.current || isNearBottom) {
      scrollToBottom()
    }
  }, [messages, isStreaming, currentConversationId, activeTab])

  useEffect(() => {
    if (!currentConversationId) {
      setLongConversationReminderDismissed(false)
      return
    }
    setLongConversationReminderDismissed(
      localStorage.getItem(`mhome-long-conversation-dismissed:${currentConversationId}`) === '1'
    )
  }, [currentConversationId])

  // 点击其他地方关闭上下文菜单
  useEffect(() => {
    const handleClick = () => setContextMenu(null)
    if (contextMenu) {
      document.addEventListener('click', handleClick)
      return () => document.removeEventListener('click', handleClick)
    }
  }, [contextMenu])

  // 点击其他地方关闭plus菜单
  useEffect(() => {
    const handleClick = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const menuArea = target.closest('[data-plus-menu="true"]')
      if (!menuArea) {
        setPlusMenuOpen(false)
      }
    }
    if (plusMenuOpen) {
      document.addEventListener('click', handleClick)
      return () => document.removeEventListener('click', handleClick)
    }
  }, [plusMenuOpen])

  // 保存编辑的标题
  const saveTitle = async () => {
    if (editingId && editingTitle.trim()) {
      await chatStore.getState().renameConversation(editingId, editingTitle.trim())
    }
    setEditingId(null)
    setEditingTitle('')
  }

  // 开始编辑标题
  const startEditing = (id: string, title: string) => {
    setEditingId(id)
    setEditingTitle(title)
    setContextMenu(null)
  }

  const cancelConversationLongPress = () => {
    if (longPressTimerRef.current) clearTimeout(longPressTimerRef.current)
    longPressTimerRef.current = null
    longPressOriginRef.current = null
  }

  const startConversationLongPress = (id: string, x: number, y: number, anchorTop: number, anchorBottom: number) => {
    cancelConversationLongPress()
    longPressOriginRef.current = { x, y }
    longPressTimerRef.current = setTimeout(() => {
      suppressConversationClickRef.current = true
      setContextMenu({ x, y, conversationId: id, anchorTop, anchorBottom })
      longPressTimerRef.current = null
    }, 550)
  }

  useLayoutEffect(() => {
    const menu = conversationMenuRef.current
    if (!contextMenu || !isMobile || !menu) return
    const menuHeight = menu.getBoundingClientRect().height
    const availableBottom = window.innerHeight - 76
    const below = contextMenu.anchorBottom + 6
    const above = contextMenu.anchorTop - menuHeight - 6
    const top = below + menuHeight <= availableBottom
      ? below
      : above >= 8
        ? above
        : Math.max(8, Math.min(contextMenu.y - menuHeight / 2, availableBottom - menuHeight))
    menu.style.top = `${top}px`
  }, [contextMenu, isMobile])

  const handleThemeToggle = () => {
    setTheme(theme === 'light' ? 'dark' : 'light')
  }

  const scrollToBottom = () => {
    setTimeout(() => {
      messagesEndRef.current?.scrollIntoView({ behavior: 'smooth' })
    }, 50)
  }

  const keepBottomAfterMediaLoad = () => {
    if (!shouldAutoScrollRef.current || isPrependingHistoryRef.current) return
    window.requestAnimationFrame(() => {
      const container = messagesContainerRef.current
      if (container) container.scrollTop = container.scrollHeight
    })
  }

  const loadOlderMessagesAtTop = async () => {
    const container = messagesContainerRef.current
    const state = chatStore.getState()
    if (!container || !state.hasOlderMessages || state.isLoadingOlderMessages || state.isStreaming) return

    const conversationId = state.currentConversationId
    const previousScrollHeight = container.scrollHeight
    const previousScrollTop = container.scrollTop
    isPrependingHistoryRef.current = true
    shouldAutoScrollRef.current = false

    await state.loadOlderMessages()

    window.requestAnimationFrame(() => {
      const currentContainer = messagesContainerRef.current
      if (!currentContainer || chatStore.getState().currentConversationId !== conversationId) {
        isPrependingHistoryRef.current = false
        return
      }

      const previousBehavior = currentContainer.style.scrollBehavior
      currentContainer.style.scrollBehavior = 'auto'
      currentContainer.scrollTop = previousScrollTop + (currentContainer.scrollHeight - previousScrollHeight)
      window.requestAnimationFrame(() => {
        currentContainer.style.scrollBehavior = previousBehavior
        isPrependingHistoryRef.current = false
      })
    })
  }

  const dismissLongConversationReminder = () => {
    if (currentConversationId) {
      localStorage.setItem(`mhome-long-conversation-dismissed:${currentConversationId}`, '1')
    }
    setLongConversationReminderDismissed(true)
  }

  const handleSend = () => {
    if (isStreaming) {
      chatStore.getState().stopStreaming()
    } else {
      shouldAutoScrollRef.current = true
      chatStore.getState().sendMessage({
        images: pendingImages, directSend,
        fileContent: pendingFile?.text,
        fileName: pendingFile?.name,
        enableThinking: thinkingEnabled,
        enableSearch: searchEnabled,
        enableImageGeneration: imageGenerationEnabled
      })
      setImageGenerationEnabled(false)
      if (pendingImages.length) { setPendingImages([]); setDirectSend(false) }
      if (pendingFile) setPendingFile(null)
    }
  }

  // 滚动监听
  const startMessageEdit = (id: string, content: string) => {
    if (isStreaming) return
    setEditingMessageId(id)
    setEditingMessageText(content)
  }

  const cancelMessageEdit = () => {
    setEditingMessageId(null)
    setEditingMessageText('')
  }

  const saveMessageEdit = async () => {
    if (!editingMessageId || !editingMessageText.trim() || isStreaming) return
    const messageId = editingMessageId
    const content = editingMessageText
    cancelMessageEdit()
    await chatStore.getState().editUserMessageAndRegenerate(messageId, content, {
      enableThinking: thinkingEnabled,
      enableSearch: searchEnabled
    })
  }

  useEffect(() => {
    const container = messagesContainerRef.current
    if (!container) return

    const handleScroll = () => {
      const { scrollTop, scrollHeight, clientHeight } = container
      const isNearBottom = scrollHeight - scrollTop - clientHeight < 100
      shouldAutoScrollRef.current = isNearBottom
      setShowBackToBottom(!isNearBottom)
      if (scrollTop < 80 && !isInitialBottomScrollRef.current) void loadOlderMessagesAtTop()
    }

    container.addEventListener('scroll', handleScroll)
    return () => container.removeEventListener('scroll', handleScroll)
  }, [currentConversationId, hasOlderMessages, isLoadingOlderMessages, isStreaming])

  // 地图使用独立的全量轻量索引，不受聊天区40条分页限制。
  useEffect(() => {
    if (!mapOpen || !currentConversationId) {
      setMapMessages([])
      setMapLoading(false)
      setMapError('')
      return
    }

    let cancelled = false
    setMapMessages([])
    setMapLoading(true)
    setMapError('')
    getConversationMessageMap(currentConversationId)
      .then((items) => {
        if (!cancelled) setMapMessages(items)
      })
      .catch((error) => {
        console.error('[App] Failed to load conversation map:', error)
        if (!cancelled) setMapError('对话地图暂时没有展开，请稍后再试')
      })
      .finally(() => {
        if (!cancelled) setMapLoading(false)
      })

    return () => {
      cancelled = true
    }
  }, [mapOpen, currentConversationId])

  // 地图面板打开、全量索引抵达后自动滚到底部。
  useEffect(() => {
    if (mapOpen && !mapLoading && mapListRef.current) {
      setTimeout(() => {
        if (mapListRef.current) {
          mapListRef.current.scrollTop = mapListRef.current.scrollHeight
        }
      }, 50)
    }
  }, [mapOpen, mapLoading, mapMessages.length])

  const jumpToMapMessage = async (messageId: string) => {
    if (mapJumpingMessageId) return
    setMapJumpingMessageId(messageId)
    setMapError('')
    isPrependingHistoryRef.current = true
    shouldAutoScrollRef.current = false

    const found = await chatStore.getState().loadUntilMessage(messageId)
    isPrependingHistoryRef.current = false
    setMapJumpingMessageId(null)

    if (!found) {
      setMapError(isStreaming ? label('Mav回复完成后再跳转到旧消息') : '这条消息暂时没有加载出来')
      return
    }

    setMapOpen(false)
    window.requestAnimationFrame(() => {
      window.requestAnimationFrame(() => {
        document.getElementById(`msg-${messageId}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' })
      })
    })
  }

  // 主题色系统 - 根据设计说明的颜色
  const colors = theme === 'light' ? {
    bgPrimary: '#F5F3F7',
    bgCard: '#FFFFFF',
    bgSurface: '#F2F0F6',
    textPrimary: '#2D2B35',
    textSecondary: '#716E85',
    textTertiary: '#A8A6B2',
    primary: '#E98E6B',
    primaryHover: '#D87455',
    primaryLight: '#F5E8E1',
    purpleLight: '#E9E6EA',
    purpleMid: '#D3D1DE',
    purpleDark: '#716E85',
    purplePale: '#B8B6C4',
    // 标题底色 - 深紫色
    titleBg: '#716E85',
    titleText: '#E9E6EA',
    border: '#E5E3EA',
    borderLight: '#F0EEF5',
    shadow: 'rgba(115, 110, 133, 0.08)',
    shadowHover: 'rgba(115, 110, 133, 0.15)'
  } : {
    bgPrimary: '#18161E',
    bgCard: '#25232B',
    bgSurface: '#2E2C34',
    textPrimary: '#E9E6EA',
    textSecondary: '#B8B6C4',
    textTertiary: '#8E8C9A',
    primary: '#E98E6B',
    primaryHover: '#D87455',
    primaryLight: 'rgba(233, 142, 107, 0.12)',
    purpleLight: '#3D3A44',
    purpleMid: '#4A4760',
    purpleDark: '#716E85',
    purplePale: '#5D5A6A',
    // 标题底色 - 浅色模式下深紫色，深色模式下使用紫色
    titleBg: '#4A4760',
    titleText: '#E9E6EA',
    border: '#4A4760',
    borderLight: 'rgba(74, 71, 96, 0.5)',
    shadow: 'rgba(0, 0, 0, 0.3)',
    shadowHover: 'rgba(0, 0, 0, 0.45)'
  }

  const compressImage = (file: File): Promise<ImageUpload> => {
    return new Promise((resolve, reject) => {
      const reader = new FileReader()
      reader.onload = (e) => {
        const img = new Image()
        img.onload = () => {
          const maxSize = 2560
          let w = img.width, h = img.height
          if (w > maxSize || h > maxSize) {
            if (w > h) { h = Math.round(h * maxSize / w); w = maxSize }
            else { w = Math.round(w * maxSize / h); h = maxSize }
          }
          const canvas = document.createElement('canvas')
          canvas.width = w; canvas.height = h
          const context = canvas.getContext('2d')
          if (!context) {
            reject(new Error('图片处理失败'))
            return
          }
          context.drawImage(img, 0, 0, w, h)
          const mimeType = file.type === 'image/png' ? 'image/png' : 'image/jpeg'
          const preview = canvas.toDataURL(mimeType, mimeType === 'image/jpeg' ? 0.88 : undefined)
          const base64 = preview.split(',')[1]
          resolve({ base64, mimeType, preview })
        }
        img.onerror = () => reject(new Error('图片无法读取'))
        img.src = e.target!.result as string
      }
      reader.onerror = () => reject(new Error('图片无法读取'))
      reader.readAsDataURL(file)
    })
  }

  const addPendingImages = async (files: File[]) => {
    const available = Math.max(0, 6 - pendingImages.length)
    if (!available) {
      window.alert('每条消息最多发送6张图片')
      return
    }
    if (files.length > available) {
      window.alert(`每条消息最多6张，这次会添加前${available}张`)
    }
    try {
      const next = await Promise.all(files.slice(0, available).map(compressImage))
      setPendingImages((current) => [...current, ...next].slice(0, 6))
    } catch (error) {
      console.error('[App] Failed to prepare images:', error)
      window.alert('有图片无法读取，请换一张再试')
    }
  }

  const extractFileText = async (file: File): Promise<string> => {
    const name = file.name.toLowerCase()

    // txt / md — 直接读
    if (['.txt', '.md', '.py', '.ts', '.tsx', '.js', '.jsx', '.json', '.yaml', '.yml', '.toml', '.sh', '.css', '.html', '.xml', '.csv', '.env', '.ini', '.cfg', '.sql'].some(ext => name.endsWith(ext))) {
      return new Promise((resolve) => {
        const reader = new FileReader()
        reader.onload = (e) => resolve((e.target?.result as string) || '')
        reader.readAsText(file, 'utf-8')
      })
    }

    // PDF — 用 pdf.js
    if (name.endsWith('.pdf')) {
      try {
        const pdfjsLib = await import('https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.min.js' as any)
        pdfjsLib.GlobalWorkerOptions.workerSrc = 'https://cdnjs.cloudflare.com/ajax/libs/pdf.js/3.11.174/pdf.worker.min.js'
        const arrayBuffer = await file.arrayBuffer()
        const pdf = await pdfjsLib.getDocument({ data: arrayBuffer }).promise
        const parts: string[] = []
        for (let i = 1; i <= pdf.numPages; i++) {
          const page = await pdf.getPage(i)
          const content = await page.getTextContent()
          parts.push(content.items.map((item: any) => item.str).join(' '))
        }
        return `[文件：${file.name}]\n\n` + parts.join('\n')
      } catch(e) {
        return `[PDF解析失败：${file.name}]`
      }
    }

    // docx — 用 mammoth
    if (name.endsWith('.docx') || name.endsWith('.doc')) {
      try {
        const mammoth = await import('https://cdnjs.cloudflare.com/ajax/libs/mammoth/1.6.0/mammoth.browser.min.js' as any)
        const arrayBuffer = await file.arrayBuffer()
        const result = await mammoth.extractRawText({ arrayBuffer })
        return `[文件：${file.name}]\n\n` + result.value
      } catch(e) {
        return `[Word文档解析失败：${file.name}]`
      }
    }

    return `[不支持的文件格式：${file.name}]`
  }

  // 内联组件：等待页
  const WaitingView = ({ book, colors, onBack, onReady, onProgressUpdate }: {
    book: any
    colors: Record<string, string>
    onBack: () => void
    onReady: () => void
    onProgressUpdate: (current: number, total: number) => void
  }) => {
    const [progress, setProgress] = useState({ processed: 0, total: 0 })
    const pollingRef = useRef<ReturnType<typeof setInterval> | null>(null)

    useEffect(() => {
      const poll = async () => {
        try {
          const { getBookStatus } = await import('./services/readingApi')
          const status = await getBookStatus(book.id)
          setProgress({ processed: status.processed_chapters, total: status.total_chapters })
          if (onProgressUpdate) onProgressUpdate(status.processed_chapters, status.total_chapters)
          if (status.status === 'ready') {
            if (pollingRef.current) clearInterval(pollingRef.current)
            onReady()
          } else if (status.status === 'failed') {
            if (pollingRef.current) clearInterval(pollingRef.current)
            alert('处理失败')
            onBack()
          }
        } catch (error) {
          console.error('获取状态失败:', error)
        }
      }

      pollingRef.current = setInterval(poll, 3000)
      poll()

      return () => {
        if (pollingRef.current) clearInterval(pollingRef.current)
      }
    }, [book.id])

    return (
      <div style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        background: colors.bgPrimary,
        position: 'relative'
      }}>
        <style>{`
          @keyframes pageTurn {
            0%, 100% { transform: rotateY(0deg); }
            25% { transform: rotateY(-15deg); }
            75% { transform: rotateY(5deg); }
          }
          .page-left {
            animation: pageTurn 3s ease-in-out infinite;
            transform-origin: right center;
          }
          .page-right {
            animation: pageTurn 3s ease-in-out infinite 0.15s;
            transform-origin: right center;
          }
        `}</style>
        <button
          onClick={onBack}
          style={{
            padding: '12px 16px',
            background: 'transparent',
            border: 'none',
            color: colors.textSecondary,
            fontSize: '14px',
            textAlign: 'right',
            cursor: 'pointer'
          }}
        >
          ← 返回书架
        </button>
        <div style={{
          flex: 1,
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          gap: '20px'
        }}>
          <div style={{
            display: 'flex',
            gap: '4px'
          }}>
            <div style={{
              width: '40px',
              height: '56px',
              background: colors.bgCard,
              borderRadius: '2px',
              border: `1px solid ${colors.border}`
            }}
            className="page-left" />
            <div style={{
              width: '40px',
              height: '56px',
              background: colors.bgCard,
              borderRadius: '2px',
              border: `1px solid ${colors.border}`
            }}
            className="page-right" />
          </div>
          <div style={{
            fontSize: '16px',
            color: colors.textPrimary,
            fontWeight: 500
          }}>
            {label('Maverick正在读书……')}
          </div>
          <div style={{
            fontSize: '14px',
            color: colors.textSecondary
          }}>
            当前翻到第{progress.processed}章 / 共{progress.total}章
          </div>
        </div>
      </div>
    )
  }

  return (
    <LoginGate>
    <div
      style={{
        height: '100dvh',
        width: '100vw',
        overflowX: 'hidden',
        background: colors.bgPrimary,
        color: colors.textPrimary,
        fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Hiragino Sans GB", sans-serif',
        transition: 'background-color 0.3s ease, color 0.3s ease',
        overflow: 'hidden',
        fontWeight: 400,
        letterSpacing: '-0.005em'
      }}
    >
      {/* 移动端遮罩 */}
      {isMobile && drawerOpen && (
        <div
          onClick={() => setDrawerOpen(false)}
          style={{
            position: 'fixed',
            inset: 0,
            background: 'rgba(0,0,0,0.35)',
            backdropFilter: 'blur(3px)',
            zIndex: 40,
            animation: 'fadeIn 0.2s ease'
          }}
        />
      )}

      {/* 底部层：左侧抽屉 */}
      <aside
        style={{
          width: isMobile ? '75vw' : '300px',
          maxWidth: isMobile ? '280px' : '300px',
          height: '100%',
          background: colors.bgCard,
          position: isMobile ? 'fixed' : 'absolute',
          left: 0,
          top: 0,
          zIndex: isMobile ? 50 : 5,
          transform: isMobile
            ? drawerOpen
              ? 'translateX(0)'
              : 'translateX(-100%)'
            : 'translateX(0)',
          transition: 'transform 0.3s cubic-bezier(0.4, 0, 0.2, 1)',
          display: 'flex',
          flexDirection: 'column',
          borderRight: `1px solid ${colors.border}`,
          boxShadow: isMobile ? '4px 0 24px rgba(0,0,0,0.12)' : 'none',
          paddingBottom: isMobile ? (inputFocused ? '0px' : focusMode ? 'env(safe-area-inset-bottom, 8px)' : 'calc(56px + env(safe-area-inset-bottom, 8px))') : 0
        }}
      >
        {/* 抽屉标题 */}
        <div style={{
          padding: isMobile ? '16px 18px' : '20px 18px',
          borderBottom: `1px solid ${colors.border}`,
          background: '#796785'
        }}>
          <h2 style={{
            margin: 0,
            color: colors.titleText,
            fontSize: isMobile ? '16px' : '17px',
            fontWeight: 600,
            letterSpacing: '-0.02em',
            textAlign: 'left'
          }}>我的对话</h2>
        </div>

        {/* 对话列表 */}
        <div style={{
          flex: 1,
          overflow: 'auto',
          padding: '8px 12px 70px'
        }}>
          {orderedConversations.map((conv, index) => (
            <React.Fragment key={conv.id}>
            {index === activeConversations.length && (
              <button
                type="button"
                aria-expanded={historyExpanded}
                onClick={() => setHistoryExpanded((value) => !value)}
                style={{
                  display: 'flex', alignItems: 'center', justifyContent: 'space-between',
                  width: '100%', padding: '13px 14px', margin: '8px 0 4px',
                  border: 'none', borderTop: `1px solid ${colors.border}`,
                  background: 'transparent', color: colors.textSecondary,
                  fontSize: '12px', fontWeight: 600, cursor: 'pointer', textAlign: 'left'
                }}
              >
                <span>历史对话 · {historyConversations.length}</span>
                <span aria-hidden="true" style={{ transform: historyExpanded ? 'rotate(180deg)' : 'none', transition: 'transform 0.2s' }}>⌄</span>
              </button>
            )}
            {(index < activeConversations.length || historyExpanded) && (
            <div
              className="conversation-card"
              onClick={(e) => {
                if (suppressConversationClickRef.current) {
                  suppressConversationClickRef.current = false
                  e.preventDefault()
                  e.stopPropagation()
                  return
                }
                void chatStore.getState().selectConversation(conv.id)
                if (isMobile) setDrawerOpen(false)
              }}
              onTouchStart={(e) => {
                if (editingId === conv.id) return
                const touch = e.touches[0]
                const rect = e.currentTarget.getBoundingClientRect()
                if (touch) startConversationLongPress(conv.id, touch.clientX, touch.clientY, rect.top, rect.bottom)
              }}
              onTouchMove={(e) => {
                const touch = e.touches[0]
                const origin = longPressOriginRef.current
                if (touch && origin && Math.hypot(touch.clientX - origin.x, touch.clientY - origin.y) > 10) cancelConversationLongPress()
              }}
              onTouchEnd={(e) => {
                cancelConversationLongPress()
                if (suppressConversationClickRef.current) {
                  e.stopPropagation()
                  setTimeout(() => { suppressConversationClickRef.current = false }, 700)
                }
              }}
              onTouchCancel={cancelConversationLongPress}
              onContextMenu={(e) => {
                e.preventDefault()
                e.stopPropagation()
                cancelConversationLongPress()
                const rect = e.currentTarget.getBoundingClientRect()
                setContextMenu({
                  x: e.clientX,
                  y: e.clientY,
                  conversationId: conv.id,
                  anchorTop: rect.top,
                  anchorBottom: rect.bottom
                })
              }}
              style={{
                padding: '12px 14px',
                borderRadius: '10px',
                marginBottom: '5px',
                cursor: 'pointer',
                background: conv.id === currentConversationId ? `${colors.purpleMid}25` : 'transparent',
                border: conv.id === currentConversationId ? `1px solid ${colors.purpleMid}50` : '1px solid transparent',
                transition: 'all 0.2s ease',
                textAlign: 'left'
              }}
            >
              <div style={{
                display: 'flex',
                justifyContent: 'space-between',
                alignItems: 'center',
                marginBottom: '6px'
              }}>
                {editingId === conv.id ? (
                  <input
                    ref={el => el?.focus()}
                    value={editingTitle}
                    onChange={(e) => setEditingTitle(e.target.value)}
                    onKeyDown={(e) => {
                      if (e.key === 'Enter') {
                        e.preventDefault()
                        saveTitle()
                      } else if (e.key === 'Escape') {
                        setEditingId(null)
                        setEditingTitle('')
                      }
                    }}
                    onBlur={saveTitle}
                    onClick={(e) => e.stopPropagation()}
                    style={{
                      flex: 1,
                      fontSize: '13px',
                      padding: '4px 8px',
                      borderRadius: '6px',
                      border: `1px solid ${colors.border}`,
                      background: colors.bgCard,
                      color: colors.textPrimary,
                      outline: 'none'
                    }}
                  />
                ) : (
                  <span style={{
                    fontSize: '14px',
                    fontWeight: 500,
                    color: colors.textPrimary,
                    letterSpacing: '-0.01em',
                    flex: 1,
                    whiteSpace: 'nowrap',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis'
                  }}>{conv.title}</span>
                )}
                <span style={{
                  fontSize: '11px',
                  color: colors.textTertiary,
                  fontWeight: 400,
                  marginLeft: '8px',
                  flexShrink: 0
                }}>{formatTime(conv.updated_at)}</span>
              </div>

              <p style={{
                margin: 0,
                fontSize: '13px',
                color: colors.textSecondary,
                whiteSpace: 'nowrap',
                overflow: 'hidden',
                textOverflow: 'ellipsis',
                lineHeight: 1.5,
                textAlign: 'left'
              }}>{conv.last_message_preview || '新对话'}</p>
            </div>
            )}
            </React.Fragment>
          ))}

        {/* 上下文菜单 */}
        {contextMenu && createPortal((
          <>
            {/* 遮罩，关闭菜单 */}
            <div
              onClick={() => setContextMenu(null)}
              style={{
                position: 'fixed',
                inset: 0,
                zIndex: 1000
              }}
            />
            {/* 菜单 */}
            <div
              ref={conversationMenuRef}
              style={{
                position: 'fixed',
                left: isMobile ? '14px' : `${Math.max(8, Math.min(contextMenu.x, window.innerWidth - 180))}px`,
                right: 'auto',
                width: isMobile ? 'min(280px, calc(75vw - 28px))' : 'auto',
                top: isMobile ? `${contextMenu.y}px` : `${Math.max(8, Math.min(contextMenu.y, window.innerHeight - 160))}px`,
                background: colors.bgCard,
                borderRadius: '10px',
                boxShadow: `0 10px 40px ${colors.shadow}`,
                border: `1px solid ${colors.border}`,
                zIndex: 1001,
                minWidth: '160px',
                padding: '6px'
              }}
            >
              <button
                onClick={() => {
                  const conv = conversations.find(c => c.id === contextMenu?.conversationId)
                  if (conv) startEditing(conv.id, conv.title)
                }}
                style={{
                  width: '100%',
                  padding: '10px 14px',
                  borderRadius: '6px',
                  border: 'none',
                  background: 'transparent',
                  color: colors.textPrimary,
                  fontSize: '13px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  transition: 'background-color 0.15s ease',
                  textAlign: 'left'
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = colors.bgSurface}
                onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
              >
                <Icon name="edit" size={14} />
                重命名
              </button>
              {menuConversation && menuConversation.id !== latestConversationId && (
                <button
                  onClick={async () => {
                    const conv = menuConversation
                    setContextMenu(null)
                    const folded = conv.is_folded === 0
                    try {
                      await chatStore.getState().foldConversation(conv.id, folded)
                      if (!folded) setHistoryExpanded(true)
                    } catch (error) {
                      console.error('[foldConversation] Failed:', error)
                      alert('未能保存对话折叠状态，请稍后重试')
                    }
                  }}
                  style={{
                    width: '100%', padding: '10px 14px', borderRadius: '6px', border: 'none',
                    background: 'transparent', color: colors.textPrimary, fontSize: '13px',
                    fontWeight: 500, cursor: 'pointer', display: 'flex', alignItems: 'center',
                    gap: '8px', textAlign: 'left'
                  }}
                  onMouseEnter={(e) => e.currentTarget.style.background = colors.bgSurface}
                  onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
                >
                  <span aria-hidden="true">{menuConversation.is_folded === 0 ? '⌄' : '⌃'}</span>
                  {menuConversation.is_folded === 0 ? '移入历史折叠' : '移出历史折叠'}
                </button>
              )}
              <button
                onClick={async () => {
                  setContextMenu(null)
                  const conv = conversations.find(c => c.id === contextMenu?.conversationId)
                  if (conv && confirm(`确定要删除"${conv.title}"吗？`)) {
                    await chatStore.getState().deleteConversation(conv.id)
                  }
                }}
                style={{
                  width: '100%',
                  padding: '10px 14px',
                  borderRadius: '6px',
                  border: 'none',
                  background: 'transparent',
                  color: '#ef4444',
                  fontSize: '13px',
                  fontWeight: 500,
                  cursor: 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  transition: 'background-color 0.15s ease',
                  textAlign: 'left'
                }}
                onMouseEnter={(e) => e.currentTarget.style.background = `${colors.bgSurface}40`}
                onMouseLeave={(e) => e.currentTarget.style.background = 'transparent'}
              >
                <Icon name="close" size={14} />
                删除
              </button>
            </div>
          </>
        ), document.body)}

        </div>

        {/* 底部操作区 */}

        {/* 底部操作区 */}
        <div style={{
          padding: isMobile ? '12px 14px 12px' : '14px 16px 16px',
          borderTop: `1px solid ${colors.border}`,
                      borderRadius: '16px 16px 0 0',
          display: 'flex',
          justifyContent: 'space-between',
          alignItems: 'center',
          gap: '10px',
          background: `${colors.bgSurface}40`
        }}>
          <button style={{
            flex: 1,
            background: `linear-gradient(135deg, ${colors.primaryHover} 0%, ${colors.primary} 100%)`,
            color: '#FFFFFF',
            border: 'none',
            padding: '10px 14px',
            borderRadius: '8px',
            fontSize: '13px',
            fontWeight: 500,
            cursor: 'pointer',
            transition: 'all 0.2s ease',
            display: 'flex',
            alignItems: 'center',
            gap: '6px',
            justifyContent: 'center',
            letterSpacing: '-0.01em',
            boxShadow: `0 2px 8px ${colors.purpleMid}40`
          }}
          onClick={async () => {
            const newId = await chatStore.getState().createConversation()
            await chatStore.getState().selectConversation(newId)
          }}>
            <Icon name="plus" size={15} color={colors.textPrimary} />
            新建对话
          </button>
          <div style={{ display: 'flex', gap: '6px' }}>
            <button style={{
              width: '38px',
              height: '38px',
              borderRadius: '8px',
              background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
              border: `1px solid ${colors.purpleMid}40`,
              cursor: 'pointer',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              transition: 'all 0.2s ease'
            }}
            onClick={() => setSettingsOpen(true)}>
              <Settings size={17} color={colors.purpleDark} strokeWidth={1.5} />
            </button>
            <button
              onClick={handleThemeToggle}
              style={{
                width: '38px',
                height: '38px',
                borderRadius: '8px',
                background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                border: `1px solid ${colors.purpleMid}40`,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.2s ease'
              }}
            >
              {theme === 'light' ? <Moon size={17} color={colors.purpleDark} strokeWidth={1.5} /> : <Sun size={17} color={colors.purpleDark} strokeWidth={1.5} />}
            </button>
          </div>
        </div>
      </aside>

      {/* 主内容区 - 桌面端显示，移动端全宽 */}
      <main style={{
        height: '100%',
        display: 'flex',
        flexDirection: 'column',
        marginLeft: isMobile ? 0 : '300px',
        overflow: 'hidden',
        backgroundColor: colors.bgPrimary,
        flex: 1,
        minWidth: 0,
        position: 'relative'
      }}>
        {/* 阅读tab - 移动端显示书架，桌面端提示 */}
        {activeTab === 'reading' && (
          <>
            {isMobile ? (
              <>
                {readingView === 'shelf' && (
                  <BookShelf
                    colors={colors}
                    isMobile={isMobile}
                    onOpenBook={(book: any) => {
                      setCurrentBook(book)
                      setReaderFocusMode(false)
                      setReadingView('reader')
                    }}
                    onProcessBook={(book: any) => {
                      setCurrentBook(book)
                      setReadingView('waiting')
                    }}
                  />
                )}
                {readingView === 'waiting' && currentBook && (
                  <WaitingView
                    book={currentBook}
                    colors={colors}
                    onBack={() => setReadingView('shelf')}
                    onReady={() => {
                      setActiveTab('dialog')
                      setReadingView('shelf')
                      setCurrentBook(null)
                    }}
                    onProgressUpdate={(current: number, total: number) => {
                      setWaitingProgress({ current, total })
                    }}
                  />
                )}
                {readingView === 'reader' && currentBook && (
                  <BookReader
                    book={currentBook}
                    onBack={() => {
                      setReaderFocusMode(false)
                      setReadingView('shelf')
                    }}
                    colors={colors}
                    isMobile={isMobile}
                    focusMode={readerFocusMode}
                    onToggleFocus={() => setReaderFocusMode(f => !f)}
                  />
                )}
              </>
            ) : (
              <div style={{
                height: '100%',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                flexDirection: 'column',
                gap: '12px'
              }}
              >
                <Icon name="book" size={64} color={colors.purplePale} />
                <div style={{
                  fontSize: '16px',
                  color: colors.textSecondary,
                  textAlign: 'center'
                }}
                >
                  仅移动端可用
                </div>
              </div>
            )}
          </>
        )}

        {activeTab === 'note' && (
          <NotesView colors={colors} isMobile={isMobile} />
        )}

        {activeTab === 'us' && (
          <div style={{
            height: '100%',
            overflowY: 'auto',
            padding: isMobile
              ? '22px 18px calc(76px + env(safe-area-inset-bottom, 8px))'
              : '34px 42px',
            boxSizing: 'border-box'
          }}>
            <div style={{
              maxWidth: '720px',
              margin: '0 auto'
            }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                gap: '16px',
                marginBottom: '20px'
              }}>
                <div>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '10px',
                    marginBottom: '6px'
                  }}>
                    <HeartPulse size={22} color={colors.primary} strokeWidth={1.6} />
                    <h1 style={{
                      margin: 0,
                      color: colors.textPrimary,
                      fontSize: isMobile ? '22px' : '26px',
                      fontWeight: 650,
                      letterSpacing: '0'
                    }}>
                      {label('Mav的小本本')}
                    </h1>
                  </div>
                  <div style={{
                    color: colors.textTertiary,
                    fontSize: '13px',
                    lineHeight: 1.6,
                    fontStyle: 'italic'
                  }}>
                    {label('Mav 写给未来自己的提醒')}
                  </div>
                </div>

                <button
                  onClick={loadMavNotes}
                  disabled={notesLoading}
                  title="刷新"
                  style={{
                    width: '38px',
                    height: '38px',
                    borderRadius: '10px',
                    border: `1px solid ${colors.purpleMid}40`,
                    background: `linear-gradient(135deg, ${colors.purpleMid}18 0%, ${colors.primaryLight} 100%)`,
                    cursor: notesLoading ? 'default' : 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    flexShrink: 0,
                    opacity: notesLoading ? 0.55 : 1
                  }}
                >
                  <RefreshCw
                    size={16}
                    color={colors.purpleDark}
                    strokeWidth={1.6}
                    style={{ animation: notesLoading ? 'spin 0.9s linear infinite' : 'none' }}
                  />
                </button>
              </div>

              <div style={{
                display: 'flex',
                flexDirection: 'column',
                gap: '12px'
              }}>
                {notesLoading && mavNotes.length === 0 && (
                  <div style={{
                    padding: '26px 0',
                    color: colors.textTertiary,
                    fontSize: '14px',
                    textAlign: 'center',
                    fontStyle: 'italic'
                  }}>
                    正在翻小本本...
                  </div>
                )}

                {notesError && (
                  <div style={{
                    padding: '16px',
                    border: `1px solid ${colors.border}`,
                    borderRadius: '8px',
                    color: colors.textSecondary,
                    background: colors.bgCard,
                    fontSize: '14px'
                  }}>
                    {notesError}
                  </div>
                )}

                {!notesLoading && !notesError && mavNotes.length === 0 && (
                  <div style={{
                    padding: '34px 18px',
                    border: `1px dashed ${colors.purpleMid}`,
                    borderRadius: '8px',
                    color: colors.textTertiary,
                    background: `${colors.bgCard}80`,
                    fontSize: '14px',
                    lineHeight: 1.8,
                    textAlign: 'center'
                  }}>
                    这里还没有便签
                  </div>
                )}

                {mavNotes.map((note) => (
                  <article
                    key={note.id}
                    style={{
                      background: colors.bgCard,
                      border: `1px solid ${colors.border}`,
                      borderRadius: '8px',
                      padding: isMobile ? '15px 16px' : '17px 18px',
                      boxShadow: `0 6px 20px ${colors.shadow}`,
                      position: 'relative',
                      overflow: 'hidden'
                    }}
                  >
                    <div style={{
                      position: 'absolute',
                      left: 0,
                      top: 0,
                      bottom: 0,
                      width: '3px',
                      background: `linear-gradient(180deg, ${colors.primary} 0%, ${colors.purpleMid} 100%)`,
                      opacity: 0.75
                    }} />
                    <div style={{
                      whiteSpace: 'pre-wrap',
                      color: colors.textPrimary,
                      fontSize: isMobile ? '14px' : '15px',
                      lineHeight: 1.85,
                      letterSpacing: '0',
                      paddingLeft: '4px'
                    }}>
                      {note.content}
                    </div>
                    <div style={{
                      marginTop: '12px',
                      color: colors.textTertiary,
                      fontSize: '12px',
                      fontStyle: 'italic',
                      display: 'flex',
                      justifyContent: 'flex-end'
                    }}>
                      {formatNoteTime(note.created_at)}
                    </div>
                  </article>
                ))}
              </div>
            </div>
          </div>
        )}

        {/* 对话窗口容器 - 完全响应式，无固定宽度限制 */}
        {activeTab === 'dialog' && (
          <div style={{
            height: '100%',
            display: 'flex',
            flexDirection: 'column',
            width: '100%',
            minWidth: 0,
            boxSizing: 'border-box',
            overflow: 'hidden',
          paddingBottom: isMobile ? (inputFocused ? '0px' : focusMode ? 'env(safe-area-inset-bottom, 8px)' : 'calc(56px + env(safe-area-inset-bottom, 8px))') : 0
        }}>
          {/* 顶部栏 */}
          <header style={{
            height: isMobile ? 'calc(54px + env(safe-area-inset-top, 0px))' : '60px',
            background: colors.bgPrimary,
            borderBottom: `1px solid ${colors.border}`,
            display: 'flex',
            alignItems: 'center',
            paddingTop: isMobile ? 'env(safe-area-inset-top, 0px)' : 0,
            paddingBottom: '0px',
            paddingLeft: isMobile ? '14px' : '18px',
            paddingRight: isMobile ? '14px' : '18px',
            gap: '14px',
            flexShrink: 0
          }}>
            {/* 汉堡菜单(移动端) */}
            {isMobile && (
              <button
                onClick={() => setDrawerOpen(true)}
                style={{
                  width: '38px',
                  height: '38px',
                  background: 'transparent',
                  border: 'none',
                  cursor: 'pointer',
                  borderRadius: '8px',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all 0.2s ease'
                }}
              >
                <Icon name="menu" size={19} color={colors.purpleDark} />
              </button>
            )}

            <div style={{
              flex: 1,
              textAlign: 'left'
            }}>
              <h2 style={{
                margin: 0,
                fontSize: isMobile ? '15px' : '16px',
                color: colors.textPrimary,
                fontWeight: 600,
                letterSpacing: '-0.02em'
              }}>
                {currentConversationId
                  ? (conversations.find(c => c.id === currentConversationId)?.title || '对话')
                  : '未选择对话'
                }
              </h2>
            </div>

            <div style={{ display: 'flex', gap: '8px' }}>
              <button onClick={() => { setMapOpen(true); setMapSearch(''); setMapSearchActive(false) }} style={{
                width: '38px',
                height: '38px',
                borderRadius: '8px',
                background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                border: `1px solid ${colors.purpleMid}40`,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.2s ease'
              }}>
                <MapPlus size={17} color={colors.purpleDark} strokeWidth={1.5} />
              </button>
              {isMobile && (
              <button onClick={() => setFocusMode(f => !f)} style={{
                width: '38px',
                height: '38px',
                borderRadius: '8px',
                background: focusMode
                  ? `linear-gradient(135deg, ${colors.purpleMid}40 0%, ${colors.purpleMid}55 100%)`
                  : `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                border: `1px solid ${colors.purpleMid}40`,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.2s ease'
              }}>
                {focusMode
                  ? <Minimize2 size={17} color={colors.purpleDark} strokeWidth={1.5} />
                  : <Maximize2 size={17} color={colors.purpleDark} strokeWidth={1.5} />
                }
              </button>
              )}
              {!isMobile && (
              <button style={{
                width: '38px',
                height: '38px',
                borderRadius: '8px',
                background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                border: `1px solid ${colors.purpleMid}40`,
                cursor: 'pointer',
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                transition: 'all 0.2s ease'
              }}
              onClick={() => setSettingsOpen(true)}>
                <Settings size={17} color={colors.purpleDark} strokeWidth={1.5} />
              </button>
              )}
            </div>
          </header>

          {/* 消息区域 */}
          <div
            ref={messagesContainerRef}
            onLoadCapture={keepBottomAfterMediaLoad}
            className="chat-messages-scroll"
            style={{
              flex: 1,
              padding: '0 24px',
              overflowY: 'auto',
              overflowX: 'hidden',
              backgroundColor: colors.bgPrimary,
              WebkitOverflowScrolling: 'touch',
              overscrollBehaviorX: 'none',
              touchAction: 'pan-y',
              scrollBehavior: 'smooth',
              width: '100%',
              boxSizing: 'border-box',
              minWidth: 0,
              maxWidth: '760px',
              margin: '0 auto'
            }}
          >
            {isLoadingConversation && (
              <div style={{
                padding: '28px 0 18px',
                textAlign: 'center',
                color: colors.textTertiary,
                fontSize: '13px',
                letterSpacing: '0.02em'
              }}>
                正在打开这间房…
              </div>
            )}
            {!isLoadingConversation && (hasOlderMessages || isLoadingOlderMessages) && (
              <div style={{ display: 'flex', justifyContent: 'center', padding: '14px 0 20px' }}>
                <button
                  type="button"
                  onClick={() => void loadOlderMessagesAtTop()}
                  disabled={isLoadingOlderMessages || isStreaming}
                  style={{
                    padding: '6px 12px',
                    border: 'none',
                    background: 'transparent',
                    color: colors.textTertiary,
                    fontSize: '12px',
                    cursor: isLoadingOlderMessages || isStreaming ? 'default' : 'pointer',
                    opacity: isLoadingOlderMessages ? 0.72 : 1
                  }}
                >
                  {isLoadingOlderMessages ? '正在翻更早的对话…' : '翻看更早的对话'}
                </button>
              </div>
            )}
            {Array.isArray(messages) && messages.map((msg) => {
              const hasMavNoteNotice = msg.role === 'assistant' && (msg.content || '').includes('[[mav_note_saved]]')
              const hasMavPortraitNotice = msg.role === 'assistant' && (msg.content || '').includes('[[mav_portrait_updated]]')
              const hasMavPortraitFailureNotice = msg.role === 'assistant' && (msg.content || '').includes('[[mav_portrait_update_failed]]')
              const hasMavMemoryRecallNotice = msg.role === 'assistant' && (msg.content || '').includes('[[mav_memory_recalled]]')
              const assistantContent = msg.role === 'assistant'
                ? (msg.content || '')
                    .replace(/\n*\[\[mav_note_saved\]\]\n*/g, '')
                    .replace(/\n*\[\[mav_portrait_updated\]\]\n*/g, '')
                    .replace(/\n*\[\[mav_portrait_update_failed\]\]\n*/g, '')
                    .replace(/\n*\[\[mav_memory_recalled\]\]\n*/g, '')
                    .trim()
                : msg.content
              const isEditingThisMessage = editingMessageId === msg.id
              const canEditUserMessage = msg.role === 'user' && !(msg.images?.length) && !msg.imagePreview && !msg.fileName && !isStreaming
              const usageParts = [
                settings.showTokenUsage && msg.totalTokens
                  ? formatTokenCount(msg.totalTokens)
                  : '',
                settings.showCacheUsage && msg.cacheReadTokens
                  ? `缓存命中 ${formatTokenCount(msg.cacheReadTokens)}`
                  : '',
                settings.showCacheUsage && msg.cacheCreationTokens
                  ? `${msg.cacheCreation1hTokens ? '1h缓存写入' : '缓存写入'} ${formatTokenCount(msg.cacheCreationTokens)}`
                  : ''
              ].filter(Boolean)

              return (
              <div
                key={msg.id}
                id={`msg-${msg.id}`}
                style={{
                  display: 'flex',
                  justifyContent: msg.role === 'user' ? 'flex-end' : 'flex-start',
                  marginBottom: '30px',
                  textAlign: 'left'
                }}
              >
                {/* Assistant消息 - 无气泡，直接在背景上 */}
                {msg.role === 'assistant' ? (
                  <div style={{
                    display: 'flex',
                    alignItems: 'flex-start',
                    width: '100%',
                    maxWidth: '100%',
                    minWidth: 0,
                    gap: '10px',
                    textAlign: 'left'
                  }}>
                    <span
                      className={`assistant-heart-emoji${isStreaming && msg === messages[messages.length - 1] ? ' assistant-heart-emoji--active' : ''}`}
                      aria-hidden="true"
                    >🧡</span>
                    {/* Markdown 内容渲染 */}
                    <div style={{
                      padding: 0,
                      color: colors.textPrimary,
                      fontSize: isMobile ? '15px' : '15px',
                      lineHeight: 1.8,
                      letterSpacing: '-0.008em',
                      position: 'relative',
                      textAlign: 'left',
                      flex: 1,
                      minWidth: 0,
                      overflowWrap: 'anywhere',
                      wordBreak: 'break-word'
                    }}>
                      {/* ThinkingBlock */}
                      {(msg.thinking || (isThinkingActive && msg === messages[messages.length - 1])) && (
                        <div style={{
                          marginBottom: '16px',
                          borderRadius: '10px',
                          border: '1px solid #E5E3EA',
                          background: 'transparent',
                          overflow: 'hidden'
                        }}>
                          <button
                            onClick={() =>
                              !isThinkingActive && setExpandedThinking(prev => ({
                                ...prev,
                                [msg.id]: !prev[msg.id]
                              }))}
                            style={{
                              width: '100%',
                              padding: '10px 14px',
                              background: '#F0EEF4',
                              border: 'none',
                              cursor: isThinkingActive ? 'default' : 'pointer',
                              display: 'flex',
                              alignItems: 'center',
                              justifyContent: 'flex-start',
                              gap: '12px',
                              fontSize: isMobile ? '12px' : '13px',
                              color: '#716E85',
                              transition: 'all 0.2s ease',
                              textAlign: 'left'
                            }}
                          >
                            <div style={{
                              width: '3px',
                              height: '14px',
                              background: isThinkingActive ? colors.purpleMid : '#D3D1DE',
                              borderRadius: '1px',
                              flexShrink: 0,
                              animation: isThinkingActive ? 'heartbeat 0.8s ease-in-out infinite' : 'none'
                            }} />
                            <span style={{ fontWeight: 500, whiteSpace: 'nowrap' }}>
                              {isThinkingActive && msg === messages[messages.length - 1]
                                ? '思考中...'
                                : `已思考${expandedThinking[msg.id] ? '' : ' ▼'}`
                              }
                            </span>
                          </button>
                          <div style={{
                            padding: expandedThinking[msg.id] ? '12px 14px 14px 30px' : '0 14px 0 30px',
                            maxHeight: expandedThinking[msg.id] ? 'none' : '0',
                            overflow: 'hidden',
                            fontSize: isMobile ? '13px' : '14px',
                            lineHeight: 1.6,
                            color: '#716E85',
                            transition: 'all 0.3s ease',
                            background: 'transparent'
                          }}>
                            <div style={{ whiteSpace: 'pre-wrap', margin: 0, display: expandedThinking[msg.id] ? 'block' : 'none' }}>
                              {msg.thinking}
                            </div>
                          </div>
                        </div>
                      )}

                      <ArtJobStatus job={msg.artJob} dark={theme === 'dark'} />

                      {hasMavMemoryRecallNotice && (
                        <div role="status" style={{
                          marginBottom: '12px', color: colors.textTertiary,
                          fontSize: isMobile ? '12px' : '13px', fontStyle: 'italic'
                        }}>{label('Mav想起了更多往事')}</div>
                      )}

                      <ChatImageGallery
                        images={msg.images}
                        legacyUrl={msg.imagePreview}
                        dark={theme === 'dark'}
                      />

                      <AssistantMessageSegments
                        content={assistantContent}
                        styleMode={settings.assistantMessageStyle}
                        streaming={isStreaming && msg === messages[messages.length - 1]}
                        dark={theme === 'dark'}
                        searchNotice={(
                            <div style={{
                              margin: '8px 0',
                              color: colors.textTertiary,
                              fontSize: isMobile ? '11px' : '12px',
                              lineHeight: 1,
                              letterSpacing: '0',
                              fontStyle: 'italic',
                              fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                            }}>
                              {label('Mav翻了翻小窗口')}
                            </div>
                        )}
                        memoryPendingNotice={(
                          <div style={{
                            margin: '8px 0', color: colors.textTertiary,
                            fontSize: isMobile ? '11px' : '12px', lineHeight: 1,
                            fontStyle: 'italic',
                            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                          }}>{label('Mav 正在回忆往事…')}</div>
                        )}
                        memoryDoneNotice={(
                          <div style={{
                            margin: '8px 0', color: colors.textTertiary,
                            fontSize: isMobile ? '11px' : '12px', lineHeight: 1,
                            fontStyle: 'italic',
                            fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                          }}>{label('Mav想起了更多往事')}</div>
                        )}
                        renderText={(contentPart) => (
                      <ReactMarkdown
                        remarkPlugins={[remarkGfm]}
                        components={{
                          table({ children }) {
                            return (
                              <div style={{ overflowX: 'auto', margin: '12px 0' }}>
                                <table style={{
                                  borderCollapse: 'collapse',
                                  width: '100%',
                                  fontSize: '13px',
                                  lineHeight: 1.6
                                }}>{children}</table>
                              </div>
                            )
                          },
                          th({ children }) {
                            return <th style={{
                              padding: '8px 12px',
                              background: '#F0EEF4',
                              borderBottom: '2px solid #D3D1DE',
                              textAlign: 'left',
                              fontWeight: 600,
                              whiteSpace: 'nowrap'
                            }}>{children}</th>
                          },
                          td({ children }) {
                            return <td style={{
                              padding: '7px 12px',
                              borderBottom: '1px solid #E9E6EA'
                            }}>{children}</td>
                          },
                          code({ node, className, children, ...props }) {
                            const match = /language-(\w+)/.exec(className || '')
                            const codeContent = String(children).replace(/\n$/, '')
                            const codeId = codeContent.slice(0, 20).replace(/\s/g, '')

                            if (match) {
                              const handleCopy = () => {
                                navigator.clipboard.writeText(codeContent)
                                setCopiedCode(codeId)
                                setTimeout(() => setCopiedCode(null), 1500)
                              }

                              return (
                                <div style={{
                                  maxWidth: '100%',
                                  boxSizing: 'border-box',
                                  position: 'relative',
                                  borderRadius: '8px',
                                  border: '1px solid #D3D1DE',
                                  overflowX: 'hidden',
                                  backgroundColor: '#F5F3F7',
                                  color: '#2D2B35',
                                  padding: '12px 16px',
                                  margin: '8px 0',
                                  fontSize: '10px',
                                  fontFamily: "'JetBrains Mono', 'Fira Code', monospace",
                                  lineHeight: 1.6
                                }}>
                                  <button
                                    onClick={handleCopy}
                                    style={{
                                      position: 'absolute',
                                      top: '8px',
                                      right: '32px',
                                      padding: '6px',
                                      borderRadius: '4px',
                                      border: 'none',
                                      background: 'transparent',
                                      cursor: 'pointer',
                                      transition: 'all 0.2s ease',
                                      zIndex: 10
                                    }}
                                  >
                                    {copiedCode === codeId ? (
                                      <CheckIcon size={16} color="#E98E6B" />
                                    ) : (
                                      <CopyIcon size={16} color="#716E85" />
                                    )}
                                  </button>
                                  <pre style={{
                                    margin: 0,
                                    whiteSpace: 'pre',
                                    wordBreak: 'normal',
                                    overflowX: 'auto',
                                    overflowY: 'auto',
                                    maxHeight: '400px',
                                    paddingRight: '32px'
                                  }}>
                                    <code style={{ whiteSpace: 'pre', wordBreak: 'normal' }}>{codeContent}</code>
                                  </pre>
                                </div>
                              )
                            }
                            return (
                              <code
                                className={className}
                                style={{
                                  backgroundColor: '#F5F3F7',
                                  color: '#2D2B35',
                                  padding: '2px 6px',
                                  borderRadius: '4px',
                                  fontSize: '0.9em',
                                  fontFamily: "'JetBrains Mono', 'Fira Code', monospace"
                                }}
                                {...props}
                              >
                                {children}
                              </code>
                            )
                          }
                        }}
                      >
                        {contentPart.trim()}
                      </ReactMarkdown>
                        )}
                      />

                      {hasMavNoteNotice && (
                        <div style={{
                          marginTop: assistantContent ? '10px' : '0',
                          color: colors.textTertiary,
                          fontSize: isMobile ? '12px' : '13px',
                          fontStyle: 'italic',
                          lineHeight: 1.6,
                          letterSpacing: '0',
                          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                        }}>
                          {label('Mav 记下了一条便签')}
                        </div>
                      )}

                      {hasMavPortraitNotice && (
                        <div style={{
                          marginTop: assistantContent || hasMavNoteNotice ? '6px' : '0',
                          color: colors.textTertiary,
                          fontSize: isMobile ? '12px' : '13px',
                          fontStyle: 'italic',
                          lineHeight: 1.6,
                          letterSpacing: '0',
                          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                        }}>
                          {label('Mav对岚有了新的认识')}
                        </div>
                      )}

                      {hasMavPortraitFailureNotice && (
                        <div style={{
                          marginTop: assistantContent || hasMavNoteNotice ? '6px' : '0',
                          color: colors.textTertiary,
                          fontSize: isMobile ? '12px' : '13px',
                          fontStyle: 'italic',
                          lineHeight: 1.6,
                          letterSpacing: '0',
                          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                        }}>
                          画像更新未能写入
                        </div>
                      )}

                      {usageParts.length > 0 ? (
                        <div style={{
                          marginTop: '8px',
                          color: colors.textTertiary,
                          fontSize: isMobile ? '11px' : '12px',
                          lineHeight: 1,
                          letterSpacing: '0',
                          fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", sans-serif'
                        }}>
                          {usageParts.join(' · ')}
                        </div>
                      ) : null}

                  </div>
                  </div>
                ) : (
                  /* User消息 - 右侧紫色气泡 */
                  <div style={{
                    maxWidth: '70%',
                    minWidth: 0,
                    display: 'flex',
                    flexDirection: 'column',
                    alignItems: 'flex-end'
                  }}>
                    <div style={{
                      maxWidth: '100%',
                      padding: isMobile ? '11px 15px' : '13px 17px',
                      borderRadius: '12px',
                      background: `linear-gradient(135deg, #ded5e8 0%, #c3b4d6 100%)`,
                      color: '#2D2B35',
                      fontSize: isMobile ? '15px' : '15px',
                      lineHeight: 1.7,
                      letterSpacing: '-0.008em',
                      wordBreak: 'break-word',
                      textAlign: 'left',
                      boxShadow: `0 2px 8px ${colors.purpleMid}30`
                    }}>
                    <ChatImageGallery
                      images={msg.images}
                      legacyUrl={msg.imagePreview}
                      dark={theme === 'dark'}
                    />
                    {msg.fileName && (
                      <div style={{
                        display: 'flex', alignItems: 'center', gap: '8px',
                        background: 'rgba(255,255,255,0.25)', borderRadius: '8px',
                        padding: '8px 10px', marginBottom: msg.content ? '8px' : 0
                      }}>
                        <div style={{
                          width: '32px', height: '32px', borderRadius: '6px',
                          background: 'rgba(255,255,255,0.3)',
                          display: 'flex', flexDirection: 'column',
                          alignItems: 'center', justifyContent: 'center', gap: '2px', flexShrink: 0
                        }}>
                          <Icon name="edit" size={14} color="#2D2B35" />
                          <span style={{ fontSize: '7px', color: '#2D2B35', fontWeight: 700 }}>
                            {msg.fileName.split('.').pop()?.toUpperCase()}
                          </span>
                        </div>
                        <span style={{ fontSize: '12px', color: '#2D2B35', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                          {msg.fileName}
                        </span>
                      </div>
                    )}
                    {isEditingThisMessage ? (
                      <div style={{ minWidth: isMobile ? '220px' : '320px' }}>
                        <textarea
                          autoFocus
                          value={editingMessageText}
                          onChange={(e) => setEditingMessageText(e.target.value)}
                          style={{
                            width: '100%',
                            minHeight: '92px',
                            maxHeight: '220px',
                            fieldSizing: 'content',
                            resize: 'vertical',
                            boxSizing: 'border-box',
                            border: '1px solid rgba(45,43,53,0.18)',
                            borderRadius: '10px',
                            background: 'rgba(255,255,255,0.42)',
                            color: '#2D2B35',
                            padding: '9px 10px',
                            fontSize: isMobile ? '15px' : '15px',
                            lineHeight: 1.7,
                            fontFamily: 'inherit',
                            outline: 'none'
                          }}
                        />
                        <div style={{
                          display: 'flex',
                          justifyContent: 'flex-end',
                          gap: '8px',
                          marginTop: '9px'
                        }}>
                          <button
                            onClick={cancelMessageEdit}
                            style={{
                              border: 'none',
                              background: 'rgba(255,255,255,0.3)',
                              color: colors.textSecondary,
                              borderRadius: '8px',
                              padding: '6px 10px',
                              cursor: 'pointer',
                              fontSize: '12px'
                            }}
                          >
                            取消
                          </button>
                          <button
                            onClick={saveMessageEdit}
                            disabled={!editingMessageText.trim()}
                            style={{
                              border: 'none',
                              background: editingMessageText.trim() ? colors.purpleDark : 'rgba(45,43,53,0.18)',
                              color: '#fff',
                              borderRadius: '8px',
                              padding: '6px 11px',
                              cursor: editingMessageText.trim() ? 'pointer' : 'default',
                              fontSize: '12px'
                            }}
                          >
                            重新发送
                          </button>
                        </div>
                      </div>
                    ) : msg.content && (
                      <pre style={{whiteSpace: 'pre-wrap', fontFamily: 'inherit', margin: 0}}>{msg.content}</pre>
                    )}
                    </div>
                    {(settings.showMessageTimestamps || (canEditUserMessage && !isEditingThisMessage)) && <div style={{
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'flex-end',
                      gap: '7px',
                      marginTop: '7px',
                      paddingRight: '2px',
                      color: colors.textTertiary,
                      fontSize: isMobile ? '11px' : '12px',
                      lineHeight: 1,
                      letterSpacing: '0'
                    }}>
                      {settings.showMessageTimestamps && <span>{formatMessageTime(msg.created_at)}</span>}
                      {canEditUserMessage && !isEditingThisMessage && (
                        <button
                          onClick={() => startMessageEdit(msg.id, msg.content)}
                          title="编辑并重新生成"
                          style={{
                            width: '22px',
                            height: '22px',
                            borderRadius: '6px',
                            border: 'none',
                            background: 'transparent',
                            cursor: 'pointer',
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center',
                            padding: 0,
                            color: colors.textTertiary,
                            opacity: 0.58
                          }}
                        >
                          <Icon name="edit" size={12} color={colors.textTertiary} />
                        </button>
                      )}
                    </div>}
                  </div>
                )}
              </div>
            )})}
            <div ref={messagesEndRef} />
          </div>

          {currentConversationId && messageTotal >= 800 && !longConversationReminderDismissed && (
            <div style={{
              width: 'calc(100% - 48px)',
              maxWidth: '712px',
              margin: '0 auto 8px',
              padding: '9px 12px',
              boxSizing: 'border-box',
              display: 'flex',
              alignItems: 'center',
              gap: '10px',
              color: colors.textSecondary,
              background: theme === 'light' ? '#F7F0EC' : colors.bgSurface,
              border: `1px solid ${colors.primary}2E`,
              borderRadius: '10px',
              fontSize: '12px',
              lineHeight: 1.55,
              flexShrink: 0
            }}>
              <span style={{ flex: 1 }}>
                这间房已经装下很多对话了（约 {Math.floor(messageTotal / 2)} 轮）。历史不会丢失；如果想让页面更轻快，可以考虑新开一间房。
              </span>
              <button
                type="button"
                onClick={dismissLongConversationReminder}
                aria-label="关闭长对话提醒"
                style={{
                  width: '24px',
                  height: '24px',
                  padding: 0,
                  border: 'none',
                  borderRadius: '7px',
                  background: 'transparent',
                  color: colors.textTertiary,
                  cursor: 'pointer',
                  fontSize: '17px',
                  lineHeight: 1,
                  flexShrink: 0
                }}
              >
                ×
              </button>
            </div>
          )}

          {/* 输入区域 */}
          <div style={{
            padding: isMobile && focusMode ? '12px 24px 16px' : '0 24px 16px',
            borderTop: `1px solid ${colors.border}`,
            flexShrink: 0,
            width: '100%',
            maxWidth: '760px',
            margin: '0 auto',
            boxSizing: 'border-box',
            backgroundColor: colors.bgCard,
            borderRadius: '16px'
          }}>
            {/* 功能标签 - 紫色主题 */}
            {(!isMobile || !focusMode) && <div style={{
              display: 'flex',
              gap: '8px',
              marginBottom: '10px',
              flexWrap: 'wrap',
              paddingTop: '12px',
              paddingBottom: '8px'
            }}>
              <span
                onClick={() => setThinkingEnabled(v => !v)}
                title="深度思考"
                style={{
                  width: '28px', height: '28px', padding: 0, borderRadius: '8px',
                  background: thinkingEnabled
                    ? `linear-gradient(135deg, ${colors.purpleMid}60 0%, ${colors.purpleMid}80 100%)`
                    : `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                  color: colors.purpleDark, cursor: 'pointer', transition: 'all 0.2s ease',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontWeight: 500,
                  border: thinkingEnabled ? `1px solid ${colors.purpleMid}` : `1px solid ${colors.purpleMid}40`,
                  boxShadow: thinkingEnabled ? `0 0 8px ${colors.purpleMid}40` : 'none'
                }}>
                <Sparkles size={14} color={thinkingEnabled ? colors.purpleDark : colors.purpleMid} strokeWidth={1.5} />
              </span>
              <span
                onClick={() => setSearchEnabled(v => !v)}
                title="联网搜索"
                style={{
                  width: '28px', height: '28px', padding: 0, borderRadius: '8px',
                  background: searchEnabled
                    ? `linear-gradient(135deg, ${colors.purpleMid}60 0%, ${colors.purpleMid}80 100%)`
                    : `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                  color: colors.purpleDark, cursor: 'pointer', transition: 'all 0.2s ease',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  fontWeight: 500,
                  border: searchEnabled ? `1px solid ${colors.purpleMid}` : `1px solid ${colors.purpleMid}40`,
                  boxShadow: searchEnabled ? `0 0 8px ${colors.purpleMid}40` : 'none'
                }}>
                <Globe size={14} color={searchEnabled ? colors.purpleDark : colors.purpleMid} strokeWidth={1.5} />
              </span>
              <button
                type="button"
                onClick={() => setImageGenerationEnabled(v => !v)}
                title={label(imageGenerationEnabled ? '已开启：仅下一条消息让Mav画图' : '让Mav画图（仅下一条消息）')}
                aria-pressed={imageGenerationEnabled}
                style={{
                  width: '28px', height: '28px', padding: 0, borderRadius: '8px',
                  background: imageGenerationEnabled
                    ? 'linear-gradient(135deg, rgba(184, 119, 87, 0.28) 0%, rgba(214, 157, 116, 0.38) 100%)'
                    : `linear-gradient(135deg, ${colors.purpleMid}20 0%, rgba(184, 119, 87, 0.10) 100%)`,
                  color: imageGenerationEnabled ? '#936B58' : colors.purpleMid,
                  cursor: 'pointer', transition: 'all 0.2s ease',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  border: imageGenerationEnabled ? '1px solid rgba(147, 107, 88, 0.58)' : `1px solid ${colors.purpleMid}40`,
                  boxShadow: imageGenerationEnabled ? '0 0 9px rgba(147, 107, 88, 0.20)' : 'none'
                }}>
                <Palette size={14} color={imageGenerationEnabled ? '#936B58' : colors.purpleMid} strokeWidth={1.5} />
              </button>
              {/* 回到底部按钮 - 移动端 */}
              {showBackToBottom && (
                <button
                  onClick={scrollToBottom}
                  style={{
                    width: '32px',
                    height: '32px',
                    padding: 0,
                    borderRadius: '50%',
                    background: `linear-gradient(135deg, ${colors.purpleMid} 0%, ${colors.purplePale} 100%)`,
                    border: `1px solid ${colors.purpleMid}50`,
                    cursor: 'pointer',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    boxShadow: `0 2px 8px ${colors.purpleMid}40`,
                    transition: 'all 0.2s ease',
                    marginLeft: 'auto',
                    flexShrink: 0
                  }}
                >
                  <Icon name="arrowDown" size={14} color={colors.bgCard} />
                </button>
              )}
            </div>}
            {/* 输入框 */}
            <div style={{
              display: 'flex',
              gap: '6px',
              width: '100%',
              boxSizing: 'border-box'
            }}>
              <div data-plus-menu="true" style={{ position: 'relative', flexShrink: 0 }}>
                <input type="file" accept="image/*" id="img-input" multiple style={{ display: 'none' }}
                  onChange={async (e) => {
                    const files = Array.from(e.target.files || [])
                    if (!files.length) return
                    await addPendingImages(files)
                    e.target.value = ''
                  }}
                />
                <input
                  type="file"
                  accept=".txt,.md,.pdf,.docx,.doc,.py,.ts,.tsx,.js,.jsx,.json,.yaml,.yml,.toml,.sh,.css,.html,.xml"
                  id="file-input"
                  style={{ display: 'none' }}
                  onChange={async (e) => {
                    const file = e.target.files?.[0]
                    if (!file) return
                    const text = await extractFileText(file)
                    if (text) setPendingFile({ name: file.name, text })
                    e.target.value = ''
                  }}
                />
                <button
                  onClick={() => setPlusMenuOpen(v => !v)}
                  style={{
                    width: '42px', height: '42px', borderRadius: '10px',
                    background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                    border: `1px solid ${colors.purpleMid}40`,
                    cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center', transition: 'all 0.2s ease'
                  }}>
                  <Icon name="plus" size={20} color={colors.purpleDark} />
                </button>
                {plusMenuOpen && !isMobile && (
                  <div style={{
                    position: 'absolute', bottom: '52px', left: 0,
                    background: colors.bgCard, borderRadius: '12px',
                    boxShadow: `0 8px 32px ${colors.shadow}`,
                    border: `1px solid ${colors.border}`,
                    padding: '6px', minWidth: '160px', zIndex: 200
                  }}>
                    <button
                      onClick={() => { document.getElementById('img-input')?.click(); setPlusMenuOpen(false) }}
                      style={{
                        width: '100%', padding: '10px 14px', borderRadius: '8px',
                        border: 'none', background: 'transparent', color: colors.textPrimary,
                        fontSize: '14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px', textAlign: 'left'
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = colors.bgSurface}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <Icon name="image" size={15} color={colors.purpleDark} /> 发送图片
                    </button>
                    <button
                      onClick={() => { document.getElementById('file-input')?.click(); setPlusMenuOpen(false) }}
                      style={{
                        width: '100%', padding: '10px 14px', borderRadius: '8px',
                        border: 'none', background: 'transparent', color: colors.textPrimary,
                        fontSize: '14px', cursor: 'pointer', display: 'flex', alignItems: 'center', gap: '10px', textAlign: 'left'
                      }}
                      onMouseEnter={e => e.currentTarget.style.background = colors.bgSurface}
                      onMouseLeave={e => e.currentTarget.style.background = 'transparent'}
                    >
                      <Icon name="edit" size={15} color={colors.purpleDark} /> 发送文件
                    </button>
                    <div style={{ padding: '8px 14px', color: colors.textTertiary, fontSize: '13px' }}>更多功能</div>
                  </div>
                )}
              </div>
                {plusMenuOpen && isMobile && (
                  <>
                    {/* 移动端遮罩 */}
                    <div
                      onClick={() => setPlusMenuOpen(false)}
                      style={{
                        position: 'fixed',
                        inset: 0,
                        background: 'rgba(0,0,0,0.3)',
                        backdropFilter: 'blur(2px)',
                        zIndex: 80
                      }}
                    />
                    {/* 移动端底部菜单 */}
                    <div style={{
                      position: 'fixed',
                      bottom: 0,
                      left: 0,
                      right: 0,
                      background: colors.bgCard,
                      borderTop: `1px solid ${colors.border}`,
                      borderRadius: '16px 16px 0 0',
                      padding: '20px 16px 32px',
                      display: 'flex',
                      justifyContent: 'space-around',
                      zIndex: 90
                    }}>
                      {[
                        { icon: 'image', label: '发送图片', onClick: () => { document.getElementById('img-input')?.click(); setPlusMenuOpen(false) } },
                        { icon: 'edit', label: '发送文件', onClick: () => { document.getElementById('file-input')?.click(); setPlusMenuOpen(false) } },
                        { icon: 'heart', label: '占位', onClick: () => {} }
                      ].map((item, index) => (
                        <button
                          key={index}
                          onClick={item.onClick}
                          style={{
                            background: 'transparent',
                            border: 'none',
                            cursor: 'pointer',
                            display: 'flex',
                            flexDirection: 'column',
                            alignItems: 'center',
                            gap: '6px',
                            padding: '0',
                            transition: 'all 0.2s ease'
                          }}
                        >
                          <div style={{
                            width: '52px',
                            height: '52px',
                            borderRadius: '12px',
                            background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
                            border: `1px solid ${colors.purpleMid}40`,
                            display: 'flex',
                            alignItems: 'center',
                            justifyContent: 'center'
                          }}>
                            <Icon name={item.icon} size={24} color={colors.purpleDark} />
                          </div>
                          <span style={{
                            fontSize: '12px',
                            color: colors.textSecondary,
                            fontWeight: 500
                          }}>{item.label}</span>
                        </button>
                      ))}
                    </div>
                  </>
                )}
              <div style={{
                flex: 1,
                position: 'relative'
              }}>
                {pendingImages.length > 0 && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '10px', flexWrap: 'wrap',
                    padding: '8px 0', marginBottom: '4px'
                  }}>
                    <div style={{ display: 'flex', gap: '7px', overflowX: 'auto', maxWidth: '100%', padding: '2px' }}>
                      {pendingImages.map((image, index) => (
                        <div key={`${image.preview.slice(-24)}-${index}`} style={{ position: 'relative', flexShrink: 0 }}>
                          <button
                            type="button"
                            aria-label={`放大查看并编辑第${index + 1}张图片`}
                            onClick={() => setEditingPendingImageIndex(index)}
                            style={{
                              height: '56px', width: '56px', padding: 0, borderRadius: '8px',
                              border: `1px solid ${colors.border}`, background: colors.bgSurface,
                              display: 'block', overflow: 'hidden', cursor: 'zoom-in', position: 'relative'
                            }}
                          >
                            <img
                              src={image.preview}
                              alt={`待发送图片 ${index + 1}`}
                              style={{ height: '100%', width: '100%', objectFit: 'cover', display: 'block' }}
                            />
                            <span style={{
                              position: 'absolute', left: '4px', bottom: '4px', width: '19px', height: '19px',
                              borderRadius: '6px', background: 'rgba(24, 22, 30, 0.68)', color: '#fff',
                              display: 'grid', placeItems: 'center', backdropFilter: 'blur(4px)'
                            }}>
                              <Maximize2 size={11} strokeWidth={2} />
                            </span>
                          </button>
                          <button
                            type="button"
                            aria-label={`移除第${index + 1}张图片`}
                            onClick={() => {
                              setPendingImages((current) => current.filter((_, itemIndex) => itemIndex !== index))
                              if (pendingImages.length === 1) setDirectSend(false)
                            }}
                            style={{
                              position: 'absolute', top: '-6px', right: '-6px',
                              width: '18px', height: '18px', borderRadius: '50%',
                              background: colors.textSecondary, border: 'none',
                              color: '#fff', fontSize: '11px', cursor: 'pointer',
                              display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1
                            }}
                          >✕</button>
                        </div>
                      ))}
                    </div>
                    <label style={{
                      display: 'flex', alignItems: 'center', gap: '6px',
                      fontSize: '13px', color: supportsDirectVision ? colors.textPrimary : colors.textTertiary,
                      cursor: supportsDirectVision ? 'pointer' : 'default', userSelect: 'none'
                    }}>
                      <input
                        type="checkbox"
                        checked={directSend}
                        disabled={!supportsDirectVision}
                        onChange={(e) => setDirectSend(e.target.checked)}
                        style={{ cursor: supportsDirectVision ? 'pointer' : 'default' }}
                      />
                      {label('直接给Mav看')}
                    </label>
                  </div>
                )}
                {pendingFile && (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '10px',
                    padding: '8px 0', marginBottom: '4px'
                  }}>
                    <div style={{ position: 'relative', flexShrink: 0 }}>
                      <div style={{
                        height: '56px', width: '56px', borderRadius: '8px',
                        background: `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}40 100%)`,
                        border: `1px solid ${colors.purpleMid}40`,
                        display: 'flex', flexDirection: 'column',
                        alignItems: 'center', justifyContent: 'center', gap: '4px'
                      }}>
                        <Icon name="edit" size={18} color={colors.purpleDark} />
                        <span style={{ fontSize: '9px', color: colors.purpleDark, fontWeight: 600 }}>
                          {pendingFile.name.split('.').pop()?.toUpperCase()}
                        </span>
                      </div>
                      <button
                        onClick={() => setPendingFile(null)}
                        style={{
                          position: 'absolute', top: '-6px', right: '-6px',
                          width: '18px', height: '18px', borderRadius: '50%',
                          background: colors.textSecondary, border: 'none',
                          color: '#fff', fontSize: '11px', cursor: 'pointer',
                          display: 'flex', alignItems: 'center', justifyContent: 'center', lineHeight: 1
                        }}
                      >✕</button>
                    </div>
                    <div style={{ fontSize: '12px', color: colors.textSecondary, flex: 1, overflow: 'hidden' }}>
                      <div style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
                        {pendingFile.name}
                      </div>
                      <div style={{ fontSize: '11px', color: colors.textTertiary, marginTop: '2px' }}>
                        {pendingFile.text.length} 字
                      </div>
                    </div>
                  </div>
                )}
                <textarea
                  value={inputText}
                  onChange={(e) => chatStore.getState().setInputText(e.target.value)}
                  onPaste={(e) => {
                    const items = e.clipboardData?.items
                    if (!items) return
                    for (const item of Array.from(items)) {
                      if (item.type.startsWith('image/')) {
                        e.preventDefault()
                        const file = item.getAsFile()
                        if (file) addPendingImages([file])
                        break
                      }
                    }
                  }}
                  onFocus={() => setInputFocused(true)}
                  onBlur={() => setTimeout(() => setInputFocused(false), 100)}
                  onKeyDown={(e) => {
                    if (e.key === 'Enter' && !e.shiftKey && !isMobile) {
                      e.preventDefault()
                      handleSend()
                    }
                  }}
                  style={{
                    width: '100%',
                    minHeight: '42px',
                    maxHeight: '168px',
                    fieldSizing: 'content',
                    overflowY: 'auto',
                    padding: '11px 14px',
                    borderRadius: '10px',
                    border: `1px solid ${colors.border}`,
                    background: colors.bgPrimary,
                    color: colors.textPrimary,
                    fontSize: '15px',
                    fontFamily: '-apple-system, BlinkMacSystemFont, "Segoe UI", "PingFang SC", "Microsoft YaHei", sans-serif',
                    outline: 'none',
                    fontWeight: 400,
                    lineHeight: 1.68,
                    textAlign: 'left',
                    boxSizing: 'border-box',
                    resize: 'none',
                    display: 'block'
                  }}
                  placeholder={!settings.apiKey ? "请先在设置中配置 API Key" : "输入消息..."}
                  disabled={!settings.apiKey || isStreaming}
                />
              </div>
              <button
                onClick={handleSend}
                disabled={(!hasSendableContent && !isStreaming) || !settings.apiKey}
                style={{
                  width: '42px',
                  height: '42px',
                  borderRadius: '10px',
                  background: isStreaming
                    ? 'transparent'
                    : `linear-gradient(135deg, ${colors.primary} 0%, ${colors.primaryHover} 100%)`,
                  border: isStreaming
                    ? `1px solid ${colors.purpleMid}40`
                    : `1px solid ${colors.primary}80`,
                  cursor: ((!hasSendableContent && !isStreaming) || !settings.apiKey) ? 'not-allowed' : 'pointer',
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  transition: 'all 0.2s ease',
                  boxShadow: isStreaming ? 'none' : `0 2px 8px ${colors.purpleMid}40`,
                  flexShrink: 0,
                  opacity: ((!hasSendableContent && !isStreaming) || !settings.apiKey) ? 0.5 : 1
                }}>
                {isStreaming
                  ? <Square size={16} color={colors.purpleDark} strokeWidth={2} fill={colors.purpleDark} />
                  : <Send size={17} color={colors.bgCard} strokeWidth={1.5} />
                }
              </button>
            </div>
          </div>
        </div>
        )}
      </main>

      {/* 移动端底部导航 */}
      {isMobile && !(activeTab === 'dialog' && (inputFocused || focusMode))
        && !(activeTab === 'reading' && readingView === 'reader' && readerFocusMode) && (
        <nav style={{
          position: 'fixed',
          bottom: 0,
          left: 0,
          right: 0,
          height: 'calc(56px + env(safe-area-inset-bottom, 8px))',
          background: colors.bgCard,
          borderTop: `1px solid ${colors.border}`,
          display: 'flex',
          justifyContent: 'space-around',
          alignItems: 'flex-start',
          paddingTop: '8px',
          paddingBottom: 'env(safe-area-inset-bottom, 8px)',
          zIndex: 60,
        }}>
          {[
            { Component: BotMessageSquare, label: '对话', value: 'dialog' },
            { Component: BookHeart, label: '阅读', value: 'reading' },
            { Component: Sticker, label: '笔记', value: 'note' },
            { Component: HeartPulse, label: '我们', value: 'us' }
          ].map((item, index) => {
            const disabled = false
            return (
            <button
              key={index}
              onClick={() => {
                if (disabled) return
                setActiveTab(item.value as any)
              }}
              style={{
                flex: 1,
                height: '100%',
                background: 'transparent',
                border: 'none',
                cursor: disabled ? 'not-allowed' : 'pointer',
                opacity: activeTab === item.value ? 1 : (disabled ? 0.35 : 0.58),
                display: 'flex',
                flexDirection: 'column',
                alignItems: 'center',
                justifyContent: 'center',
                gap: '5px',
                transition: 'all 0.2s ease',
                paddingTop: '2px'
              }}
            >
              <item.Component
                size={32}
                strokeWidth={activeTab === item.value ? 1.8 : 1.5}
                color={activeTab === item.value ? colors.primary : colors.purplePale}
              />
              <span style={{
                fontSize: '12px',
                color: activeTab === item.value ? colors.primary : colors.purplePale,
                fontWeight: 500,
                letterSpacing: '-0.01em'
              }}>{item.label}</span>
            </button>
            )
          })}
        </nav>
      )}

      <style>{`
        *, *::before, *::after {
          box-sizing: border-box;
        }
        html, body {
          margin: 0;
          padding: 0;
          overflow-x: hidden;
          width: 100%;
          height: 100%;
        }
        * {
          max-width: 100%;
        }
        .chat-messages-scroll {
          scrollbar-width: thin;
          scrollbar-color: rgba(113, 110, 133, 0.28) transparent;
        }
        .chat-messages-scroll::-webkit-scrollbar {
          width: 4px;
          height: 0;
        }
        .chat-messages-scroll::-webkit-scrollbar-track {
          background: transparent;
        }
        .chat-messages-scroll::-webkit-scrollbar-thumb {
          background-color: rgba(113, 110, 133, 0.28);
          border-radius: 999px;
        }
        .chat-messages-scroll::-webkit-scrollbar-button {
          display: none;
          width: 0;
          height: 0;
        }
        @keyframes heartbeat {
          0%, 100% { transform: scale(1); }
          50% { transform: scale(1.15); }
        }
        .assistant-heart-emoji {
          display: inline-flex;
          align-items: center;
          justify-content: center;
          width: 24px;
          height: 22px;
          margin-top: 1px;
          flex: 0 0 24px;
          font-size: 18px;
          line-height: 1;
        }
        .assistant-heart-emoji--active {
          animation: heartbeat 1.15s ease-in-out infinite;
        }
        @media (prefers-reduced-motion: reduce) {
          .assistant-heart-emoji--active {
            animation: none;
          }
        }
        @keyframes fadeIn {
          from { opacity: 0; }
          to { opacity: 1; }
        }
        @keyframes spin {
          from { transform: rotate(0deg); }
          to { transform: rotate(360deg); }
        }
        @keyframes studioBreath {
          0%, 100% { transform: rotate(-2deg) scale(1); opacity: 0.72; }
          50% { transform: rotate(2deg) scale(1.08); opacity: 1; }
        }
        .studio-ellipsis {
          display: inline-block;
          width: 1.25em;
          overflow: hidden;
          white-space: nowrap;
          vertical-align: baseline;
          animation: studioDots 1.25s steps(4, end) infinite;
        }
        @keyframes studioDots {
          from { clip-path: inset(0 100% 0 0); }
          to { clip-path: inset(0 0 0 0); }
        }
        @keyframes slideUp {
          from { opacity: 0; transform: translateY(8px); }
          to { opacity: 1; transform: translateY(0); }
        }
        @keyframes slideInRight {
          from { transform: translateX(100%); }
          to { transform: translateX(0); }
        }
        @keyframes slideInLeft {
          from { transform: translateX(0); }
          to { transform: translateX(-100%); }
        }
        /* 只应用于特定的可滚动容器 */
        div[style*="overflow: auto"]::-webkit-scrollbar,
        div[style*="overflow-y: auto"]::-webkit-scrollbar,
        div[style*="overflow-y: scroll"]::-webkit-scrollbar {
          width: 4px;
        }
        div[style*="overflow: auto"]::-webkit-scrollbar-track,
        div[style*="overflow-y: auto"]::-webkit-scrollbar-track,
        div[style*="overflow-y: scroll"]::-webkit-scrollbar-track {
          background: ${colors.bgSurface};
        }
        div[style*="overflow: auto"]::-webkit-scrollbar-thumb,
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb,
        div[style*="overflow-y: scroll"]::-webkit-scrollbar-thumb {
          background: ${colors.purpleMid};
          border-radius: 2px;
        }
        div[style*="overflow: auto"]::-webkit-scrollbar-thumb:hover,
        div[style*="overflow-y: auto"]::-webkit-scrollbar-thumb:hover,
        div[style*="overflow-y: scroll"]::-webkit-scrollbar-thumb:hover {
          background: ${colors.purplePale};
        }
        input::placeholder {
          color: ${colors.textTertiary};
        }
        button:active {
          transform: scale(0.96);
        }
      `}</style>
    <SettingsPanel isOpen={settingsOpen} onClose={() => setSettingsOpen(false)} />

    {editingPendingImageIndex !== null && pendingImages[editingPendingImageIndex] && (
      <PendingImageEditor
        key={`${editingPendingImageIndex}-${pendingImages[editingPendingImageIndex].preview.slice(-24)}`}
        image={pendingImages[editingPendingImageIndex]}
        imageNumber={editingPendingImageIndex + 1}
        theme={theme}
        onClose={() => setEditingPendingImageIndex(null)}
        onApply={(editedImage) => {
          setPendingImages((current) => current.map((item, index) =>
            index === editingPendingImageIndex ? editedImage : item
          ))
          setEditingPendingImageIndex(null)
        }}
      />
    )}

    {/* 对话地图 */}
    {mapOpen && (
      <>
        {/* 遮罩：移动端全屏，桌面端无遮罩 */}
        {isMobile && (
          <div
            onClick={() => setMapOpen(false)}
            style={{
              position: 'fixed', inset: 0, zIndex: 200,
              background: 'rgba(0,0,0,0.3)',
              animation: 'fadeIn 0.2s ease'
            }}
          />
        )}

        {/* 面板：移动端底部弹层，桌面端右侧抽屉 */}
        <div style={isMobile ? {
          position: 'fixed',
          bottom: 0, left: 0, right: 0,
          height: '55vh',
          background: colors.bgCard,
          borderRadius: '20px 20px 0 0',
          zIndex: 201,
          display: 'flex',
          flexDirection: 'column',
          animation: 'slideUp 0.25s ease',
          boxShadow: `0 -8px 32px ${colors.shadow}`
        } : {
          position: 'fixed',
          top: 0, right: 0, bottom: 0,
          width: '280px',
          marginLeft: '300px',
          background: colors.bgCard,
          borderLeft: `1px solid ${colors.border}`,
          zIndex: 100,
          display: 'flex',
          flexDirection: 'column',
          animation: 'slideInRight 0.25s ease',
          boxShadow: `-4px 0 24px ${colors.shadow}`
        }}>
          {/* 拖动条（仅移动端） */}
          {isMobile && (
            <div style={{ display: 'flex', justifyContent: 'center', padding: '10px 0 4px' }}>
              <div style={{ width: '36px', height: '4px', borderRadius: '2px', background: colors.border }} />
            </div>
          )}

          {/* 顶部栏 */}
          <div style={{
            display: 'flex', alignItems: 'center', gap: '10px',
            padding: isMobile ? '8px 16px 10px' : '16px 16px 12px',
            borderBottom: `1px solid ${colors.border}`,
            flexShrink: 0
          }}>
            {mapSearchActive ? (
              <>
                <input
                  autoFocus
                  value={mapSearch}
                  onChange={e => setMapSearch(e.target.value)}
                  placeholder="搜索消息..."
                  style={{
                    flex: 1, background: colors.bgSurface,
                    border: `1px solid ${colors.border}`,
                    borderRadius: '8px', padding: '6px 12px',
                    color: colors.textPrimary, fontSize: '14px',
                    outline: 'none', fontFamily: 'inherit'
                  }}
                />
                <button onClick={() => { setMapSearchActive(false); setMapSearch('') }}
                  style={{ background: 'none', border: 'none', color: colors.textSecondary, cursor: 'pointer', fontSize: '14px', padding: '4px 6px' }}>
                  取消
                </button>
              </>
            ) : (
              <>
                <MapPlus size={16} color={colors.purpleDark} strokeWidth={1.5} />
                <span style={{ flex: 1, fontSize: '15px', fontWeight: 600, color: colors.textPrimary }}>对话地图</span>
                <button onClick={() => setMapSearchActive(true)}
                  style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', color: colors.textSecondary }}>
                  <Search size={18} strokeWidth={1.5} />
                </button>
                {!isMobile && (
                  <button onClick={() => setMapOpen(false)}
                    style={{ background: 'none', border: 'none', cursor: 'pointer', padding: '4px', color: colors.textSecondary }}>
                    ✕
                  </button>
                )}
              </>
            )}
          </div>

          {/* 消息列表 */}
          <div
            ref={mapListRef}
            style={{ flex: 1, overflowY: 'auto', padding: '8px 12px' }}
          >
            {mapLoading && (
              <div style={{ padding: '28px 12px', textAlign: 'center', color: colors.textTertiary, fontSize: '13px' }}>
                正在展开整张地图…
              </div>
            )}
            {mapError && (
              <div style={{ padding: '12px', color: colors.textSecondary, fontSize: '13px', lineHeight: 1.6 }}>
                {mapError}
              </div>
            )}
            {!mapLoading && !mapError && mapMessages.length === 0 && (
              <div style={{ padding: '28px 12px', textAlign: 'center', color: colors.textTertiary, fontSize: '13px' }}>
                这间房还没有留下对话
              </div>
            )}
            {mapMessages
              .filter(msg => {
                if (!mapSearch) return true
                const text = (msg.content || '') + (msg.thinking || '')
                return text.toLowerCase().includes(mapSearch.toLowerCase())
              })
              .map((msg, idx) => {
                const preview = (msg.thinking ? `[思考] ${msg.thinking} ` : '') + (msg.content || '')
                const trimmed = preview.replace(/\n+/g, ' ').trim().slice(0, 60)
                const isUser = msg.role === 'user'
                return (
                  <div
                    key={msg.id || idx}
                    onClick={() => void jumpToMapMessage(msg.id)}
                    style={{
                      display: 'flex',
                      justifyContent: isUser ? 'flex-end' : 'flex-start',
                      marginBottom: '6px',
                      opacity: mapJumpingMessageId && mapJumpingMessageId !== msg.id ? 0.48 : 1,
                      transition: 'opacity 0.15s ease',
                      contentVisibility: 'auto',
                      containIntrinsicSize: '44px'
                    }}
                  >
                    <div style={{
                      maxWidth: '85%',
                      padding: '7px 12px',
                      borderRadius: '12px',
                      fontSize: '13px',
                      lineHeight: 1.5,
                      cursor: 'pointer',
                      background: isUser
                        ? `linear-gradient(135deg, ${colors.purpleMid}25 0%, ${colors.purpleMid}35 100%)`
                        : colors.bgSurface,
                      color: colors.textSecondary,
                      border: `1px solid ${colors.border}`,
                      transition: 'all 0.15s ease'
                    }}
                    onMouseEnter={e => e.currentTarget.style.borderColor = colors.purpleMid}
                    onMouseLeave={e => e.currentTarget.style.borderColor = colors.border}
                    >
                      {mapJumpingMessageId === msg.id ? '正在翻到这里…' : <>{trimmed || '…'}{preview.length > 60 ? '…' : ''}</>}
                    </div>
                  </div>
                )
              })}
          </div>
        </div>
      </>
    )}

    </div>
    </LoginGate>
  )
}
