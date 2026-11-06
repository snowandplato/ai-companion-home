import { Brush, Check, Minus, RotateCcw, X } from 'lucide-react'
import type { ArtJob } from '../services/api'
import { useIdentityLabel } from '../stores/identityStore'

interface ArtJobStatusProps {
  job?: ArtJob | null
  dark?: boolean
}

const COPY: Record<ArtJob['status'], { title: string; detail: string }> = {
  drafting: { title: 'Mav正在补一张创作笺', detail: '刚才的构想没有被画室接住' },
  queued: { title: 'Mav把画架支起来了', detail: '正在整理这次想画的东西' },
  generating: { title: '画室里正在落笔', detail: '三张候选稿正在慢慢成形' },
  reviewing: { title: 'Mav正在看画', detail: '他会亲自比较三张候选稿' },
  revising: { title: '这一批被Mav打回了', detail: '他改了画法，准备再画一次' },
  completed: { title: 'Mav挑中了这一张', detail: '其余候选稿没有公开' },
  declined: { title: 'Mav这次没有打开画布', detail: '不画也是他认真留下的决定' },
  rejected: { title: 'Mav这次没有交稿', detail: '两批里都没有他愿意发表的画' },
  prompt_failed: { title: 'Mav这轮没有留下创作笺', detail: '画室没有收到可用的创作决定' },
  failed: { title: '画室暂时没有回应', detail: '这次创作没有顺利完成' }
}

export function ArtJobStatus({ job, dark = false }: ArtJobStatusProps) {
  const label = useIdentityLabel()
  if (!job) return null
  const copy = job.mode === 'native'
    ? job.status === 'completed'
      ? { title: 'Mav画好了', detail: (job.selectedImages?.length || 1) > 1 ? `${job.selectedImages?.length}张作品已经放进对话` : '作品已经放进对话' }
      : job.status === 'failed'
        ? COPY.failed
        : { title: 'Mav正在画', detail: '这次由他直接完成作品' }
    : COPY[job.status]
  const active = ['drafting', 'queued', 'generating', 'reviewing', 'revising'].includes(job.status)
  const failed = job.status === 'failed'
  const promptFailed = job.status === 'prompt_failed'
  const rejected = job.status === 'rejected'
  const declined = job.status === 'declined'
  const completed = job.status === 'completed'
  const Icon = failed || rejected || promptFailed ? X : declined ? Minus : completed ? Check : job.status === 'revising' ? RotateCcw : Brush
  const ink = failed || rejected ? '#A46058' : declined || promptFailed ? (dark ? '#B9A89B' : '#81766E') : completed ? '#66765E' : dark ? '#D2A68B' : '#936B58'
  const background = dark ? 'rgba(124, 87, 69, 0.12)' : 'rgba(167, 116, 84, 0.075)'
  const border = dark ? 'rgba(210, 166, 139, 0.22)' : 'rgba(147, 107, 88, 0.19)'

  return (
    <div
      role="status"
      aria-live="polite"
      style={{
        display: 'flex', alignItems: 'center', gap: '11px',
        width: 'fit-content', maxWidth: '100%',
        margin: completed ? '6px 0 10px' : '8px 0 14px',
        padding: '9px 12px 9px 10px', borderRadius: '10px',
        border: `1px solid ${border}`, background,
        color: ink
      }}
    >
      <span style={{
        width: '27px', height: '27px', borderRadius: '8px', flexShrink: 0,
        display: 'grid', placeItems: 'center',
        background: dark ? 'rgba(210, 166, 139, 0.10)' : 'rgba(147, 107, 88, 0.09)'
      }}>
        <Icon size={14} strokeWidth={1.7} style={{ animation: active ? 'studioBreath 1.7s ease-in-out infinite' : 'none' }} />
      </span>
      <span style={{ minWidth: 0 }}>
        <span style={{ display: 'block', fontSize: '12.5px', lineHeight: 1.35, fontWeight: 600 }}>
          {label(copy.title)}{active && <span className="studio-ellipsis" aria-hidden="true">...</span>}
        </span>
        <span style={{ display: 'block', marginTop: '2px', fontSize: '11px', lineHeight: 1.35, opacity: 0.72 }}>
          {label(copy.detail)}
        </span>
      </span>
    </div>
  )
}
