import { useState, useEffect, useLayoutEffect, useRef } from 'react'
import { Maximize2, Minimize2, Eye, EyeOff, List } from 'lucide-react'
import { useIdentityLabel } from '../stores/identityStore'
import {
  listChapters,
  getChapter,
  updateProgress,
  addReply,
  addAnnotation
} from '../services/readingApi'

interface BookReaderProps {
  book: any
  onBack: () => void
  colors: Record<string, string>
  isMobile: boolean
  focusMode: boolean
  onToggleFocus: () => void
}

type Chapter = {
  chapter_index: number
  title: string
}

type Annotation = {
  id: number
  start_char: number
  end_char: number
  quote_text: string
  author: string
  content: string
  root_id: number | null
  parent_id: number | null
  created_at?: string
}

type ChapterData = {
  chapter_index: number
  title: string
  content_text: string
  content_images: any[]
  annotations: Annotation[]
}

const TURN_DISTANCE = 96
const EDGE_TOLERANCE = 3

export const BookReader: React.FC<BookReaderProps> = ({
  book,
  onBack,
  colors,
  isMobile,
  focusMode,
  onToggleFocus
}) => {
  const label = useIdentityLabel()
  const [chapters, setChapters] = useState<Chapter[]>([])
  const [currentChapter, setCurrentChapter] = useState(book.last_chapter || 0)
  const [chapterData, setChapterData] = useState<ChapterData | null>(null)
  const [showAnnotations, setShowAnnotations] = useState(true)
  const [loading, setLoading] = useState(true)
  const [tocOpen, setTocOpen] = useState(false)
  const [annotationPanel, setAnnotationPanel] = useState<{
    id: number
    quoteText: string
    thread: Annotation[]
  } | null>(null)
  const [replyInput, setReplyInput] = useState('')
  const [isReplying, setIsReplying] = useState(false)
  const [streamingText, setStreamingText] = useState('')
  const replyAbortRef = useRef<AbortController | null>(null)
  const scrollRef = useRef<HTMLDivElement>(null)
  const shouldScrollToBottomRef = useRef(false)
  const chapterRequestRef = useRef(0)
  const edgePullRef = useRef<{ x: number; y: number; top: boolean; bottom: boolean } | null>(null)
  const pageRef = useRef<HTMLDivElement>(null)
  const pullOffsetRef = useRef(0)
  const reboundRef = useRef<Animation | null>(null)
  const [showPreviousChapterHint, setShowPreviousChapterHint] = useState(false)
  const threadRef = useRef<HTMLDivElement>(null)
  // 文字选择添加批注
  const [selectionBubble, setSelectionBubble] = useState<{
    x: number; y: number; text: string; startChar: number; endChar: number
  } | null>(null)
  const [selectionInput, setSelectionInput] = useState('')
  const [isCreatingAnnotation, setIsCreatingAnnotation] = useState(false)
  const [createStreamingText, setCreateStreamingText] = useState('')
  const [createDoneText, setCreateDoneText] = useState('')
  const [showAnnotationInput, setShowAnnotationInput] = useState(false)
  const createAbortRef = useRef<AbortController | null>(null)
  const createThreadRef = useRef<HTMLDivElement>(null)
  const readerBottom = isMobile
    ? focusMode ? 'env(safe-area-inset-bottom, 0px)' : 'calc(56px + env(safe-area-inset-bottom, 8px))'
    : '0px'
  const [keyboardInset, setKeyboardInset] = useState(0)
  const panelOpen = Boolean(annotationPanel) || showAnnotationInput
  // In focus mode the footer already pads its controls above the safe area.
  const panelBottom = isMobile ? `max(${focusMode ? '0px' : readerBottom}, ${keyboardInset}px)` : '40px'

  useEffect(() => {
    const viewport = window.visualViewport
    if (!isMobile || !panelOpen || !viewport) return
    const update = () => setKeyboardInset(Math.max(0, window.innerHeight - viewport.height - viewport.offsetTop))
    update()
    viewport.addEventListener('resize', update)
    viewport.addEventListener('scroll', update)
    return () => {
      viewport.removeEventListener('resize', update)
      viewport.removeEventListener('scroll', update)
      setKeyboardInset(0)
    }
  }, [isMobile, panelOpen])

  const loadChapters = async () => {
    try {
      const result = await listChapters(book.id)
      setChapters(result.chapters)
    } catch (error) {
      console.error('加载章节目录失败:', error)
    }
  }

  const loadChapter = async (index: number) => {
    const request = ++chapterRequestRef.current
    try {
      setLoading(true)
      const data = await getChapter(book.id, index)
      if (request !== chapterRequestRef.current) return
      // 展开每个根批注的replies为扁平Annotation条目
      const flatAnnotations: Annotation[] = []
      for (const ann of data.annotations) {
        flatAnnotations.push(ann)
        if (ann.replies && Array.isArray(ann.replies)) {
          for (const reply of ann.replies) {
            flatAnnotations.push(reply)
          }
        }
      }
      setChapterData({ ...data, annotations: flatAnnotations })
      updateProgress(book.id, index, 0)
    } catch (error) {
      if (request !== chapterRequestRef.current) return
      console.error('加载章节内容失败:', error)
      setChapterData(null)
      if (index !== 0) {
        setCurrentChapter(0)
      }
    } finally {
      if (request === chapterRequestRef.current) setLoading(false)
    }
  }

  const goToChapter = (index: number) => {
    if (index < 0 || index >= chapters.length) return
    setCurrentChapter(index)
    shouldScrollToBottomRef.current = false
    setTocOpen(false)
  }

  const closeAnnotationPanel = () => {
    // 如果有正在进行的SSE流，取消它
    if (replyAbortRef.current) {
      replyAbortRef.current.abort()
      replyAbortRef.current = null
    }
    setAnnotationPanel(null)
    setReplyInput('')
    setIsReplying(false)
    setStreamingText('')
  }

  // 文字选择监听：选中文字后弹出"添加批注"气泡
  useEffect(() => {
    const handleSelectionEnd = () => {
      // 先关掉旧的，避免气泡残留
      // 在创建中不处理新的选择
      if (isCreatingAnnotation) return

      const sel = window.getSelection()
      if (!sel || sel.isCollapsed || !sel.toString().trim()) {
        // 无有效选中文字
        return
      }
      const text = sel.toString().trim()
      if (text.length < 2) return

      // 确认选区在正文区内
      const anchor = sel.anchorNode
      if (!anchor || !scrollRef.current?.contains(anchor)) return

      // 获取选中文字在全文中的 start_char/end_char
      const fullText = chapterData?.content_text
      if (!fullText) return

      // 用 getRangeAt 获取选中范围，拿到准确的选区文字
      const range = sel.getRangeAt(0)
      const selText = range.toString()
      const idx = fullText.indexOf(selText)
      if (idx === -1) return

      const startChar = idx
      const endChar = idx + selText.length

      // 计算气泡位置
      const rect = range.getBoundingClientRect()
      const containerRect = scrollRef.current!.getBoundingClientRect()
      const x = rect.left + rect.width / 2 - containerRect.left
      const y = rect.top - containerRect.top - 8

      setSelectionBubble({ x, y, text: selText, startChar, endChar })
    }

    const handleClickOutside = (e: MouseEvent) => {
      if (selectionBubble && scrollRef.current && !scrollRef.current.contains(e.target as Node)) {
        // 不在这里处理——正文外的点击由其他逻辑处理
      }
    }

    const ref = scrollRef.current
    if (ref) {
      ref.addEventListener('mouseup', handleSelectionEnd)
      ref.addEventListener('touchend', handleSelectionEnd)
      document.addEventListener('mousedown', handleClickOutside)
    }
    return () => {
      if (ref) {
        ref.removeEventListener('mouseup', handleSelectionEnd)
        ref.removeEventListener('touchend', handleSelectionEnd)
      }
      document.removeEventListener('mousedown', handleClickOutside)
    }
  }, [chapterData, isCreatingAnnotation, selectionBubble])

  // 点击正文区域关闭 selectionBubble（但不影响文字选择）
  useEffect(() => {
    if (!selectionBubble) return
    const handlePointer = (e: MouseEvent | TouchEvent) => {
      // 允许触摸事件先触发 selectionend
      if (e.type === 'touchstart') return
      const target = e.target as HTMLElement
      // 如果点击在正文区且不是气泡内的元素，关闭气泡
      if (scrollRef.current?.contains(target)) {
        // 检查是否点在气泡按钮上——如果是，不关闭
        if (target.closest('[data-selection-bubble]')) return
        // 检查是否还有活跃的选区（用户可能刚选中还没松手）
        const sel = window.getSelection()
        if (sel && !sel.isCollapsed && sel.toString().trim() === selectionBubble.text) return
        setSelectionBubble(null)
      }
    }
    document.addEventListener('mousedown', handlePointer)
    document.addEventListener('touchstart', handlePointer)
    return () => {
      document.removeEventListener('mousedown', handlePointer)
      document.removeEventListener('touchstart', handlePointer)
    }
  }, [selectionBubble])

  const handleSendReply = async () => {
    const content = replyInput.trim()
    if (!content || !annotationPanel || isReplying) return

    setIsReplying(true)
    setStreamingText('')
    setReplyInput('')

    try {
      const controller = new AbortController()
      replyAbortRef.current = controller

      // 先追加用户回复到线程（本地显示）
      const now = new Date().toISOString()
      const userReply: Annotation = {
        id: -(Date.now()),
        start_char: 0,
        end_char: 0,
        quote_text: '',
        author: 'user',
        content,
        root_id: annotationPanel.id,
        parent_id: annotationPanel.id,
        created_at: now
      }
      setAnnotationPanel(prev => {
        if (!prev) return prev
        return { ...prev, thread: [...prev.thread, userReply] }
      })
      setChapterData(prev => prev ? { ...prev, annotations: [...prev.annotations, userReply] } : prev)

      const res = await addReply(annotationPanel.id, content)

      if (!res.ok) {
        console.error('回复失败:', res.status)
        // 保留用户消息，但标记发送失败
        return
      }

      // SSE 流式读取
      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let accumulated = ''
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || '' // 最后一个不完整的行保留

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'text' && data.text) {
                accumulated += data.text
                setStreamingText(accumulated)
              } else if (data.type === 'done') {
                // 完成，将Mav回复追加到线程
                setStreamingText('')
                if (accumulated.trim()) {
                  const mavReply: Annotation = {
                    id: data.reply_id || data.annotation_id || -(Date.now() + 1),
                    start_char: 0,
                    end_char: 0,
                    quote_text: '',
                    author: 'mav',
                    content: accumulated,
                    root_id: annotationPanel.id,
                    parent_id: annotationPanel.id,
                    created_at: new Date().toISOString()
                  }
                  setAnnotationPanel(prev => {
                    if (!prev) return prev
                    return { ...prev, thread: [...prev.thread, mavReply] }
                  })
                  setChapterData(prev => prev ? { ...prev, annotations: [...prev.annotations, mavReply] } : prev)
                }
                accumulated = ''
              }
            } catch {
              // 非JSON行，忽略
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        // 被取消，忽略
      } else {
        console.error('SSE读取失败:', error)
      }
    } finally {
      setIsReplying(false)
      replyAbortRef.current = null
    }
  }

  const handleCancelReply = () => {
    if (replyAbortRef.current) {
      replyAbortRef.current.abort()
      replyAbortRef.current = null
    }
    setIsReplying(false)
    setStreamingText('')
  }

  const handleSendAnnotation = async () => {
    const selBubble = selectionBubble
    const content = selectionInput.trim()
    if (!content || !selBubble || isCreatingAnnotation || !chapterData) return

    setIsCreatingAnnotation(true)
    setCreateStreamingText('')
    setCreateDoneText('')
    setSelectionInput('')
    // 不清空 selectionBubble，保留面板直到用户手动关闭
    // 清除文字选择
    window.getSelection()?.removeAllRanges()

    try {
      const controller = new AbortController()
      createAbortRef.current = controller

      const res = await addAnnotation(book.id, {
        chapter_index: currentChapter,
        start_char: selBubble.startChar,
        end_char: selBubble.endChar,
        quote_text: selBubble.text,
        content
      })

      if (!res.ok) {
        console.error('创建批注失败:', res.status)
        return
      }

      // SSE 流式读取
      const reader = res.body!.getReader()
      const decoder = new TextDecoder()
      let accumulated = ''
      let buffer = ''

      while (true) {
        const { done, value } = await reader.read()
        if (done) break

        buffer += decoder.decode(value, { stream: true })
        const lines = buffer.split('\n')
        buffer = lines.pop() || ''

        for (const line of lines) {
          if (line.startsWith('data: ')) {
            try {
              const data = JSON.parse(line.slice(6))
              if (data.type === 'text' && data.text) {
                accumulated += data.text
                setCreateStreamingText(accumulated)
              } else if (data.type === 'done') {
                setCreateStreamingText('')
                // 后端在 done 中返回 annotation_id 和 reply_id
                const annId = data.annotation_id || -(Date.now())
                const mavReplyId = data.reply_id || -(Date.now() + 1)
                const now = new Date().toISOString()

                // 用户根批注
                const userAnnotation: Annotation = {
                  id: annId,
                  start_char: selBubble.startChar,
                  end_char: selBubble.endChar,
                  quote_text: selBubble.text,
                  author: 'user',
                  content,
                  root_id: data.annotation_id ? annId : null,
                  parent_id: null,
                  created_at: now
                }

                // Mav 第一条回复
                const mavReply: Annotation = {
                  id: mavReplyId,
                  start_char: 0,
                  end_char: 0,
                  quote_text: '',
                  author: 'mav',
                  content: accumulated,
                  root_id: annId,
                  parent_id: annId,
                  created_at: now
                }

                // 添加到 chapterData.annotations
                const newAnnotations: Annotation[] = accumulated.trim()
                  ? [userAnnotation, mavReply]
                  : [userAnnotation]
                setChapterData(prev => prev ? { ...prev, annotations: [...prev.annotations, ...newAnnotations] } : prev)
                // 保留 Mav 回复在面板里显示
                if (accumulated.trim()) {
                  setCreateDoneText(accumulated)
                }
                accumulated = ''
              }
            } catch {
              // 非JSON行，忽略
            }
          }
        }
      }
    } catch (error) {
      if (error instanceof DOMException && error.name === 'AbortError') {
        // 被取消，忽略
      } else {
        console.error('SSE读取失败:', error)
      }
    } finally {
      setIsCreatingAnnotation(false)
      createAbortRef.current = null
    }
  }

  const closeAnnotationInput = () => {
    if (createAbortRef.current) {
      createAbortRef.current.abort()
      createAbortRef.current = null
    }
    setShowAnnotationInput(false)
    setSelectionInput('')
    setSelectionBubble(null)
    setIsCreatingAnnotation(false)
    setCreateStreamingText('')
    setCreateDoneText('')
  }

  const releasePull = () => {
    edgePullRef.current = null
    setShowPreviousChapterHint(false)
    const offset = pullOffsetRef.current
    pullOffsetRef.current = 0
    const page = pageRef.current
    if (!page || !offset) return
    page.style.transform = ''
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    reboundRef.current = page.animate([
      { transform: `translateY(${offset}px)`, easing: 'cubic-bezier(0.2, 0.8, 0.3, 1)' },
      { transform: `translateY(${offset * -0.16}px)`, offset: 0.72 },
      { transform: 'translateY(0)' },
    ], { duration: 290 })
  }

  const canPull = () => !loading && Boolean(chapterData) && !panelOpen && !tocOpen
    && !selectionBubble && (window.getSelection()?.isCollapsed ?? true)

  const startEdgePull = (event: React.TouchEvent<HTMLDivElement>) => {
    if (event.touches.length !== 1 || !canPull()
      || (event.target instanceof Element && event.target.closest('button, a, input, textarea'))) {
      releasePull()
      return
    }
    const el = event.currentTarget
    const top = currentChapter > 0 && el.scrollTop <= EDGE_TOLERANCE
    const bottom = currentChapter < chapters.length - 1
      && el.scrollHeight - el.clientHeight - el.scrollTop <= EDGE_TOLERANCE
    const touch = event.touches[0]
    edgePullRef.current = top || bottom ? { x: touch.clientX, y: touch.clientY, top, bottom } : null
  }

  const edgePullDistance = (touch: { clientX: number; clientY: number }, el: HTMLDivElement) => {
    const start = edgePullRef.current
    if (!start || !canPull()) return 0
    const dy = touch.clientY - start.y
    if (Math.abs(dy) <= Math.abs(touch.clientX - start.x) * 1.3) return 0
    if (dy > 0 && start.top && el.scrollTop <= EDGE_TOLERANCE) return dy
    if (dy < 0 && start.bottom && el.scrollHeight - el.clientHeight - el.scrollTop <= EDGE_TOLERANCE) return dy
    return 0
  }

  const moveEdgePull = (event: React.TouchEvent<HTMLDivElement>) => {
    if (!edgePullRef.current) return
    if (event.touches.length !== 1) {
      releasePull()
      return
    }
    const dy = edgePullDistance(event.touches[0], event.currentTarget)
    setShowPreviousChapterHint(dy >= TURN_DISTANCE)
    const page = pageRef.current
    if (!page || window.matchMedia('(prefers-reduced-motion: reduce)').matches) return
    reboundRef.current?.cancel()
    // As the page stretches, each additional pixel of drag moves it a little less.
    const offset = (88 * dy) / (110 + Math.abs(dy))
    pullOffsetRef.current = offset
    page.style.transform = offset ? `translateY(${offset}px)` : ''
  }

  const endEdgePull = (event: React.TouchEvent<HTMLDivElement>) => {
    const touch = event.changedTouches[0]
    const dy = touch ? edgePullDistance(touch, event.currentTarget) : 0
    releasePull()
    if (Math.abs(dy) < TURN_DISTANCE) return
    setLoading(true)
    shouldScrollToBottomRef.current = dy > 0
    setCurrentChapter(currentChapter + (dy > 0 ? -1 : 1))
  }

  useEffect(() => { loadChapters() }, [book.id])

  useEffect(() => {
    if (chapters.length > 0) {
      loadChapter(currentChapter)
    }
  }, [currentChapter, chapters.length])

  useLayoutEffect(() => {
    const el = scrollRef.current
    if (loading || !chapterData || !el) return
    el.scrollTop = shouldScrollToBottomRef.current ? el.scrollHeight : 0
  }, [loading, chapterData?.chapter_index])

  // 批注面板线程自动滚动到底部
  useEffect(() => {
    if (threadRef.current && annotationPanel) {
      threadRef.current.scrollTop = threadRef.current.scrollHeight
    }
  }, [annotationPanel, streamingText])

  // 创建批注面板流式文本滚动
  useEffect(() => {
    if (createThreadRef.current && showAnnotationInput && (createStreamingText || createDoneText)) {
      createThreadRef.current.scrollTop = createThreadRef.current.scrollHeight
    }
  }, [showAnnotationInput, createStreamingText, createDoneText])

  // 分段函数：用quote_text搜索定位，不依赖start_char/end_char偏移量
  const partitionTextWithAnnotations = (text: string, annotations: Annotation[]) => {
    // 只取根批注，用quote_text在文本里搜索实际位置
    const located: Array<{ start: number, end: number, id: number }> = []

    annotations
      .filter(a => a.parent_id === null && a.quote_text && a.quote_text.trim())
      .forEach(ann => {
        const idx = text.indexOf(ann.quote_text)
        if (idx === -1) return  // 找不到就跳过
        located.push({ start: idx, end: idx + ann.quote_text.length, id: ann.id })
      })

    // 按位置排序，去除重叠
    located.sort((a, b) => a.start - b.start)
    const valid: typeof located = []
    let lastEnd = -1
    for (const loc of located) {
      if (loc.start >= lastEnd) {
        valid.push(loc)
        lastEnd = loc.end
      }
    }

    // 切分文本
    const segments: Array<{ text: string, isAnnotation: boolean, annotationId?: number, author?: string }> = []
    let pos = 0
    for (const loc of valid) {
      if (loc.start > pos) {
        segments.push({ text: text.slice(pos, loc.start), isAnnotation: false })
      }
      const ann = annotations.find(a => a.id === loc.id)
      segments.push({
        text: text.slice(loc.start, loc.end),
        isAnnotation: true,
        annotationId: loc.id,
        author: ann?.author
      })
      pos = loc.end
    }
    if (pos < text.length) {
      segments.push({ text: text.slice(pos), isAnnotation: false })
    }

    return segments
  }

  // Render inline so showing the pull hint does not replace the active touch target.
  const renderHighlightedText = (text: string, annotations: Annotation[], show: boolean) => {
    const segments = partitionTextWithAnnotations(text, annotations)

    return (
      <div
        style={{
          fontSize: '16px',
          lineHeight: 1.9,
          color: colors.textPrimary,
          letterSpacing: '-0.005em',
          whiteSpace: 'pre-wrap',
          WebkitUserSelect: 'text',
          userSelect: 'text'
        }}
      >
        {segments.map((seg, idx) =>
          seg.isAnnotation ? (
            <span
              key={idx}
              onClick={() => {
                if (!show) return
                const annotation = annotations.find(a => a.id === seg.annotationId)
                if (annotation) {
                  const thread = annotations
                    .filter(a => a.id === annotation.id || a.root_id === annotation.id)
                    .sort((a, b) => {
                    if (a.created_at && b.created_at) return a.created_at.localeCompare(b.created_at)
                    return a.id - b.id
                  })
                  setAnnotationPanel({
                    id: annotation.id,
                    quoteText: annotation.quote_text,
                    thread
                  })
                }
              }}
              style={{
                background: show
                  ? (seg.author === 'mav' ? `${colors.primary}35` : `${colors.purpleMid}50`)
                  : 'transparent',
                borderBottom: show
                  ? (seg.author === 'mav'
                    ? `1px dashed ${colors.primary}60`
                    : `1px dashed ${colors.purpleMid}60`)
                  : 'none',
                borderRadius: '3px',
                padding: show ? '0 2px' : '0',
                cursor: show ? 'pointer' : 'default',
                whiteSpace: 'pre-wrap',
                transition: 'background 0.2s ease'
              }}
              onMouseEnter={(e) => {
                if (show) e.currentTarget.style.background = seg.author === 'mav'
                  ? `${colors.primary}55`
                  : `${colors.purpleMid}70`
              }}
              onMouseLeave={(e) => {
                if (show) e.currentTarget.style.background = seg.author === 'mav'
                  ? `${colors.primary}35`
                  : `${colors.purpleMid}50`
              }}
            >
              {seg.text}
            </span>
          ) : (
            <span key={idx}>{seg.text}</span>
          )
        )}
      </div>
    )
  }

  // 批注面板组件
  const AnnotationPanel = () => {
    if (!annotationPanel) return null

    const isMav = (author: string) => author === 'mav'
    // 根批注作者：线程第一条
    const rootAuthor = annotationPanel.thread[0]?.author
    const isMavRoot = rootAuthor === 'mav'

    return (
      <>
        {/* 遮罩 */}
        <div
          onClick={closeAnnotationPanel}
          style={{
            position: 'absolute', inset: 0,
            background: 'rgba(0,0,0,0.4)',
            zIndex: 50
          }}
        />
        {/* 面板 */}
        <div style={{
          position: 'absolute',
          bottom: panelBottom,
          left: '50%',
          transform: 'translateX(-50%)',
          width: isMobile ? '100%' : '400px',
          maxWidth: isMobile ? '100%' : 'calc(100% - 32px)',
          background: colors.bgCard,
          borderRadius: isMobile ? '20px 20px 0 0' : '16px',
          boxShadow: `0 8px 32px ${colors.shadow}`,
          zIndex: 51,
          display: 'flex',
          flexDirection: 'column',
          maxHeight: isMobile ? `min(65dvh, calc(100% - ${panelBottom} - 12px))` : '55vh'
        }}>
          {/* 顶部：标题 + 原文引用 */}
          <div style={{
            padding: '20px 20px 0 20px',
            flexShrink: 0
          }}>
            <div style={{
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'space-between',
              marginBottom: '12px'
            }}>
              <span style={{
                fontSize: '14px',
                fontWeight: 600,
                color: colors.textPrimary
              }}>
                {isMavRoot ? label('🧠 Mav 批注') : '💬 我的批注'}
              </span>
              <button
                onClick={closeAnnotationPanel}
                style={{
                  width: '28px', height: '28px',
                  borderRadius: '14px',
                  background: colors.bgSurface,
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  color: colors.textSecondary
                }}
              >
                ×
              </button>
            </div>
            {/* 原文引用 */}
            {annotationPanel.quoteText && (
              <div style={{
                fontSize: '13px',
                lineHeight: 1.6,
                color: colors.textTertiary,
                padding: '10px 14px',
                background: colors.bgSurface,
                borderRadius: '10px',
                borderLeft: `3px solid ${isMavRoot ? colors.primary + '50' : colors.purpleMid + '50'}`,
                marginBottom: '12px',
                fontStyle: 'italic'
              }}>
                "{annotationPanel.quoteText}"
              </div>
            )}
            {/* 分隔线 */}
            <div style={{
              height: '1px',
              background: colors.border,
              marginBottom: '8px'
            }} />
          </div>

          {/* 中部：线程消息区（可滚动） */}
          <div
            ref={threadRef}
            style={{
              flex: 1,
              overflowY: 'auto',
              padding: '4px 20px 12px 20px',
              minHeight: 0
            }}
          >
            {annotationPanel.thread.map((msg, idx) => (
              <div
                key={`${msg.id}-${idx}`}
                style={{
                  marginBottom: idx < annotationPanel.thread.length - 1 ? '16px' : '0'
                }}
              >
                {/* 发送者头像 + 名字 */}
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  marginBottom: '4px'
                }}>
                  {isMav(msg.author) ? (
                    <span style={{ fontSize: '16px' }}>🧡</span>
                  ) : (
                    <div style={{
                      width: '20px',
                      height: '20px',
                      borderRadius: '10px',
                      background: `${colors.primary}30`,
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center',
                      fontSize: '10px',
                      color: colors.primary
                    }}>
                      👤
                    </div>
                  )}
                  <span style={{
                    fontSize: '12px',
                    fontWeight: 600,
                    color: isMav(msg.author) ? colors.purpleMid : colors.textSecondary
                  }}>
                    {isMav(msg.author) ? label('Mav') : '我'}
                  </span>
                </div>
                {/* 消息内容 */}
                <div style={{
                  fontSize: '14px',
                  lineHeight: 1.65,
                  color: colors.textPrimary,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  marginLeft: '28px'
                }}>
                  {msg.content}
                </div>
              </div>
            ))}
            {/* 流式接收中的文字 */}
            {isReplying && streamingText && (
              <div style={{ marginBottom: '16px' }}>
                <div style={{
                  display: 'flex',
                  alignItems: 'center',
                  gap: '8px',
                  marginBottom: '4px'
                }}>
                  <span style={{ fontSize: '16px' }}>🧡</span>
                  <span style={{
                    fontSize: '12px',
                    fontWeight: 600,
                    color: colors.purpleMid
                  }}>
                    {label('Mav')}
                  </span>
                  <span style={{
                    fontSize: '11px',
                    color: colors.textTertiary
                  }}>
                    (正在输入...)
                  </span>
                </div>
                <div style={{
                  fontSize: '14px',
                  lineHeight: 1.65,
                  color: colors.textPrimary,
                  whiteSpace: 'pre-wrap',
                  wordBreak: 'break-word',
                  marginLeft: '28px'
                }}>
                  {streamingText}
                </div>
              </div>
            )}
          </div>

          {/* 底部：输入框（固定） */}
          <div style={{
            flexShrink: 0,
            padding: '12px 16px',
            paddingBottom: isMobile ? 'calc(12px + env(safe-area-inset-bottom, 0px))' : '12px',
            borderTop: `1px solid ${colors.border}`,
            display: 'flex',
            gap: '8px',
            alignItems: 'center'
          }}>
            <textarea
              value={replyInput}
              onChange={(e) => setReplyInput(e.target.value)}
              placeholder="写下你的想法..."
              disabled={isReplying}
              rows={1}
              style={{
                flex: 1,
                minHeight: '38px',
                maxHeight: '120px',
                padding: '8px 14px',
                borderRadius: '19px',
                border: `1px solid ${colors.border}`,
                background: colors.bgSurface,
                color: colors.textPrimary,
                fontSize: '14px',
                lineHeight: 1.5,
                outline: 'none',
                resize: 'none',
                overflowY: 'auto',
                boxSizing: 'border-box',
                fieldSizing: 'content' as any
              }}
            />
            {isReplying ? (
              <button
                onClick={handleCancelReply}
                style={{
                  width: '38px', height: '38px',
                  borderRadius: '19px',
                  border: 'none',
                  cursor: 'pointer',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: `${colors.danger || '#ef4444'}20`,
                  color: colors.danger || '#ef4444',
                  flexShrink: 0
                }}
                title="停止"
              >
                ■
              </button>
            ) : (
              <button
                onClick={handleSendReply}
                disabled={!replyInput.trim()}
                style={{
                  width: '38px', height: '38px',
                  borderRadius: '19px',
                  border: 'none',
                  cursor: replyInput.trim() ? 'pointer' : 'default',
                  display: 'flex', alignItems: 'center', justifyContent: 'center',
                  background: replyInput.trim()
                    ? colors.primary
                    : `${colors.textTertiary}40`,
                  color: '#fff',
                  flexShrink: 0,
                  opacity: replyInput.trim() ? 1 : 0.5,
                  transition: 'opacity 0.15s ease'
                }}
                title="发送"
              >
                ↑
              </button>
            )}
          </div>
        </div>
      </>
    )
  }

  return (
    <div style={{
      height: '100%',
      paddingBottom: readerBottom,
      display: 'flex',
      flexDirection: 'column',
      background: colors.bgPrimary,
      position: 'relative'
    }}>
      {/* 顶部栏 */}
      <header style={{
        height: 'calc(54px + env(safe-area-inset-top, 0px))',
        background: colors.bgPrimary,
        borderBottom: `1px solid ${colors.border}`,
        display: 'flex',
        alignItems: 'center',
        paddingTop: 'env(safe-area-inset-top, 0px)',
        paddingLeft: '14px',
        paddingRight: '14px',
        gap: '12px',
        flexShrink: 0
      }}>
        <button type="button" aria-label="返回书架" onClick={onBack} style={{
          width: '38px', height: '38px',
          background: 'transparent', border: 'none',
          cursor: 'pointer', borderRadius: '8px',
          display: 'flex', alignItems: 'center', justifyContent: 'center'
        }}>
          <span style={{ fontSize: '18px', color: colors.textPrimary }}>←</span>
        </button>

        <div style={{ flex: 1, overflow: 'hidden' }}>
          <h2 style={{
            margin: 0, fontSize: '15px',
            color: colors.textPrimary, fontWeight: 600,
            letterSpacing: '-0.02em',
            whiteSpace: 'nowrap', overflow: 'hidden', textOverflow: 'ellipsis'
          }}>
            {book.title}
          </h2>
        </div>

        <div style={{ display: 'flex', gap: '8px' }}>
          <button onClick={() => setShowAnnotations(v => !v)} style={{
            width: '38px', height: '38px', borderRadius: '8px',
            background: showAnnotations
              ? `linear-gradient(135deg, ${colors.purpleMid}40 0%, ${colors.purpleMid}55 100%)`
              : `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
            border: `1px solid ${colors.purpleMid}40`,
            cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center'
          }}>
            {showAnnotations
              ? <Eye size={16} color={colors.purpleDark} strokeWidth={1.5} />
              : <EyeOff size={16} color={colors.purpleMid} strokeWidth={1.5} />
            }
          </button>
          {isMobile && (
            <button type="button" aria-label={focusMode ? '退出全屏' : '全屏阅读'} aria-pressed={focusMode} onClick={onToggleFocus} style={{
              width: '38px', height: '38px', borderRadius: '8px',
              background: focusMode
                ? `linear-gradient(135deg, ${colors.purpleMid}40 0%, ${colors.purpleMid}55 100%)`
                : `linear-gradient(135deg, ${colors.purpleMid}20 0%, ${colors.purpleMid}30 100%)`,
              border: `1px solid ${colors.purpleMid}40`,
              cursor: 'pointer', display: 'flex', alignItems: 'center', justifyContent: 'center'
            }}>
              {focusMode
                ? <Minimize2 size={16} color={colors.purpleDark} strokeWidth={1.5} />
                : <Maximize2 size={16} color={colors.purpleDark} strokeWidth={1.5} />
              }
            </button>
          )}
        </div>
      </header>

      {showPreviousChapterHint && (
        <div aria-hidden="true" style={{
          position: 'absolute',
          top: 'calc(54px + env(safe-area-inset-top, 0px) + 10px)',
          left: '50%',
          transform: 'translateX(-50%)',
          zIndex: 2,
          pointerEvents: 'none',
          padding: '5px 12px',
          borderRadius: '999px',
          background: colors.bgCard,
          color: colors.textSecondary,
          fontSize: '13px',
          whiteSpace: 'nowrap'
        }}>
          上一章
        </div>
      )}

      {/* 正文区 */}
      <div
        ref={scrollRef}
        onTouchStart={startEdgePull}
        onTouchMove={moveEdgePull}
        onTouchEnd={endEdgePull}
        onTouchCancel={releasePull}
        style={{
          flex: 1,
          overflow: 'auto',
          padding: '20px 20px',
          paddingBottom: '64px',
          backgroundColor: colors.bgPrimary,
          overscrollBehaviorY: 'none',
          WebkitOverflowScrolling: 'touch',
          WebkitTapHighlightColor: 'rgba(0,0,0,0)'
        }}
      >
        {loading ? (
          <div style={{
            display: 'flex', justifyContent: 'center',
            alignItems: 'center', height: '150px',
            color: colors.textTertiary, fontSize: '14px'
          }}>
            加载中...
          </div>
        ) : chapterData ? (
          <div ref={pageRef} style={{ maxWidth: '680px', margin: '0 auto' }}>
            {/* 文字选择气泡（浮动） */}
            {selectionBubble && (
              <div
                data-selection-bubble
                style={{
                  position: 'absolute',
                  left: `${selectionBubble.x}px`,
                  top: `${selectionBubble.y}px`,
                  transform: 'translate(-50%, -100%)',
                  zIndex: 15,
                  transition: 'top 0.15s ease, left 0.15s ease'
                }}
              >
                <button
                  onClick={() => {
                    setSelectionInput('')
                    setShowAnnotationInput(true)
                  }}
                  style={{
                    padding: '6px 14px',
                    borderRadius: '20px',
                    border: 'none',
                    background: `${colors.purpleMid}ee`,
                    color: '#fff',
                    fontSize: '13px',
                    fontWeight: 600,
                    cursor: 'pointer',
                    boxShadow: `0 2px 12px ${colors.shadow}`,
                    letterSpacing: '-0.01em',
                    whiteSpace: 'nowrap'
                  }}
                >
                  添加批注
                </button>
              </div>
            )}
            {chapterData.title && (
              <h3 style={{
                margin: '0 0 24px 0', fontSize: '18px', fontWeight: 600,
                color: colors.textPrimary, letterSpacing: '-0.02em'
              }}>
                {chapterData.title}
              </h3>
            )}
            {renderHighlightedText(chapterData.content_text, chapterData.annotations, showAnnotations)}
            {currentChapter < chapters.length - 1 && (
              <>
                <div style={{
                  marginTop: '48px', paddingTop: '24px',
                  borderTop: `1px solid ${colors.border}`,
                  textAlign: 'center', color: colors.textTertiary, fontSize: '13px'
                }}>
                  <button type="button" onClick={() => goToChapter(currentChapter + 1)}
                    style={{ border: 0, background: 'transparent', color: colors.primary, padding: '10px 16px', cursor: 'pointer' }}>
                    点击或上拉进入下一章
                  </button>
                </div>
              </>
            )}
            {currentChapter === chapters.length - 1 && (
              <div style={{
                marginTop: '48px', paddingTop: '24px',
                borderTop: `1px solid ${colors.border}`,
                textAlign: 'center', color: colors.textTertiary, fontSize: '13px'
              }}>
                全文完
              </div>
            )}
          </div>
        ) : (
          <div style={{
            display: 'flex', justifyContent: 'center',
            alignItems: 'center', height: '150px',
            color: colors.textTertiary, fontSize: '14px'
          }}>
            加载失败
          </div>
        )}
      </div>

      {/* 浮动目录按钮 */}
      {!focusMode && chapters.length > 1 && (
        <button
          onClick={() => setTocOpen(v => !v)}
          style={{
            position: 'absolute',
            right: '16px',
            bottom: isMobile ? `calc(${readerBottom} + 16px)` : '24px',
            width: '44px', height: '44px',
            borderRadius: '22px',
            background: colors.primary,
            border: 'none', cursor: 'pointer',
            display: 'flex', alignItems: 'center', justifyContent: 'center',
            boxShadow: `0 4px 16px ${colors.primary}60`,
            zIndex: 10
          }}
        >
          <List size={20} color="#fff" strokeWidth={1.8} />
        </button>
      )}

      {/* 目录遮罩 + 抽屉 — 只在打开时 mount，避免 iOS 中透明 compositing layer 压制选区高亮 */}
      {tocOpen && (
        <>
          <div
            onClick={() => setTocOpen(false)}
            style={{
              position: 'absolute', inset: 0,
              background: 'rgba(0,0,0,0.3)',
              zIndex: 20
            }}
          />
          <div style={{
            position: 'absolute', top: 0, left: 0, bottom: 0,
            width: '72%', maxWidth: '280px',
            background: colors.bgCard,
            zIndex: 21, display: 'flex', flexDirection: 'column',
            boxShadow: `4px 0 24px ${colors.shadow}`
          }}>
            <div style={{
              padding: '16px', borderBottom: `1px solid ${colors.border}`,
              fontSize: '15px', fontWeight: 600, color: colors.textPrimary,
              flexShrink: 0
            }}>
              目录
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '8px 0' }}>
              {chapters.map(ch => (
                <div
                  key={ch.chapter_index}
                  onClick={() => goToChapter(ch.chapter_index)}
                  style={{
                    padding: '12px 16px',
                    fontSize: '14px',
                    color: ch.chapter_index === currentChapter
                      ? colors.primary : colors.textPrimary,
                    fontWeight: ch.chapter_index === currentChapter ? 600 : 400,
                    cursor: 'pointer',
                    borderLeft: ch.chapter_index === currentChapter
                      ? `3px solid ${colors.primary}` : '3px solid transparent',
                    background: ch.chapter_index === currentChapter
                      ? `${colors.primary}12` : 'transparent',
                    transition: 'all 0.15s ease'
                  }}
                >
                  {ch.title || `第${ch.chapter_index + 1}章`}
                </div>
              ))}
            </div>
          </div>
        </>
      )}

      {/* 批注面板 */}
      {AnnotationPanel()}

      {/* 创建批注面板 */}
      {showAnnotationInput && selectionBubble && (
        <>
          <div
            onClick={closeAnnotationInput}
            style={{
              position: 'absolute', inset: 0,
              background: 'rgba(0,0,0,0.4)',
              zIndex: 52
            }}
          />
          <div style={{
            position: 'absolute',
            bottom: panelBottom,
            left: '50%',
            transform: 'translateX(-50%)',
            width: isMobile ? '100%' : '400px',
            maxWidth: isMobile ? '100%' : 'calc(100% - 32px)',
            background: colors.bgCard,
            borderRadius: isMobile ? '20px 20px 0 0' : '16px',
            boxShadow: `0 8px 32px ${colors.shadow}`,
            zIndex: 53,
            display: 'flex',
            flexDirection: 'column',
            maxHeight: isMobile ? `min(65dvh, calc(100% - ${panelBottom} - 12px))` : '55vh'
          }}>
            {/* 顶部：选中文 */}
            <div style={{
              padding: '20px 20px 0 20px',
              flexShrink: 0
            }}>
              <div style={{
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'space-between',
                marginBottom: '12px'
              }}>
                <span style={{
                  fontSize: '14px',
                  fontWeight: 600,
                  color: colors.textPrimary
                }}>
                  ✏️ 添加批注
                </span>
                <button
                  onClick={closeAnnotationInput}
                  style={{
                    width: '28px', height: '28px',
                    borderRadius: '14px',
                    background: colors.bgSurface,
                    border: 'none',
                    cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    color: colors.textSecondary
                  }}
                >
                  ×
                </button>
              </div>
              <div style={{
                fontSize: '13px',
                lineHeight: 1.6,
                color: colors.textTertiary,
                padding: '10px 14px',
                background: colors.bgSurface,
                borderRadius: '10px',
                borderLeft: `3px solid ${colors.purpleMid}50`,
                marginBottom: '8px',
                fontStyle: 'italic'
              }}>
                "{selectionBubble.text}"
              </div>
              <div hidden={!isCreatingAnnotation && !createDoneText} style={{
                height: '1px',
                background: colors.border,
                marginBottom: '8px'
              }} />
            </div>

            {/* 中部：流式文本区 */}
            <div
              ref={createThreadRef}
              hidden={!isCreatingAnnotation && !createDoneText}
              style={{
                flex: 1,
                overflowY: 'auto',
                padding: '4px 20px 12px 20px',
                minHeight: 0
              }}
            >
              {/* SSE 流式接收中 */}
              {isCreatingAnnotation && (
                <div>
                  {createStreamingText ? (
                    <>
                      <div style={{
                        display: 'flex',
                        alignItems: 'center',
                        gap: '8px',
                        marginBottom: '4px'
                      }}>
                        <span style={{ fontSize: '16px' }}>🧡</span>
                        <span style={{
                          fontSize: '12px',
                          fontWeight: 600,
                          color: colors.purpleMid
                        }}>
                          {label('Mav')}
                        </span>
                        <span style={{
                          fontSize: '11px',
                          color: colors.textTertiary
                        }}>
                          (正在输入...)
                        </span>
                      </div>
                      <div style={{
                        fontSize: '14px',
                        lineHeight: 1.65,
                        color: colors.textPrimary,
                        whiteSpace: 'pre-wrap',
                        wordBreak: 'break-word',
                        marginLeft: '28px'
                      }}>
                        {createStreamingText}
                      </div>
                    </>
                  ) : (
                    <div style={{
                      fontSize: '14px',
                      color: colors.textTertiary,
                      textAlign: 'center',
                      padding: '20px 0'
                    }}>
                      {label('🧡 Mav 正在思考...')}
                    </div>
                  )}
                </div>
              )}
              {/* Mav 回复完成，展示 */}
              {!isCreatingAnnotation && createDoneText && (
                <div>
                  <div style={{
                    display: 'flex',
                    alignItems: 'center',
                    gap: '8px',
                    marginBottom: '4px'
                  }}>
                    <span style={{ fontSize: '16px' }}>🧡</span>
                    <span style={{
                      fontSize: '12px',
                      fontWeight: 600,
                      color: colors.purpleMid
                    }}>
                      {label('Mav')}
                    </span>
                  </div>
                  <div style={{
                    fontSize: '14px',
                    lineHeight: 1.65,
                    color: colors.textPrimary,
                    whiteSpace: 'pre-wrap',
                    wordBreak: 'break-word',
                    marginLeft: '28px'
                  }}>
                    {createDoneText}
                  </div>
                </div>
              )}
            </div>

            {/* 底部：输入框 / 完成状态 */}
            <div style={{
              flexShrink: 0,
              padding: '12px 16px',
              paddingBottom: isMobile ? 'calc(12px + env(safe-area-inset-bottom, 0px))' : '12px',
              borderTop: `1px solid ${colors.border}`,
              display: 'flex',
              gap: '8px',
              alignItems: 'center'
            }}>
              {isCreatingAnnotation || createDoneText ? (
                // SSE 进行中 / 已完成 — 显示关闭按钮
                <button
                  onClick={closeAnnotationInput}
                  style={{
                    flex: 1,
                    height: '38px',
                    borderRadius: '19px',
                    border: 'none',
                    cursor: 'pointer',
                    display: 'flex', alignItems: 'center', justifyContent: 'center',
                    background: colors.purpleMid,
                    color: '#fff',
                    fontSize: '14px',
                    fontWeight: 600,
                    letterSpacing: '-0.01em'
                  }}
                >
                  ✓ 完成
                </button>
              ) : (
                // 发送前 — 输入框 + 发送按钮
                <>
                  <textarea
                    value={selectionInput}
                    onChange={(e) => setSelectionInput(e.target.value)}
                    placeholder="写下你的想法..."
                    rows={1}
                    style={{
                      flex: 1,
                      minHeight: '38px',
                      maxHeight: '120px',
                      padding: '8px 14px',
                      borderRadius: '19px',
                      border: `1px solid ${colors.border}`,
                      background: colors.bgSurface,
                      color: colors.textPrimary,
                      fontSize: '14px',
                      lineHeight: 1.5,
                      outline: 'none',
                      resize: 'none',
                      overflowY: 'auto',
                      boxSizing: 'border-box',
                      fieldSizing: 'content' as any
                    }}
                  />
                  <button
                    onClick={handleSendAnnotation}
                    disabled={!selectionInput.trim()}
                    style={{
                      width: '38px', height: '38px',
                      borderRadius: '19px',
                      border: 'none',
                      cursor: selectionInput.trim() ? 'pointer' : 'default',
                      display: 'flex', alignItems: 'center', justifyContent: 'center',
                      background: selectionInput.trim()
                        ? colors.purpleMid
                        : `${colors.textTertiary}40`,
                      color: '#fff',
                      flexShrink: 0,
                      opacity: selectionInput.trim() ? 1 : 0.5,
                      transition: 'opacity 0.15s ease'
                    }}
                    title="发送"
                  >
                    ↑
                  </button>
                </>
              )}
            </div>
          </div>
        </>
      )}
    </div>
  )
}
