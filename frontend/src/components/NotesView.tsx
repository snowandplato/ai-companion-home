import React, { useEffect, useMemo, useRef, useState } from 'react'
import { useIdentityLabel } from '../stores/identityStore'
import {
  ArrowLeft,
  Check,
  CheckSquare,
  ChevronDown,
  Hourglass,
  ImagePlus,
  MessageCircle,
  Pencil,
  Plus,
  RotateCcw,
  Search,
  Send,
  Sparkles,
  Square,
  Trash2,
  X
} from 'lucide-react'
import {
  deleteImageNote,
  getImageNote,
  getLatestMavNoteCommentJob,
  getMavNoteCommentJob,
  listImageNotes,
  listNoteTags,
  resolveNoteAsset,
  saveImageNote,
  searchImageNotes,
  startMavNoteComment
} from '../services/notesApi'
import type { ImageNoteDetail, ImageNoteSummary, MavNoteCommentJob, NoteSearchResult } from '../services/notesApi'

type Colors = Record<string, string>
type ViewMode = 'list' | 'detail' | 'editor' | 'search' | 'timeline'

interface Props {
  colors: Colors
  isMobile: boolean
}

const formatDate = (value?: string) => {
  if (!value) return ''
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(5, 16)
  const month = String(date.getMonth() + 1).padStart(2, '0')
  const day = String(date.getDate()).padStart(2, '0')
  const hour = String(date.getHours()).padStart(2, '0')
  const minute = String(date.getMinutes()).padStart(2, '0')
  return `${month}/${day} ${hour}:${minute}`
}

const formatMonth = (value?: string) => {
  if (!value) return '未知月份'
  const date = new Date(value)
  if (Number.isNaN(date.getTime())) return value.slice(0, 7)
  return `${date.getFullYear()}年${date.getMonth() + 1}月`
}

const splitTags = (text: string) => {
  return text
    .split(/[,\s，、#]+/)
    .map(item => item.trim())
    .filter(Boolean)
}

const commentRequestKey = (noteId: number) => `mhome-note-comment-request:${noteId}`
const errorMessage = (error: unknown, fallback: string) => error instanceof Error ? error.message : fallback

const Highlight = ({ text, query, color }: { text: string, query: string, color: string }) => {
  if (!query) return <>{text}</>
  const index = text.toLowerCase().indexOf(query.toLowerCase())
  if (index < 0) return <>{text}</>
  return (
    <>
      {text.slice(0, index)}
      <span style={{ color, fontWeight: 700 }}>{text.slice(index, index + query.length)}</span>
      {text.slice(index + query.length)}
    </>
  )
}

export const NotesView: React.FC<Props> = ({ colors, isMobile }) => {
  const label = useIdentityLabel()
  const [view, setView] = useState<ViewMode>('list')
  const [notes, setNotes] = useState<ImageNoteSummary[]>([])
  const [tags, setTags] = useState<string[]>([])
  const [selectedTags, setSelectedTags] = useState<string[]>([])
  const [tagMenuOpen, setTagMenuOpen] = useState(false)
  const [selectionMode, setSelectionMode] = useState(false)
  const [selectedNoteIds, setSelectedNoteIds] = useState<number[]>([])
  const [detail, setDetail] = useState<ImageNoteDetail | null>(null)
  const [editing, setEditing] = useState<ImageNoteDetail | null>(null)
  const [loading, setLoading] = useState(false)
  const [error, setError] = useState('')
  const [searchText, setSearchText] = useState('')
  const [searchResults, setSearchResults] = useState<NoteSearchResult[]>([])
  const [commentDraft, setCommentDraft] = useState('')
  const [commenting, setCommenting] = useState(false)
  const [commentJob, setCommentJob] = useState<MavNoteCommentJob | null>(null)
  const [previewImageUrl, setPreviewImageUrl] = useState('')
  const fileInputRef = useRef<HTMLInputElement | null>(null)

  const [form, setForm] = useState({
    title: '',
    body: '',
    tagText: '',
    images: [] as File[],
    previews: [] as string[]
  })

  const loadList = async () => {
    setLoading(true)
    setError('')
    try {
      const [nextNotes, nextTags] = await Promise.all([listImageNotes(), listNoteTags()])
      setNotes(nextNotes)
      setTags(nextTags)
      setSelectedTags(prev => {
        if (prev.length === 0) return nextTags
        return prev.filter(tag => nextTags.includes(tag))
      })
    } catch (err) {
      setError(errorMessage(err, '加载失败'))
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadList()
  }, [])

  const allTagsSelected = tags.length === 0 || selectedTags.length === tags.length
  const visibleNotes = useMemo(() => {
    if (allTagsSelected) return notes
    if (selectedTags.length === 0) return []
    return notes.filter(note => note.tags?.some(tag => selectedTags.includes(tag)))
  }, [allTagsSelected, notes, selectedTags])

  const timelineGroups = useMemo(() => {
    const groups = new Map<string, ImageNoteSummary[]>()
    const sorted = [...notes].sort((a, b) => new Date(b.created_at || '').getTime() - new Date(a.created_at || '').getTime())
    sorted.forEach(note => {
      const key = formatMonth(note.created_at)
      groups.set(key, [...(groups.get(key) || []), note])
    })
    return Array.from(groups.entries()).map(([month, items]) => ({ month, items }))
  }, [notes])

  const filterLabel = allTagsSelected
    ? 'All'
    : selectedTags.length === 0
      ? '未选择标签'
      : selectedTags.length === 1
        ? `#${selectedTags[0]}`
        : `${selectedTags.length} 个标签`

  const openDetail = async (noteId: number) => {
    setLoading(true)
    setError('')
    try {
      const item = await getImageNote(noteId)
      setDetail(item)
      setView('detail')
      setCommentJob(null)
      setCommenting(false)
      try {
        const job = await getLatestMavNoteCommentJob(noteId)
        if (job?.status === 'completed' || job?.status === 'failed' || job?.status === 'interrupted') {
          localStorage.removeItem(commentRequestKey(noteId))
        }
        setCommentJob(job?.status === 'completed' ? null : job)
        setCommenting(job?.status === 'pending' || job?.status === 'running')
        if (job?.mode === 'reply' && (job.status === 'failed' || job.status === 'interrupted')) {
          setCommentDraft(job.user_reply)
        }
      } catch {
        // The note remains readable even if progress status is temporarily unavailable.
      }
    } catch (err) {
      setError(errorMessage(err, '打开失败'))
    } finally {
      setLoading(false)
    }
  }

  const openEditor = (note?: ImageNoteDetail | null) => {
    setEditing(note || null)
    setForm({
      title: note?.title || '',
      body: note?.body || '',
      tagText: note?.tags?.join(' ') || '',
      images: [],
      previews: []
    })
    setSelectionMode(false)
    setSelectedNoteIds([])
    setView('editor')
  }

  const onFiles = (files: FileList | null) => {
    if (!files) return
    const picked = Array.from(files).filter(file => file.type.startsWith('image/'))
    const previews = picked.map(file => URL.createObjectURL(file))
    setForm(prev => ({
      ...prev,
      images: [...prev.images, ...picked],
      previews: [...prev.previews, ...previews]
    }))
  }

  const removePicked = (index: number) => {
    setForm(prev => ({
      ...prev,
      images: prev.images.filter((_, i) => i !== index),
      previews: prev.previews.filter((_, i) => i !== index)
    }))
  }

  const saveNote = async () => {
    setLoading(true)
    setError('')
    try {
      const saved = await saveImageNote({
        id: editing?.id,
        title: form.title,
        body: form.body,
        tags: splitTags(form.tagText),
        images: form.images
      })
      await loadList()
      setDetail(saved)
      setView('detail')
    } catch (err) {
      setError(errorMessage(err, '保存失败'))
    } finally {
      setLoading(false)
    }
  }

  const runSearch = async () => {
    if (!searchText.trim()) return
    setLoading(true)
    setError('')
    try {
      setSearchResults(await searchImageNotes(searchText.trim()))
    } catch (err) {
      setError(errorMessage(err, '搜索失败'))
    } finally {
      setLoading(false)
    }
  }

  const runComment = async (mode: 'new' | 'regenerate' | 'supplement' | 'reply') => {
    if (!detail || commenting) return
    const reply = commentDraft.trim()
    if (mode === 'reply' && !reply) return
    setCommenting(true)
    setError('')
    const noteId = detail.id
    const storageKey = commentRequestKey(noteId)
    const requestId = localStorage.getItem(storageKey) || crypto.randomUUID()
    localStorage.setItem(storageKey, requestId)
    try {
      const job = await startMavNoteComment(noteId, { mode, user_reply: reply, request_id: requestId })
      if (job.status === 'completed') {
        setDetail(await getImageNote(noteId))
        if (mode === 'reply') setCommentDraft('')
        setCommentJob(null)
        setCommenting(false)
        localStorage.removeItem(storageKey)
      } else if (job.status === 'failed' || job.status === 'interrupted') {
        setCommentJob(job)
        setCommenting(false)
        setError(job.error || '评论没有完成，旧评论仍在')
        localStorage.removeItem(storageKey)
      } else {
        setCommentJob(job)
      }
    } catch (err) {
      try {
        const job = await getLatestMavNoteCommentJob(noteId)
        if (job?.status === 'pending' || job?.status === 'running') {
          setCommentJob(job)
          setError(label('连接暂时中断，Mav可能仍在创作；刷新后也能继续查看。'))
          return
        }
      } catch {
        // Keep the request ID so a later retry can safely find the same job.
      }
      setError(errorMessage(err, '评论启动失败，请稍后重新打开笔记确认状态'))
      setCommenting(false)
    }
  }

  const activeNoteId = detail?.id
  const activeJobId = commentJob?.id
  const activeJobStatus = commentJob?.status
  useEffect(() => {
    if (view !== 'detail' || !activeNoteId || !activeJobId || !['pending', 'running'].includes(activeJobStatus || '')) return
    const noteId = activeNoteId
    const jobId = activeJobId
    let disposed = false
    let polling = false
    const poll = async () => {
      if (polling) return
      polling = true
      try {
        const job = await getMavNoteCommentJob(noteId, jobId)
        if (disposed) return
        if (job.status === 'completed') {
          const refreshed = await getImageNote(noteId)
          if (disposed) return
          setDetail(refreshed)
          setCommentJob(null)
          setCommenting(false)
          if (job.mode === 'reply') setCommentDraft('')
          setError('')
          localStorage.removeItem(commentRequestKey(noteId))
        } else if (job.status === 'failed' || job.status === 'interrupted') {
          setCommentJob(job)
          setCommenting(false)
          setError(job.error || '评论没有完成，旧评论仍在')
          if (job.mode === 'reply') setCommentDraft(job.user_reply)
          localStorage.removeItem(commentRequestKey(noteId))
        } else {
          setCommentJob(job)
          setError('')
        }
      } catch {
        if (!disposed) setError('暂时无法查看评论进度；后台会继续创作，稍后重新打开笔记即可。')
      } finally {
        polling = false
      }
    }
    void poll()
    const timer = window.setInterval(() => { void poll() }, 2000)
    return () => { disposed = true; window.clearInterval(timer) }
  }, [view, activeNoteId, activeJobId, activeJobStatus])

  const toggleTag = (tag: string) => {
    setSelectedTags(prev => prev.includes(tag)
      ? prev.filter(item => item !== tag)
      : [...prev, tag]
    )
  }

  const toggleSelectionMode = () => {
    setSelectionMode(prev => !prev)
    setSelectedNoteIds([])
  }

  const toggleNoteSelected = (noteId: number) => {
    setSelectedNoteIds(prev => prev.includes(noteId)
      ? prev.filter(id => id !== noteId)
      : [...prev, noteId]
    )
  }

  const deleteSelectedNotes = async () => {
    if (selectedNoteIds.length === 0 || loading) return
    setLoading(true)
    setError('')
    try {
      await Promise.all(selectedNoteIds.map(id => deleteImageNote(id)))
      setSelectedNoteIds([])
      setSelectionMode(false)
      await loadList()
    } catch (err) {
      setError(errorMessage(err, '删除失败'))
    } finally {
      setLoading(false)
    }
  }

  if (!isMobile) {
    return (
      <div style={{
        height: '100%',
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'center',
        color: colors.textTertiary,
        fontSize: '15px'
      }}>
        笔记模块仅移动端可用
      </div>
    )
  }

  const shellStyle: React.CSSProperties = {
    height: '100%',
    width: '100%',
    overflow: 'hidden auto',
    scrollbarWidth: 'none',
    msOverflowStyle: 'none',
    overscrollBehaviorX: 'none',
    padding: '0 0 calc(88px + env(safe-area-inset-bottom, 8px))',
    boxSizing: 'border-box',
    background: colors.bgPrimary
  }

  const contentStyle: React.CSSProperties = {
    padding: '0 16px',
    boxSizing: 'border-box'
  }

  const iconButton = (disabled = false): React.CSSProperties => ({
    width: '38px',
    height: '38px',
    borderRadius: '12px',
    border: `1px solid ${colors.border}`,
    background: colors.bgCard,
    color: colors.textSecondary,
    display: 'flex',
    alignItems: 'center',
    justifyContent: 'center',
    opacity: disabled ? 0.5 : 1,
    cursor: disabled ? 'default' : 'pointer',
    boxShadow: `0 6px 18px ${colors.shadow}`
  })

  const inputStyle: React.CSSProperties = {
    width: '100%',
    border: `1px solid ${colors.border}`,
    background: colors.bgCard,
    color: colors.textPrimary,
    borderRadius: '12px',
    padding: '12px 13px',
    boxSizing: 'border-box',
    fontSize: '15px',
    outline: 'none',
    fontFamily: 'inherit'
  }

  const Header = ({ title, back, actions }: { title: string, back?: () => void, actions?: React.ReactNode }) => (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: '10px',
      boxSizing: 'border-box',
      margin: '0 0 14px',
      position: 'sticky',
      top: 0,
      zIndex: 5,
      background: colors.bgPrimary,
      padding: '10px 16px 8px'
    }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: '10px', minWidth: 0, flex: 1 }}>
        {back && (
          <button onClick={back} style={iconButton()}>
            <ArrowLeft size={18} strokeWidth={1.7} />
          </button>
        )}
        <div style={{
          fontSize: '22px',
          fontWeight: 680,
          color: colors.textPrimary,
          whiteSpace: 'nowrap',
          overflow: 'hidden',
          textOverflow: 'ellipsis'
        }}>
          {title}
        </div>
      </div>
      {actions && (
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', flexShrink: 0 }}>
          {actions}
        </div>
      )}
    </div>
  )

  if (view === 'search') {
    return (
      <div style={shellStyle}>
        <Header title="搜索笔记" back={() => setView('list')} />
        <div style={contentStyle}>
        <div style={{ display: 'flex', gap: '8px', marginBottom: '14px' }}>
          <input
            autoFocus
            value={searchText}
            onChange={e => setSearchText(e.target.value)}
            onKeyDown={e => e.key === 'Enter' && runSearch()}
            placeholder={label('搜索标题、正文、图片文字、Mav评论')}
            style={inputStyle}
          />
          <button onClick={runSearch} style={iconButton(!searchText.trim())}>
            <Search size={17} strokeWidth={1.7} />
          </button>
        </div>
        {error && <div style={{ color: colors.primary, fontSize: '13px', marginBottom: '10px' }}>{error}</div>}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '10px' }}>
          {searchResults.map(result => (
            <button
              key={result.id}
              onClick={() => openDetail(result.id)}
              style={{
                border: `1px solid ${colors.border}`,
                background: colors.bgCard,
                borderRadius: '12px',
                padding: '10px',
                display: 'flex',
                gap: '10px',
                textAlign: 'left',
                boxShadow: `0 6px 18px ${colors.shadow}`
              }}
            >
              {result.cover && (
                <img
                  src={resolveNoteAsset(result.cover)}
                  alt=""
                  style={{ width: '54px', height: '54px', objectFit: 'cover', borderRadius: '8px', flexShrink: 0 }}
                />
              )}
              <div style={{ minWidth: 0 }}>
                <div style={{ color: colors.textPrimary, fontSize: '14px', fontWeight: 620, marginBottom: '4px' }}>
                  {result.title}
                </div>
                <div style={{ color: colors.textSecondary, fontSize: '13px', lineHeight: 1.5 }}>
                  <Highlight text={result.snippet} query={searchText.trim()} color={colors.primary} />
                </div>
              </div>
            </button>
          ))}
          {!loading && searchText && searchResults.length === 0 && (
            <div style={{ color: colors.textTertiary, textAlign: 'center', padding: '34px 0', fontSize: '14px' }}>
              没搜到这条线索
            </div>
          )}
        </div>
        </div>
      </div>
    )
  }

  if (view === 'editor') {
    return (
      <div style={shellStyle}>
        <Header title={editing ? '编辑笔记' : '新笔记'} back={() => editing ? openDetail(editing.id) : setView('list')} />
        <div style={{ ...contentStyle, display: 'flex', flexDirection: 'column', gap: '12px' }}>
          <input ref={fileInputRef} type="file" accept="image/*" multiple hidden onChange={e => onFiles(e.target.files)} />
          <button
            onClick={() => fileInputRef.current?.click()}
            style={{
              border: `1px dashed ${colors.purpleMid}`,
              background: `${colors.bgCard}90`,
              color: colors.textSecondary,
              borderRadius: '12px',
              padding: '18px',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              gap: '8px',
              fontSize: '14px'
            }}
          >
            <ImagePlus size={17} strokeWidth={1.7} />
            添加图片
          </button>

          {editing?.images?.length ? (
            <div style={{ display: 'flex', gap: '8px', overflowX: 'auto', paddingBottom: '2px' }}>
              {editing.images.map(image => (
                <button
                  key={image.id}
                  type="button"
                  onClick={() => setPreviewImageUrl(resolveNoteAsset(image.url))}
                  style={{
                    width: '78px',
                    height: '78px',
                    border: 'none',
                    padding: 0,
                    borderRadius: '10px',
                    overflow: 'hidden',
                    background: colors.bgCard,
                    flexShrink: 0
                  }}
                >
                  <img
                    src={resolveNoteAsset(image.thumb_url)}
                    alt=""
                    style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }}
                  />
                </button>
              ))}
            </div>
          ) : null}

          {form.previews.length > 0 && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(3, 1fr)', gap: '8px' }}>
              {form.previews.map((src, index) => (
                <div key={src} style={{ position: 'relative', aspectRatio: '1 / 1' }}>
                  <button
                    type="button"
                    onClick={() => setPreviewImageUrl(src)}
                    style={{
                      width: '100%',
                      height: '100%',
                      border: 'none',
                      padding: 0,
                      borderRadius: '10px',
                      overflow: 'hidden',
                      background: colors.bgCard
                    }}
                  >
                    <img src={src} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', display: 'block' }} />
                  </button>
                  <button
                    onClick={() => removePicked(index)}
                    style={{
                      position: 'absolute',
                      top: '5px',
                      right: '5px',
                      width: '24px',
                      height: '24px',
                      borderRadius: '50%',
                      border: 'none',
                      background: 'rgba(45,43,53,0.58)',
                      color: '#fff',
                      display: 'flex',
                      alignItems: 'center',
                      justifyContent: 'center'
                    }}
                  >
                    <X size={14} />
                  </button>
                </div>
              ))}
            </div>
          )}

          <input
            value={form.title}
            onChange={e => setForm(prev => ({ ...prev, title: e.target.value }))}
            placeholder="标题可留空"
            style={inputStyle}
          />
          <textarea
            value={form.body}
            onChange={e => setForm(prev => ({ ...prev, body: e.target.value }))}
            placeholder="正文也可以先空着"
            rows={5}
            style={{ ...inputStyle, resize: 'vertical', lineHeight: 1.6 }}
          />
          <input
            value={form.tagText}
            onChange={e => setForm(prev => ({ ...prev, tagText: e.target.value }))}
            placeholder="标签，用空格或逗号隔开"
            style={inputStyle}
          />

          {tags.length > 0 && (
            <div style={{ display: 'flex', gap: '8px', overflowX: 'auto', paddingBottom: '2px' }}>
              {tags.map(tag => (
                <button
                  key={tag}
                  onClick={() => setForm(prev => ({
                    ...prev,
                    tagText: splitTags(prev.tagText).includes(tag)
                      ? splitTags(prev.tagText).filter(item => item !== tag).join(' ')
                      : [...splitTags(prev.tagText), tag].join(' ')
                  }))}
                  style={{
                    border: `1px solid ${colors.border}`,
                    background: splitTags(form.tagText).includes(tag) ? colors.primaryLight : colors.bgCard,
                    color: splitTags(form.tagText).includes(tag) ? colors.primary : colors.textSecondary,
                    borderRadius: '999px',
                    padding: '7px 10px',
                    fontSize: '13px',
                    whiteSpace: 'nowrap'
                  }}
                >
                  #{tag}
                </button>
              ))}
            </div>
          )}

          {error && <div style={{ color: colors.primary, fontSize: '13px' }}>{error}</div>}
          <button
            onClick={saveNote}
            disabled={loading || (!editing && form.images.length === 0)}
            style={{
              border: 'none',
              background: `linear-gradient(135deg, ${colors.primary} 0%, ${colors.primaryHover} 100%)`,
              color: '#fff',
              borderRadius: '14px',
              padding: '13px 16px',
              fontSize: '15px',
              fontWeight: 650,
              opacity: loading || (!editing && form.images.length === 0) ? 0.55 : 1
            }}
          >
            保存
          </button>
        </div>
        {previewImageUrl && (
          <div
            onClick={() => setPreviewImageUrl('')}
            style={{
              position: 'fixed',
              inset: 0,
              zIndex: 80,
              background: 'rgba(28, 25, 35, 0.76)',
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              padding: '18px',
              boxSizing: 'border-box'
            }}
          >
            <img
              src={previewImageUrl}
              alt=""
              onClick={event => event.stopPropagation()}
              style={{
                maxWidth: '100%',
                maxHeight: '86vh',
                objectFit: 'contain',
                borderRadius: '14px',
                boxShadow: '0 18px 48px rgba(20,18,28,0.32)',
                background: colors.bgCard
              }}
            />
            <button
              type="button"
              onClick={() => setPreviewImageUrl('')}
              style={{
                position: 'fixed',
                top: 'calc(14px + env(safe-area-inset-top, 0px))',
                right: '14px',
                width: '38px',
                height: '38px',
                borderRadius: '50%',
                border: 'none',
                background: 'rgba(255,255,255,0.88)',
                color: colors.textSecondary,
                display: 'flex',
                alignItems: 'center',
                justifyContent: 'center',
                boxShadow: '0 8px 24px rgba(20,18,28,0.22)'
              }}
            >
              <X size={18} strokeWidth={1.8} />
            </button>
          </div>
        )}
      </div>
    )
  }

  if (view === 'detail' && detail) {
    return (
      <div style={shellStyle}>
        <Header title={detail.title} back={() => setView('list')} />
        <div style={contentStyle}>
        <div style={{ display: 'flex', justifyContent: 'flex-end', gap: '8px', marginBottom: '10px' }}>
          <button onClick={() => openEditor(detail)} style={iconButton()}>
            <Pencil size={16} strokeWidth={1.7} />
          </button>
          <button
            onClick={async () => {
              if (!detail) return
              await deleteImageNote(detail.id)
              await loadList()
              setView('list')
            }}
            style={iconButton()}
          >
            <Trash2 size={16} strokeWidth={1.7} />
          </button>
        </div>

        <div style={{
          display: 'flex',
          overflowX: 'auto',
          scrollSnapType: 'x mandatory',
          gap: '10px',
          margin: '0 -16px 14px',
          padding: '0 16px 4px'
        }}>
          {detail.images.map(image => (
            <img
              key={image.id}
              src={resolveNoteAsset(image.url)}
              alt=""
              style={{
                width: '100%',
                maxHeight: '56vh',
                objectFit: 'contain',
                borderRadius: '14px',
                background: colors.bgCard,
                scrollSnapAlign: 'center',
                flex: '0 0 100%'
              }}
            />
          ))}
        </div>

        <article style={{ color: colors.textPrimary }}>
          <h1 style={{ margin: '0 0 8px', fontSize: '22px', lineHeight: 1.35, letterSpacing: 0 }}>
            {detail.title}
          </h1>
          <div style={{ color: colors.textTertiary, fontSize: '12px', marginBottom: '12px' }}>
            {formatDate(detail.created_at)}
          </div>
          {detail.body && (
            <div style={{ fontSize: '15px', lineHeight: 1.8, whiteSpace: 'pre-wrap', marginBottom: '12px' }}>
              {detail.body}
            </div>
          )}
          {detail.tags.length > 0 && (
            <div style={{ display: 'flex', gap: '8px', flexWrap: 'wrap', marginBottom: '18px' }}>
              {detail.tags.map(tag => (
                <span key={tag} style={{
                  background: colors.primaryLight,
                  color: colors.primary,
                  borderRadius: '999px',
                  padding: '5px 9px',
                  fontSize: '12px'
                }}>
                  #{tag}
                </span>
              ))}
            </div>
          )}
        </article>

        <section style={{
          borderTop: `1px solid ${colors.border}`,
          paddingTop: '14px',
          display: 'flex',
          flexDirection: 'column',
          gap: '10px'
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: '7px', color: colors.textSecondary, fontSize: '14px' }}>
              <MessageCircle size={16} strokeWidth={1.7} />
              {label('Mav评论')}
            </div>
            <div style={{ display: 'flex', gap: '7px' }}>
              <button onClick={() => runComment(detail.comments.length ? 'supplement' : 'new')} disabled={commenting} style={iconButton(commenting)}>
                <Sparkles size={15} strokeWidth={1.7} />
              </button>
              {detail.comments.length > 0 && (
                <button onClick={() => runComment('regenerate')} disabled={commenting} style={iconButton(commenting)}>
                  <RotateCcw size={15} strokeWidth={1.7} />
                </button>
              )}
            </div>
          </div>

          {detail.comments.length === 0 && (
            <div style={{
              color: colors.textTertiary,
              fontSize: '13px',
              lineHeight: 1.7,
              padding: '12px 0',
              fontStyle: 'italic'
            }}>
              {label('点一下星星，让 Mav 看看这篇笔记')}
            </div>
          )}

          {detail.comments.map(comment => (
            <div
              key={comment.id}
              style={{
                alignSelf: comment.author === 'user' ? 'flex-end' : 'flex-start',
                maxWidth: '88%',
                background: comment.author === 'user' ? '#C9B9DA' : colors.bgCard,
                color: colors.textPrimary,
                border: `1px solid ${comment.author === 'user' ? 'transparent' : colors.border}`,
                borderRadius: '14px',
                padding: '10px 12px',
                fontSize: '14px',
                lineHeight: 1.7,
                whiteSpace: 'pre-wrap',
                boxShadow: `0 5px 16px ${colors.shadow}`
              }}
            >
              {comment.content || label('Mav正在看...')}
            </div>
          ))}

          {commentJob && (commentJob.status === 'pending' || commentJob.status === 'running') && (
            <>
              {commentJob.mode === 'reply' && commentJob.user_reply && (
                <div style={{ alignSelf: 'flex-end', maxWidth: '88%', background: '#C9B9DA', color: colors.textPrimary, borderRadius: '14px', padding: '10px 12px', fontSize: '14px', lineHeight: 1.7, whiteSpace: 'pre-wrap' }}>
                  {commentJob.user_reply}
                </div>
              )}
              <div style={{ alignSelf: 'flex-start', maxWidth: '88%', background: colors.bgCard, color: colors.textPrimary, border: `1px solid ${colors.border}`, borderRadius: '14px', padding: '10px 12px', fontSize: '14px', lineHeight: 1.7, whiteSpace: 'pre-wrap', boxShadow: `0 5px 16px ${colors.shadow}` }}>
                {commentJob.draft_text || label('Mav正在看...')}
                <div style={{ color: colors.textTertiary, fontSize: '12px', marginTop: '6px' }}>即使离开页面，完成后也会保存到这篇笔记</div>
              </div>
            </>
          )}

          {commentJob && (commentJob.status === 'failed' || commentJob.status === 'interrupted') && (
            <div style={{ color: colors.textTertiary, fontSize: '13px', lineHeight: 1.6 }}>
              {commentJob.error || '这次评论未完成'}。已有评论没有被删除，可以确认后重试。
            </div>
          )}

          {commentJob?.status === 'completed' && (
            <div style={{ color: colors.textTertiary, fontSize: '13px', lineHeight: 1.6 }}>
              评论已经保存；当前页面暂时没能刷新出来，重新打开这篇笔记即可查看。
            </div>
          )}

          {detail.comments.length > 0 && (
            <div style={{ display: 'flex', gap: '8px', marginTop: '4px', alignItems: 'flex-end' }}>
              <textarea
                value={commentDraft}
                onChange={e => setCommentDraft(e.target.value)}
                disabled={commenting}
                placeholder={label('回复Mav...')}
                rows={1}
                style={{
                  ...inputStyle,
                  minHeight: '44px',
                  maxHeight: '168px',
                  fieldSizing: 'content',
                  resize: 'none',
                  lineHeight: 1.5,
                  overflowY: 'auto'
                }}
              />
              <button onClick={() => runComment('reply')} disabled={commenting || !commentDraft.trim()} style={iconButton(commenting || !commentDraft.trim())}>
                <Send size={16} strokeWidth={1.7} />
              </button>
            </div>
          )}
        </section>
      </div>
      </div>
    )
  }

  if (view === 'timeline') {
    return (
      <div style={shellStyle}>
        <Header title="时间地图" back={() => setView('list')} />
        <div style={contentStyle}>
        {timelineGroups.length === 0 && (
          <div style={{ color: colors.textTertiary, textAlign: 'center', padding: '46px 20px', fontSize: '14px', lineHeight: 1.8 }}>
            这里还没有可以标记时间的笔记
          </div>
        )}
        <div style={{ display: 'flex', flexDirection: 'column', gap: '22px' }}>
          {timelineGroups.map(group => (
            <section key={group.month}>
              <div style={{
                display: 'flex',
                alignItems: 'baseline',
                justifyContent: 'space-between',
                marginBottom: '9px'
              }}>
                <h2 style={{
                  margin: 0,
                  color: colors.textPrimary,
                  fontSize: '17px',
                  fontWeight: 680,
                  letterSpacing: 0
                }}>
                  {group.month}
                </h2>
                <span style={{ color: colors.textTertiary, fontSize: '12px' }}>
                  {group.items.length} 篇
                </span>
              </div>
              <div style={{
                display: 'grid',
                gridTemplateColumns: 'repeat(4, minmax(0, 1fr))',
                gap: '7px'
              }}>
                {group.items.map(note => (
                  <button
                    key={note.id}
                    type="button"
                    onClick={() => openDetail(note.id)}
                    style={{
                      aspectRatio: '1 / 1',
                      border: `1px solid ${colors.borderLight}`,
                      borderRadius: '10px',
                      padding: 0,
                      overflow: 'hidden',
                      background: colors.bgCard,
                      boxShadow: `0 5px 14px ${colors.shadow}`
                    }}
                  >
                    {note.cover ? (
                      <img
                        src={resolveNoteAsset(note.cover)}
                        alt=""
                        style={{
                          width: '100%',
                          height: '100%',
                          display: 'block',
                          objectFit: 'cover'
                        }}
                      />
                    ) : (
                      <span style={{
                        display: 'flex',
                        width: '100%',
                        height: '100%',
                        alignItems: 'center',
                        justifyContent: 'center',
                        boxSizing: 'border-box',
                        padding: '7px',
                        color: colors.textTertiary,
                        fontSize: '11px',
                        lineHeight: 1.35,
                        textAlign: 'center',
                        overflow: 'hidden'
                      }}>
                        {note.title}
                      </span>
                    )}
                  </button>
                ))}
              </div>
            </section>
          ))}
        </div>
      </div>
      </div>
    )
  }

  return (
    <div style={shellStyle}>
      <div style={{
        display: 'flex',
        alignItems: 'center',
        justifyContent: 'space-between',
        gap: '10px',
        boxSizing: 'border-box',
        margin: '0 0 14px',
        position: 'sticky',
        top: 0,
        zIndex: 6,
        background: colors.bgPrimary,
        padding: '10px 16px 8px'
      }}>
        <div style={{ display: 'grid', gridTemplateColumns: 'auto auto', alignItems: 'center', columnGap: '7px' }}>
          <div style={{ fontSize: '24px', fontWeight: 700, color: colors.textPrimary, letterSpacing: 0 }}>
            笔记
          </div>
          <button
            type="button"
            onClick={() => setView('timeline')}
            aria-label="时间地图"
            style={{
              width: '28px',
              height: '28px',
              border: 'none',
              borderRadius: '50%',
              background: 'transparent',
              color: colors.primary,
              display: 'flex',
              alignItems: 'center',
              justifyContent: 'center',
              opacity: 0.82
            }}
          >
            <Hourglass size={17} strokeWidth={1.75} />
          </button>
          <div style={{ color: colors.textTertiary, fontSize: '12px', marginTop: '2px' }}>
            {selectionMode ? `已选 ${selectedNoteIds.length} 篇` : filterLabel}
          </div>
        </div>
        <div style={{ display: 'flex', gap: '8px', alignItems: 'center' }}>
          <button onClick={() => setView('search')} style={iconButton()}>
            <Search size={17} strokeWidth={1.7} />
          </button>
          <div style={{ position: 'relative' }}>
            <button onClick={() => setTagMenuOpen(prev => !prev)} style={iconButton()}>
              <ChevronDown size={16} strokeWidth={1.7} />
            </button>
            {tagMenuOpen && (
              <div style={{
                position: 'absolute',
                right: 0,
                top: '46px',
                width: '240px',
                maxWidth: 'calc(100vw - 32px)',
                maxHeight: '270px',
                overflowY: 'auto',
                border: `1px solid ${colors.border}`,
                background: colors.bgCard,
                borderRadius: '12px',
                padding: '8px',
                boxShadow: `0 12px 28px ${colors.shadowHover}`,
                zIndex: 10
              }}>
                <button
                  onClick={() => setSelectedTags(allTagsSelected ? [] : tags)}
                  style={{
                    width: '100%',
                    border: 'none',
                    background: allTagsSelected ? colors.primaryLight : 'transparent',
                    color: allTagsSelected ? colors.primary : colors.textSecondary,
                    borderRadius: '9px',
                    padding: '8px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'space-between',
                    fontSize: '13px',
                    marginBottom: '4px'
                  }}
                >
                  全部
                  {allTagsSelected && <Check size={14} />}
                </button>
                {tags.map(tag => {
                  const checked = selectedTags.includes(tag)
                  return (
                    <button
                      key={tag}
                      onClick={() => toggleTag(tag)}
                      style={{
                        width: '100%',
                        border: 'none',
                        background: checked ? colors.primaryLight : 'transparent',
                        color: checked ? colors.primary : colors.textSecondary,
                        borderRadius: '9px',
                        padding: '8px',
                        display: 'flex',
                        alignItems: 'center',
                        justifyContent: 'space-between',
                        fontSize: '13px',
                        textAlign: 'left'
                      }}
                    >
                      <span style={{ overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>#{tag}</span>
                      {checked && <Check size={14} />}
                    </button>
                  )
                })}
              </div>
            )}
          </div>
          <button onClick={toggleSelectionMode} style={iconButton()}>
            {selectionMode ? <X size={17} strokeWidth={1.7} /> : <CheckSquare size={17} strokeWidth={1.7} />}
          </button>
        </div>
      </div>

      <div style={contentStyle}>
      {error && <div style={{ color: colors.primary, fontSize: '13px', marginBottom: '10px' }}>{error}</div>}
      {selectionMode && (
        <div style={{
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'space-between',
          gap: '8px',
          marginBottom: '12px',
          padding: '10px 11px',
          border: `1px solid ${colors.border}`,
          background: colors.bgCard,
          borderRadius: '12px',
          boxShadow: `0 6px 18px ${colors.shadow}`
        }}>
          <button
            onClick={() => setSelectedNoteIds(selectedNoteIds.length === visibleNotes.length ? [] : visibleNotes.map(note => note.id))}
            style={{ border: 'none', background: 'transparent', color: colors.textSecondary, fontSize: '13px' }}
          >
            {selectedNoteIds.length === visibleNotes.length ? '取消全选' : '全选当前'}
          </button>
          <button
            onClick={deleteSelectedNotes}
            disabled={selectedNoteIds.length === 0 || loading}
            style={{
              border: 'none',
              background: selectedNoteIds.length === 0 ? colors.borderLight : colors.primary,
              color: selectedNoteIds.length === 0 ? colors.textTertiary : '#fff',
              borderRadius: '10px',
              padding: '8px 12px',
              fontSize: '13px',
              fontWeight: 650
            }}
          >
            删除 {selectedNoteIds.length || ''}
          </button>
        </div>
      )}
      {loading && notes.length === 0 && (
        <div style={{ color: colors.textTertiary, textAlign: 'center', padding: '34px 0', fontSize: '14px' }}>
          正在翻笔记...
        </div>
      )}
      {!loading && visibleNotes.length === 0 && (
        <div style={{ color: colors.textTertiary, textAlign: 'center', padding: '46px 20px', fontSize: '14px', lineHeight: 1.8 }}>
          这里还没有符合条件的笔记
        </div>
      )}
      <div style={{
        display: 'grid',
        gridTemplateColumns: 'repeat(2, minmax(0, 1fr))',
        gap: '10px',
        alignItems: 'start'
      }}>
        {visibleNotes.map(note => {
          const selected = selectedNoteIds.includes(note.id)
          return (
            <button
              key={note.id}
              onClick={() => selectionMode ? toggleNoteSelected(note.id) : openDetail(note.id)}
              style={{
                width: '100%',
                border: `1px solid ${selected ? colors.primary : colors.border}`,
                borderRadius: '12px',
                background: colors.bgCard,
                padding: 0,
                textAlign: 'left',
                overflow: 'hidden',
                boxShadow: `0 7px 18px ${colors.shadow}`,
                position: 'relative'
              }}
            >
              {selectionMode && (
                <div style={{
                  position: 'absolute',
                  top: '7px',
                  right: '7px',
                  zIndex: 2,
                  width: '24px',
                  height: '24px',
                  borderRadius: '50%',
                  background: selected ? colors.primary : 'rgba(255,255,255,0.88)',
                  color: selected ? '#fff' : colors.textTertiary,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  boxShadow: `0 4px 12px ${colors.shadow}`
                }}>
                  {selected ? <Check size={14} /> : <Square size={14} />}
                </div>
              )}
              {note.cover && (
                <div style={{ width: '100%', height: '128px', overflow: 'hidden', background: colors.purpleLight }}>
                  <img
                    src={resolveNoteAsset(note.cover)}
                    alt=""
                    style={{
                      width: '100%',
                      height: '100%',
                      display: 'block',
                      objectFit: 'cover',
                      objectPosition: 'center center'
                    }}
                  />
                </div>
              )}
              <div style={{ padding: '9px 10px 10px' }}>
                <div style={{
                  color: colors.textPrimary,
                  fontSize: '14px',
                  fontWeight: 620,
                  lineHeight: 1.45,
                  height: '40px',
                  marginBottom: '5px',
                  wordBreak: 'break-word',
                  display: '-webkit-box',
                  WebkitLineClamp: 2,
                  WebkitBoxOrient: 'vertical',
                  overflow: 'hidden'
                }}>
                  {note.title}
                </div>
                <div style={{ color: colors.textTertiary, fontSize: '11px' }}>
                  {formatDate(note.created_at)}
                </div>
              </div>
            </button>
          )
        })}
      </div>
      </div>

      <button
        onClick={() => openEditor()}
        style={{
          position: 'fixed',
          right: '16px',
          bottom: 'calc(84px + env(safe-area-inset-bottom, 8px))',
          width: '48px',
          height: '48px',
          borderRadius: '16px',
          border: 'none',
          background: `linear-gradient(135deg, ${colors.primary} 0%, ${colors.primaryHover} 100%)`,
          color: '#fff',
          display: 'flex',
          alignItems: 'center',
          justifyContent: 'center',
          boxShadow: `0 12px 28px ${colors.shadowHover}`,
          zIndex: 12
        }}
      >
        <Plus size={22} strokeWidth={1.9} />
      </button>
    </div>
  )
}
