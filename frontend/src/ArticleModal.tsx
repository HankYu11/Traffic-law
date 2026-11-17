import { useEffect, useState } from 'react'
import { diffChars, getJSON, STATUS_LABEL, type Status } from './api'

interface Para {
  paragraph: number | null
  text: string
  status: Status
  effective_from: string | null
  effective_to: string | null
  note: string
}
interface Version {
  version_label: string
  law_name: string
  article_label: string
  url: string
  paragraphs: Para[]
}

function statusChip(p: Para) {
  const cls = p.status === 'in_force' ? 'ok' : p.status === 'pending' ? 'pending' : 'old'
  const when =
    p.status === 'pending' ? `${p.effective_from} 施行` : p.status === 'superseded' ? `${p.effective_to} 止` : p.effective_from ? `${p.effective_from} 起` : ''
  return (
    <span className={`chip ${cls}`}>
      {STATUS_LABEL[p.status]}
      {when && `・${when}`}
    </span>
  )
}

export default function ArticleModal({
  pcode,
  articleNo,
  asOf,
  focusParagraph,
  onClose,
}: {
  pcode: string
  articleNo: string
  asOf: string
  focusParagraph?: number | null
  onClose: () => void
}) {
  const [data, setData] = useState<{ versions: Version[] } | null>(null)
  const [showDiff, setShowDiff] = useState(true)

  useEffect(() => {
    getJSON<{ versions: Version[] }>(`/api/article/${pcode}/${articleNo}?as_of=${asOf}`).then(setData)
  }, [pcode, articleNo, asOf])

  useEffect(() => {
    const h = (e: KeyboardEvent) => e.key === 'Escape' && onClose()
    window.addEventListener('keydown', h)
    return () => window.removeEventListener('keydown', h)
  }, [onClose])

  if (!data) return null
  const vs = [...data.versions].sort((a, b) => (a.paragraphs[0].effective_to ? -1 : 1) - (b.paragraphs[0].effective_to ? -1 : 1))
  const two = vs.length === 2
  const [oldV, newV] = two ? vs : [null, vs[0]]
  const head = vs[0]

  return (
    <div className="overlay" onClick={onClose}>
      <div className="modal" onClick={(e) => e.stopPropagation()}>
        <div className="modal-h">
          <h3>
            {head.law_name} {head.article_label}
          </h3>
          {two && <span className="chip warn">有修正版本</span>}
          {two && (
            <button className={`btn ${showDiff ? 'on' : ''}`} onClick={() => setShowDiff(!showDiff)}>
              標示差異
            </button>
          )}
          <a className="btn" href={head.url} target="_blank" rel="noreferrer">
            全國法規資料庫原文 ↗
          </a>
          <button className="x" onClick={onClose} aria-label="關閉">
            ×
          </button>
        </div>
        <div className="modal-b">
          <div className="notice info">
            查詢基準日 <b>{asOf}</b>。系統依「施行日／失效日」判斷每一項的效力狀態，回答時只引用基準日當天有效的條文。
          </div>
          <div className={`versions ${two ? 'two' : ''}`}>
            {[oldV, newV].filter(Boolean).map((v) => (
              <div className="ver" key={v!.version_label}>
                <div className="ver-h">
                  {v!.version_label}
                  {v === oldV ? <span className="chip old">修正前</span> : two ? <span className="chip ok">修正後</span> : null}
                </div>
                {v!.paragraphs.map((p, idx) => {
                  const counterpart = two ? (v === newV ? oldV!.paragraphs[idx] : newV!.paragraphs[idx]) : undefined
                  const focus = focusParagraph && p.paragraph === focusParagraph
                  return (
                    <div className={`para ${p.status}`} key={idx} style={focus ? { outline: '2px solid var(--blue)', outlineOffset: -2 } : undefined}>
                      <div className="pn">
                        {p.paragraph ? `第 ${p.paragraph} 項` : '條文'} {statusChip(p)}
                      </div>
                      {p.note && <div className="notice pending small">{p.note}</div>}
                      {two && showDiff && counterpart ? (
                        <DiffText a={v === newV ? counterpart.text : p.text} b={v === newV ? p.text : counterpart.text} side={v === newV ? 'new' : 'old'} />
                      ) : (
                        p.text
                      )}
                    </div>
                  )
                })}
              </div>
            ))}
          </div>
        </div>
      </div>
    </div>
  )
}

function DiffText({ a, b, side }: { a: string; b: string; side: 'old' | 'new' }) {
  const parts = diffChars(a, b)
  return (
    <>
      {parts.map((p, i) =>
        p.t === 'same' ? (
          <span key={i}>{p.s}</span>
        ) : p.t === 'add' && side === 'new' ? (
          <mark key={i} className="diff-add">
            {p.s}
          </mark>
        ) : p.t === 'del' && side === 'old' ? (
          <mark key={i} className="diff-del">
            {p.s}
          </mark>
        ) : null,
      )}
    </>
  )
}
