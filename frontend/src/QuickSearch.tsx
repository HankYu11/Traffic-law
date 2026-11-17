import { useEffect, useState } from 'react'
import { getJSON, STATUS_LABEL, type Source } from './api'

const EXAMPLES = ['35條', '第7條之2', '拒絕接受測試檢定', '移置保管', '十五分鐘 漱口', '勸導 酒精濃度']

export default function QuickSearch({
  domain,
  asOf,
  onOpenArticle,
}: {
  domain: string | null
  asOf: string
  onOpenArticle: (s: { pcode: string; article_no: string; paragraph?: number | null }) => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Source[]>([])
  const [ms, setMs] = useState<number | null>(null)

  useEffect(() => {
    if (!q.trim()) {
      setRes([])
      setMs(null)
      return
    }
    const t = setTimeout(async () => {
      const t0 = performance.now()
      const r = await getJSON<{ results: Source[] }>(
        `/api/search?q=${encodeURIComponent(q)}&as_of=${asOf}${domain ? `&domain=${domain}` : ''}`,
      )
      setMs(Math.round(performance.now() - t0))
      setRes(r.results)
    }, 150)
    return () => clearTimeout(t)
  }, [q, domain, asOf])

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <input
        className="qs-input"
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="輸入條號或關鍵字，例如：35條、拒測、移置保管"
      />
      <div className="qs-hint">
        外勤快速模式：不經 AI 生成，直接比對條號與關鍵字，只列出基準日（{asOf}）有效的條文。
        {ms !== null && <> 查詢耗時 {ms} ms，{res.length} 筆。</>}
        <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {EXAMPLES.map((e) => (
            <button key={e} className="btn" onClick={() => setQ(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
      <div className="qs-res">
        {res.map((s) => (
          <div className="card src qs-item" key={s.id} onClick={() => onOpenArticle({ pcode: s.pcode, article_no: s.article_no, paragraph: s.paragraph })}>
            <div className="src-h">
              <span className="src-t">{s.cite}</span>
            </div>
            <div className="chips">
              <span className="chip">{s.law_level}</span>
              <span className={`chip ${s.status === 'in_force' ? 'ok' : 'pending'}`}>{STATUS_LABEL[s.status]}</span>
              <span className="chip">{s.version_label}</span>
              {s.exact && <span className="chip">條號命中</span>}
            </div>
            <div className="src-body">{s.text}</div>
          </div>
        ))}
      </div>
    </div>
  )
}
