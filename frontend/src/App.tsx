import { useEffect, useState } from 'react'
import Admin from './Admin'
import ArticleModal from './ArticleModal'
import Chat from './Chat'
import QuickSearch from './QuickSearch'
import SopView from './SopView'
import { getAccessCode, getJSON, postJSON, setAccessCode, type Meta } from './api'

type Page = 'chat' | 'search' | 'sop' | 'admin'

export default function App() {
  const [meta, setMeta] = useState<Meta | null>(null)
  const [page, setPage] = useState<Page>('chat')
  const [domain, setDomain] = useState<string | null>(null)
  const [asOf, setAsOf] = useState('')
  const [article, setArticle] = useState<{ pcode: string; article_no: string; paragraph?: number | null } | null>(null)
  const [sopFocus, setSopFocus] = useState<{ id: string; step?: string }>({ id: 'dui' })

  const [locked, setLocked] = useState(false)

  useEffect(() => {
    getJSON<Meta>('/api/meta').then((m) => {
      setMeta(m)
      setAsOf(m.today)
      if (m.access_required) {
        // 已存的存取碼先驗證一次
        if (!getAccessCode()) setLocked(true)
        else postJSON('/api/auth/check', {}).catch(() => setLocked(true))
      }
    })
    const onDenied = () => setLocked(true)
    window.addEventListener('access-denied', onDenied)
    return () => window.removeEventListener('access-denied', onDenied)
  }, [])

  if (!meta) return <div className="page muted">載入中…</div>
  if (locked) return <AccessGate onOk={() => setLocked(false)} />

  const openSop = (id: string, step?: string) => {
    setSopFocus({ id, step })
    setPage('sop')
  }
  const domainInfo = meta.domains.find((d) => d.id === domain)

  return (
    <>
      <div className="topbar">
        <div className="brand">
          🛡️ 警政法規智慧助理 <span className="badge">實證 Demo</span>
        </div>
        <div className="nav">
          {(
            [
              ['chat', '問答'],
              ['search', '快速查條文'],
              ['sop', '作業程序檢核'],
              ['admin', '管理後台'],
            ] as const
          ).map(([k, l]) => (
            <button key={k} className={page === k ? 'active' : ''} onClick={() => setPage(k)}>
              {l}
            </button>
          ))}
        </div>
        <div className="spacer" />
        <div className={`asof ${asOf !== meta.today ? 'changed' : ''}`} title="系統只引用基準日當天有效的條文。可調整日期查詢修法前後的規定。">
          查詢基準日
          <input type="date" value={asOf} onChange={(e) => setAsOf(e.target.value || meta.today)} />
          {asOf !== meta.today && (
            <button className="reset" onClick={() => setAsOf(meta.today)}>
              回到今天
            </button>
          )}
        </div>
      </div>

      {(page === 'chat' || page === 'search') && (
        <div className="domainbar">
          <button className={domain === null ? 'active' : ''} onClick={() => setDomain(null)}>
            全部業務
          </button>
          {meta.domains.map((d) => (
            <button key={d.id} className={domain === d.id ? 'active' : ''} onClick={() => setDomain(d.id)}>
              {d.name}
            </button>
          ))}
          <span className="desc">{domainInfo ? domainInfo.desc : '檢索所有已收錄的法規與作業程序'}</span>
        </div>
      )}

      <div className="page">
        {asOf !== meta.today && (
          <div className="notice warn">
            目前以 <b>{asOf}</b> 為基準日查詢（非今日），回答會依當時有效的條文版本。
          </div>
        )}
        <div style={{ display: page === 'chat' ? 'block' : 'none' }}>
          <Chat domain={domain} asOf={asOf} onOpenArticle={setArticle} onOpenSop={openSop} />
        </div>
        {page === 'search' && <QuickSearch domain={domain} asOf={asOf} onOpenArticle={setArticle} />}
        {page === 'sop' && <SopView sopId={sopFocus.id} focusStep={sopFocus.step} asOf={asOf} onOpenArticle={setArticle} />}
        {page === 'admin' && <Admin meta={meta} onOpenArticle={setArticle} />}
        <div className="footer-note">
          模型：{meta.llm.model}（開放權重，可於警局 H200 以 vLLM 地端部署）・向量模型：{meta.embedding}（地端執行）・法規資料：全國法規資料庫（{meta.corpus.dump_updated}）
          <br />
          本系統回答僅供勤務參考，實際執法以現行法規及主管機關解釋為準。
        </div>
      </div>

      {article && <ArticleModal pcode={article.pcode} articleNo={article.article_no} focusParagraph={article.paragraph} asOf={asOf} onClose={() => setArticle(null)} />}
    </>
  )
}

function AccessGate({ onOk }: { onOk: () => void }) {
  const [code, setCode] = useState('')
  const [err, setErr] = useState('')
  const [busy, setBusy] = useState(false)
  async function submit(e: React.FormEvent) {
    e.preventDefault()
    setBusy(true)
    setErr('')
    setAccessCode(code.trim())
    try {
      await postJSON('/api/auth/check', {})
      onOk()
    } catch {
      setAccessCode('')
      setErr('存取碼不正確')
    } finally {
      setBusy(false)
    }
  }
  return (
    <div className="gate">
      <form className="card gate-card" onSubmit={submit}>
        <div className="brand-dark">🛡️ 警政法規智慧助理</div>
        <div className="muted" style={{ margin: '6px 0 16px' }}>實證 Demo・請輸入存取碼</div>
        <input autoFocus value={code} onChange={(e) => setCode(e.target.value)} placeholder="存取碼" className="qs-input" />
        {err && <div style={{ color: 'var(--bad)', marginTop: 8 }}>{err}</div>}
        <button className="btn primary" style={{ width: '100%', marginTop: 14, padding: 12 }} disabled={busy || !code.trim()}>
          進入
        </button>
      </form>
    </div>
  )
}
