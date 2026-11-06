import { useState, useEffect, useRef } from 'react'
import { Icon } from './Icon'
import { useIdentityLabel } from '../stores/identityStore'
import {
  getBookList,
  uploadBook,
  deleteBook,
  processBook,
  getBookStatus,
  isSupportedBookFormat,
  getBookStatusText,
  canOpenBook,
  formatFileSize // @ts-ignore - unused for now
} from '../services/readingApi'

interface BookShelfProps {
  colors: Record<string, string>
  isMobile: boolean
  onOpenBook?: (book: any) => void
  onProcessBook?: (book: any) => void
}

type Book = {
  id: number
  title: string
  author: string
  status: 'unprocessed' | 'processing' | 'ready' | 'failed'
  total_chapters: number
  processed_chapters: number
  cover_base64: string | null
}

type UploadProgress = {
  bookId: string
  progress: number
  status: 'uploading'
}

export const BookShelf: React.FC<BookShelfProps> = ({ colors, isMobile: _isMobile, onOpenBook, onProcessBook }) => { // @ts-ignore - isMobile unused for now
  const label = useIdentityLabel()
  void formatFileSize // mark as intentionally unused
  const [books, setBooks] = useState<Book[]>([])
  const [uploading, setUploading] = useState<UploadProgress | null>(null)
  const [loading, setLoading] = useState(false)
  const [deleteConfirm, setDeleteConfirm] = useState<number | null>(null)
  const [dragActive, setDragActive] = useState(false)
  const messageAreaRef = useRef<HTMLDivElement>(null)
  const pollingRef = useRef<Record<number, ReturnType<typeof setInterval>>>({})

  // 获取书籍状态指示器颜色
  const getStatusColor = (status: string) => {
    if (status === 'failed') return '#E74C3C'
    if (status === 'processing') return colors.purpleMid
    return colors.primary
  }

  // 开始轮询书籍状态
  const startPolling = (bookId: number) => {
    if (pollingRef.current[bookId]) return

    pollingRef.current[bookId] = setInterval(async () => {
      try {
        const status = await getBookStatus(bookId)
        setBooks(prev => prev.map(b =>
          b.id === bookId
            ? { ...b, status: status.status, processed_chapters: status.processed_chapters }
            : b
        ))
        if (status.status === 'ready' || status.status === 'failed') {
          clearInterval(pollingRef.current[bookId])
          delete pollingRef.current[bookId]
        }
      } catch (error) {
        console.error('获取状态失败:', error)
      }
    }, 3000)
  }

  // 加载书籍列表
  const loadBooks = async () => {
    setLoading(true)
    try {
      const result = await getBookList()
      setBooks(result.books)
      // 自动开始轮询处理中的书籍
      result.books.forEach((b: Book) => {
        if (b.status === 'processing') startPolling(b.id)
      })
    } catch (error) {
      console.error('加载书籍失败:', error)
    } finally {
      setLoading(false)
    }
  }

  useEffect(() => {
    loadBooks()
  }, [])

  // 清理轮询
  useEffect(() => {
    return () => {
      Object.values(pollingRef.current).forEach(clearInterval)
    }
  }, [])

  // 隐藏选择框
  useEffect(() => {
    const handleClickOutside = (e: MouseEvent) => {
      const target = e.target as HTMLElement
      const deleteActions = document.querySelector('[data-delete-actions="true"]')
      if (deleteActions && !deleteActions.contains(target)) {
        setDeleteConfirm(null)
      }
    }
    document.addEventListener('click', handleClickOutside)
    return () => document.removeEventListener('click', handleClickOutside)
  }, [deleteConfirm])

  // 处理文件选择
  const handleFileSelect = (e: React.ChangeEvent<HTMLInputElement>) => {
    const files = e.target.files
    if (files && files.length > 0) {
      handleUpload(files[0])
    }
  }

  // 处理拖拽事件
  const handleDrag = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    if (e.type === 'dragenter' || e.type === 'dragleave') {
      setDragActive(e.type === 'dragenter')
    }
  }

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault()
    e.stopPropagation()
    setDragActive(false)

    const files = e.dataTransfer.files
    if (files && files.length > 0) {
      handleUpload(files[0])
    }
  }

  // 上传书籍
  const handleUpload = async (file: File) => {
    // 检查文件格式
    if (!isSupportedBookFormat(file)) {
      alert('不支持的文件格式')
      return
    }

    // 检查文件大小（10MB限制）
    if (file.size > 10 * 1024 * 1024) {
      alert('文件大小不能超过10MB')
      return
    }

    // 检查是否已有相同标题的书
    const bookTitle = file.name.replace(/\.[^/.]+$/, '')
    if (books.some(b => b.title === bookTitle)) {
      alert('该书籍已存在')
      return
    }

    try {
      setUploading({
        bookId: file.name,
        progress: 0,
        status: 'uploading'
      })

      const result = await uploadBook(file, (progress) => {
        setUploading({ bookId: file.name, progress, status: 'uploading' })
      })

      // 添加到书籍列表
      const newBook: Book = {
        id: result.book_id,
        title: result.title,
        author: result.author || '未知作者',
        status: 'unprocessed',
        total_chapters: result.total_chapters,
        processed_chapters: 0,
        cover_base64: null
      }

      setBooks(prev => [...prev, newBook])
      setUploading(null)
    } catch (error) {
      console.error('上传失败:', error)
      alert('上传失败')
      setUploading(null)
    }
  }

  // 删除书籍
  const handleDelete = async (bookId: number) => {
    try {
      await deleteBook(bookId)
      setBooks(prev => prev.filter(b => b.id !== bookId))
      setDeleteConfirm(null)
    } catch (error) {
      console.error('删除失败:', error)
      alert('删除失败')
    }
  }

  return (
    <div style={{
      height: '100%',
      display: 'flex',
      flexDirection: 'column',
      background: colors.bgPrimary,
      padding: '12px',
      paddingBottom: 'calc(12px + 56px + env(safe-area-inset-bottom, 8px))',
      boxSizing: 'border-box'
    }}>
      {/* 标题栏 */}
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: '16px'
      }}>
        <h1 style={{
          margin: 0,
          fontSize: '24px',
          fontWeight: 700,
          color: colors.textPrimary,
          letterSpacing: '-0.02em'
        }}>
          书架
        </h1>
        {books.length > 0 && (
          <button
            onClick={loadBooks}
            style={{
              background: colors.bgCard,
              border: `1px solid ${colors.border}`,
              borderRadius: '8px',
              padding: '8px 14px',
              fontSize: '13px',
              color: colors.textSecondary,
              cursor: 'pointer'
            }}
          >
            刷新
          </button>
        )}
      </div>

      {/* 上传按钮区域 */}
      <div
        onClick={() => messageAreaRef.current?.querySelector('input')?.click()}
        onDragEnter={handleDrag}
        onDragLeave={handleDrag}
        onDragOver={handleDrag}
        onDrop={handleDrop}
        style={{
          position: 'relative',
          height: '100px',
          background: dragActive ? colors.bgSurface : colors.bgCard,
          border: dragActive
            ? `2px dashed ${colors.primary}`
            : `1px dashed ${colors.border}`,
          borderRadius: '12px',
          display: 'flex',
          flexDirection: 'column',
          alignItems: 'center',
          justifyContent: 'center',
          cursor: uploading?.status === 'uploading' ? 'not-allowed' : 'pointer',
          marginBottom: '20px',
          transition: 'all 0.2s ease',
          opacity: uploading?.status === 'uploading' ? 0.6 : 1
        }}
      >
        <div ref={messageAreaRef}>
          <input
            type="file"
            accept=".epub"
            onChange={handleFileSelect}
            onClick={(e) => e.stopPropagation()}
            disabled={uploading?.status === 'uploading'}
            style={{ display: 'none' }}
          />
        </div>

        {uploading?.status === 'uploading' ? (
          <>
            <Icon name="book" size={32} color={colors.purpleMid} />
            <div style={{
              marginTop: '8px',
              fontSize: '14px',
              color: colors.textSecondary,
              display: 'flex',
              alignItems: 'center',
              gap: '6px',
              flexDirection: 'column'
            }}>
              <span>上传中 {uploading.progress}%</span>
              <div style={{
                width: '200px',
                height: '4px',
                background: colors.borderLight,
                borderRadius: '2px',
                overflow: 'hidden'
              }}>
                <div style={{
                  width: `${uploading.progress}%`,
                  height: '100%',
                  background: colors.primary,
                  transition: 'width 0.3s ease'
                }} />
              </div>
            </div>
          </>
        ) : (
          <>
            <Icon name="plus" size={32} color={colors.textTertiary} />
            <div style={{
              marginTop: '8px',
              fontSize: '14px',
              color: colors.textSecondary
            }}>
              上传书籍
            </div>
          </>
        )}
      </div>

      {/* 书籍网格 */}
      <div style={{ flex: 1, overflow: 'auto' }}>
        {loading ? (
          <div style={{
            display: 'flex',
            justifyContent: 'center',
            alignItems: 'center',
            height: '150px',
            color: colors.textTertiary,
            fontSize: '14px'
          }}>
            加载中...
          </div>
        ) : books.length === 0 ? (
          <div style={{
            display: 'flex',
            flexDirection: 'column',
            alignItems: 'center',
            justifyContent: 'center',
            height: '200px',
            color: colors.textTertiary,
            gap: '8px'
          }}>
            <Icon name="book" size={48} color={colors.purplePale} />
            <span style={{ fontSize: '14px' }}>暂无书籍，请上传</span>
          </div>
        ) : (
          <div style={{
            display: 'grid',
            gridTemplateColumns: 'repeat(2, 1fr)',
            gap: '12px'
          }}>
            {books.map(book => (
              <div
                key={book.id}
                style={{
                  background: colors.bgCard,
                  borderRadius: '12px',
                  overflow: 'hidden',
                  position: 'relative',
                  boxShadow: `0 2px 8px ${colors.shadow}`,
                  transition: 'all 0.2s ease',
                  cursor: canOpenBook(book.status) ? 'pointer' : 'default',
                  border: `1px solid ${colors.border}`
                }}
                onClick={() => {
                  if (canOpenBook(book.status)) {
                    if (onOpenBook) {
                      onOpenBook(book)
                    }
                  }
                }}
              >
                {/* 书籍封面 */}
                <div style={{
                  height: '140px',
                  background: colors.bgSurface,
                  display: 'flex',
                  alignItems: 'center',
                  justifyContent: 'center',
                  position: 'relative'
                }}>
                  {book.cover_base64 ? (
                    <img
                      src={book.cover_base64}
                      alt={book.title}
                      style={{
                        width: '100%',
                        height: '100%',
                        objectFit: 'cover'
                      }}
                    />
                  ) : (
                    <Icon name="book" size={40} color={colors.purplePale} />
                  )}

                  {/* 状态标签 */}
                  {book.status !== 'ready' && (
                    <div style={{
                      position: 'absolute',
                      top: '8px',
                      right: '8px',
                      background: colors.bgCard,
                      borderRadius: '4px',
                      padding: '4px 8px',
                      fontSize: '11px',
                      color: getStatusColor(book.status),
                      fontWeight: 500,
                      boxShadow: `0 2px 4px ${colors.shadow}`
                    }}>
                      {label(getBookStatusText(book.status))}
                    </div>
                  )}
                </div>

                {/* 书籍信息 */}
                <div style={{ padding: '10px' }}>
                  <div style={{
                    fontSize: '13px',
                    fontWeight: 600,
                    color: colors.textPrimary,
                    marginBottom: '4px',
                    overflow: 'hidden',
                    textOverflow: 'ellipsis',
                    whiteSpace: 'nowrap'
                  }}>
                    {book.title}
                  </div>

                  <div style={{
                    fontSize: '11px',
                    color: colors.textSecondary,
                    marginBottom: '6px'
                  }}>
                    {book.author}
                  </div>

                  {/* 操作按钮 */}
                  {book.status === 'unprocessed' && (
                    <div style={{ display: 'flex', gap: '6px', marginTop: '8px' }}>
                      <button
                        onClick={async (e) => {
                          e.stopPropagation()
                          await processBook(book.id)
                          setBooks(prev => prev.map(b =>
                            b.id === book.id ? { ...b, status: 'processing' } : b
                          ))
                          startPolling(book.id)
                          if (onProcessBook) {
                            onProcessBook(book)
                          }
                        }}
                        style={{
                          flex: 1, padding: '6px 0', fontSize: '11px',
                          background: colors.primary, color: '#fff',
                          border: 'none', borderRadius: '6px', cursor: 'pointer'
                        }}
                      >
                        {label('给Mav读')}
                      </button>
                      <button
                        onClick={(e) => {
                          e.stopPropagation()
                          alert('阅读器开发中')
                        }}
                        style={{
                          flex: 1, padding: '6px 0', fontSize: '11px',
                          background: colors.bgSurface, color: colors.textSecondary,
                          border: `1px solid ${colors.border}`, borderRadius: '6px', cursor: 'pointer'
                        }}
                      >
                        自己读
                      </button>
                    </div>
                  )}

                  {book.status === 'processing' && (
                    <div style={{
                      marginTop: '8px', fontSize: '11px',
                      color: colors.purpleMid, textAlign: 'center'
                    }}>
                      {label('Maverick正在读书…')}<br/>
                      第{book.processed_chapters}章 / 共{book.total_chapters}章
                    </div>
                  )}

                  {book.status === 'failed' && (
                    <button
                      onClick={async (e) => {
                        e.stopPropagation()
                        await processBook(book.id)
                        setBooks(prev => prev.map(b =>
                          b.id === book.id ? { ...b, status: 'processing' } : b
                        ))
                        startPolling(book.id)
                      }}
                      style={{
                        width: '100%', marginTop: '8px', padding: '6px 0',
                        fontSize: '11px', background: '#FEE2E2', color: '#E74C3C',
                        border: 'none', borderRadius: '6px', cursor: 'pointer'
                      }}
                    >
                      处理失败，重试
                    </button>
                  )}
                </div>

                {/* 删除按钮 */}
                <button
                  onClick={(e) => {
                    e.stopPropagation()
                    setDeleteConfirm(book.id)
                  }}
                  style={{
                    position: 'absolute',
                    top: '8px',
                    left: '8px',
                    width: '28px',
                    height: '28px',
                    background: colors.bgCard,
                    border: `1px solid ${colors.border}`,
                    borderRadius: '6px',
                    display: 'flex',
                    alignItems: 'center',
                    justifyContent: 'center',
                    cursor: 'pointer',
                    opacity: 0,
                    transition: 'opacity 0.2s ease'
                  }}
                  onMouseEnter={(e) => {
                    ;(e.currentTarget as HTMLElement).style.opacity = '1'
                  }}
                  onMouseLeave={(e) => {
                    if (deleteConfirm !== book.id) {
                      ;(e.currentTarget as HTMLElement).style.opacity = '0'
                    }
                  }}
                >
                  <Icon name="close" size={16} color={colors.textTertiary} />
                </button>

                {/* 删除确认框 */}
                {deleteConfirm === book.id && (
                  <div
                    data-delete-actions="true"
                    style={{
                      position: 'absolute',
                      top: '50%',
                      left: '50%',
                      transform: 'translate(-50%, -50%)',
                      background: colors.bgCard,
                      border: `1px solid ${colors.border}`,
                      borderRadius: '10px',
                      padding: '12px',
                      boxShadow: `0 8px 24px ${colors.shadow}`,
                      zIndex: 10,
                      minWidth: '140px'
                    }}
                    onClick={(e) => e.stopPropagation()}
                  >
                    <div style={{
                      fontSize: '13px',
                      color: colors.textPrimary,
                      marginBottom: '10px',
                      textAlign: 'center'
                    }}>
                      删除此书籍?
                    </div>
                    <div style={{ display: 'flex', gap: '8px' }}>
                      <button
                        onClick={() => setDeleteConfirm(null)}
                        style={{
                          flex: 1,
                          padding: '8px',
                          background: colors.bgSurface,
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '12px',
                          color: colors.textSecondary,
                          cursor: 'pointer'
                        }}
                      >
                        取消
                      </button>
                      <button
                        onClick={() => handleDelete(book.id)}
                        style={{
                          flex: 1,
                          padding: '8px',
                          background: colors.primary,
                          border: 'none',
                          borderRadius: '6px',
                          fontSize: '12px',
                          color: '#fff',
                          cursor: 'pointer'
                        }}
                      >
                        删除
                      </button>
                    </div>
                  </div>
                )}
              </div>
            ))}
          </div>
        )}
      </div>
    </div>
  )
}
