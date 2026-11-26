import { useEffect, useState } from 'react'
import { getJSON, STATUS_LABEL, type Meta, type Source } from './api'

const EXAMPLES = ['35條', '第7條之2', '拒絕接受測試檢定', '移置保管', '十五分鐘 漱口', '勸導 酒精濃度']

const SHORT: Record<string, string> = {
  道路交通管理處罰條例: '道交條例',
  道路交通安全規則: '道安規則',
  違反道路交通管理事件統一裁罰基準及處理細則: '裁罰細則',
  道路交通事故處理辦法: '事故處理辦法',
  道路交通違規車輛移置保管及處理辦法: '移置保管辦法',
  酒駕毒駕防制教育及酒癮毒品戒癮治療實施管理辦法: '酒駕防制辦法',
  警察職權行使法: '警職法',
  中華民國刑法: '刑法',
  刑事訴訟法: '刑訴法',
}

export default function QuickSearch({
  laws,
  asOf,
  onOpenArticle,
}: {
  laws: Meta['corpus']['documents']
  asOf: string
  onOpenArticle: (s: { pcode: string; article_no: string; paragraph?: number | null }) => void
}) {
  const [q, setQ] = useState('')
  const [res, setRes] = useState<Source[]>([])
  const [ms, setMs] = useState<number | null>(null)
  const [pcode, setPcode] = useState<string | null>(null)

  useEffect(() => {
    if (!q.trim()) {
      setRes([])
      setMs(null)
      return
    }
    const t = setTimeout(async () => {
      const t0 = performance.now()
      const r = await getJSON<{ results: Source[] }>(
        `/api/search?q=${encodeURIComponent(q)}&as_of=${asOf}${pcode ? `&pcode=${pcode}` : ''}`,
      )
      setMs(Math.round(performance.now() - t0))
      setRes(r.results)
    }, 150)
    return () => clearTimeout(t)
  }, [q, pcode, asOf])

  return (
    <div style={{ maxWidth: 900, margin: '0 auto' }}>
      <input
        className="qs-input"
        autoFocus
        value={q}
        onChange={(e) => setQ(e.target.value)}
        placeholder="輸入條號或關鍵字，例如：35條、拒測、移置保管"
      />
      <div className="qs-laws">
        <span className="muted">法規：</span>
        <button className={pcode === null ? 'on' : ''} onClick={() => setPcode(null)}>
          全部
        </button>
        {laws.map((l) => (
          <button key={l.pcode} className={pcode === l.pcode ? 'on' : ''} onClick={() => setPcode(l.pcode)} title={l.name}>
            {SHORT[l.name] ?? l.name}
          </button>
        ))}
      </div>
      <div className="qs-hint">
        外勤快速模式：不經 AI 生成，直接比對條號與關鍵字，只列出基準日（{asOf}）有效的條文。
        {ms !== null && <> 查詢耗時 {ms} ms，{res.length} 筆{res.length >= 20 ? '（只列前 20 筆）' : ''}。</>}
        <div style={{ marginTop: 6, display: 'flex', gap: 6, flexWrap: 'wrap' }}>
          {EXAMPLES.map((e) => (
            <button key={e} className="btn" onClick={() => setQ(e)}>
              {e}
            </button>
          ))}
        </div>
      </div>
      {q.trim() && ms !== null && res.length === 0 && (
        <div className="card" style={{ padding: 20, marginTop: 12 }}>
          查無符合的條文。可換個關鍵字，或到「問答」用口語描述狀況。
        </div>
      )}
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
