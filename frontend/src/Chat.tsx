import { Fragment, useEffect, useRef, useState, type ReactNode } from 'react'
import ReactMarkdown from 'react-markdown'
import remarkGfm from 'remark-gfm'
import { askStream, postJSON, STATUS_LABEL, type PendingNote, type Source, type Verification } from './api'

interface Turn {
  q: string
  answer: string
  sources: Source[]
  pending: PendingNote[]
  sops: string[]
  expansions?: string[]
  phase: 'retrieving' | 'thinking' | 'writing' | 'done' | 'error'
  verification?: Verification
  refused?: boolean
  timing?: { retrieval_ms: number; first_token_ms?: number; total_ms: number }
  queryId?: number
  cached?: boolean
  feedback?: number
  asOf: string
  baseline?: string | 'loading'
  error?: string
}

const SUGGESTIONS: { tag: string; q: string; domain?: string }[] = [
  { tag: '酒駕', q: '機車駕駛人酒測值 0.3 毫克，現場要怎麼處理？罰多少？' },
  { tag: '拒測', q: '駕駛人拒絕酒測，要告知哪些法律效果？' },
  { tag: '版本控管', q: '汽車駕駛人酒駕第一次被查獲，罰鍰是多少？' },
  { tag: '程序', q: '實施酒測前要先問什麼？可以讓他漱口嗎？' },
  { tag: '交通', q: '行經設有告示的酒測站不停車就跑了，要怎麼舉發？' },
  { tag: '交通', q: '酒精濃度超過標準但只超過一點點，可以勸導不舉發嗎？' },
]

export default function Chat({
  domain,
  asOf,
  onOpenArticle,
  onOpenSop,
}: {
  domain: string | null
  asOf: string
  onOpenArticle: (s: { pcode: string; article_no: string; paragraph?: number | null }) => void
  onOpenSop: (id: string, step?: string) => void
}) {
  const [turns, setTurns] = useState<Turn[]>([])
  const [input, setInput] = useState('')
  const [busy, setBusy] = useState(false)
  const [active, setActive] = useState<number>(-1) // 右側出處對應的回合
  const [hl, setHl] = useState<number | null>(null)
  const endRef = useRef<HTMLDivElement>(null)

  useEffect(() => {
    endRef.current?.scrollIntoView({ behavior: 'smooth', block: 'end' })
  }, [turns.length, turns[turns.length - 1]?.phase])

  const update = (idx: number, patch: Partial<Turn> | ((t: Turn) => Partial<Turn>)) =>
    setTurns((ts) => ts.map((t, i) => (i === idx ? { ...t, ...(typeof patch === 'function' ? patch(t) : patch) } : t)))

  async function send(q: string) {
    q = q.trim()
    if (!q || busy) return
    setInput('')
    setBusy(true)
    const idx = turns.length
    const history = turns.flatMap((t) => [
      { role: 'user', content: t.q },
      { role: 'assistant', content: t.answer },
    ])
    setTurns((ts) => [...ts, { q, answer: '', sources: [], pending: [], sops: [], phase: 'retrieving', asOf }])
    setActive(idx)
    try {
      await askStream({ question: q, domain, as_of: asOf, history }, (e) => {
        if (e.type === 'sources') update(idx, { sources: e.sources, pending: e.pending, sops: e.sops, expansions: e.expansions, phase: 'thinking' })
        else if (e.type === 'thinking') update(idx, (t) => (t.phase === 'thinking' ? {} : { phase: 'thinking' }))
        else if (e.type === 'token') update(idx, (t) => ({ answer: t.answer + e.text, phase: 'writing' }))
        else if (e.type === 'done')
          update(idx, { answer: e.answer, verification: e.verification, refused: e.refused, timing: e.timing, queryId: e.query_id, cached: e.cached, phase: 'done' })
        else if (e.type === 'error') update(idx, { phase: 'error', error: e.message })
      })
    } catch (err) {
      update(idx, { phase: 'error', error: err instanceof Error ? err.message : String(err) })
    } finally {
      setBusy(false)
    }
  }

  async function runBaseline(idx: number) {
    update(idx, { baseline: 'loading' })
    try {
      const r = await postJSON<{ answer: string }>('/api/baseline', { question: turns[idx].q })
      update(idx, { baseline: r.answer })
    } catch (err) {
      update(idx, { baseline: `（無法取得：${err instanceof Error ? err.message : String(err)}）` })
    }
  }

  async function feedback(idx: number, rating: number, needsHuman = false) {
    const t = turns[idx]
    if (!t.queryId) return
    await postJSON('/api/feedback', { query_id: t.queryId, rating, needs_human: needsHuman })
    update(idx, { feedback: needsHuman ? 2 : rating })
  }

  const cur = turns[active]

  return (
    <div className="chat-layout">
      <div>
        <div className="thread">
          {turns.length === 0 && (
            <div className="empty">
              <h2>員警勤務法規助理</h2>
              <div>用口語描述現場狀況，系統依現行法規與作業程序回答，每一句都附出處、可點開看原文。</div>
              <div className="suggest">
                {SUGGESTIONS.map((s) => (
                  <button key={s.q} onClick={() => send(s.q)}>
                    <span className="tag">{s.tag}</span>
                    {s.q}
                  </button>
                ))}
              </div>
            </div>
          )}
          {turns.map((t, i) => (
            <Fragment key={i}>
              <div className="msg-q">{t.q}</div>
              <div className="msg-a" onMouseEnter={() => setActive(i)}>
                {t.pending.length > 0 && (
                  <div className="notice pending">
                    <b>施行日提醒：</b>
                    {t.pending.map((p) => (
                      <div key={p.cite}>
                        {p.cite} 定自 <b>{p.effective_from}</b> 施行，查詢基準日（{t.asOf}）尚未生效。
                      </div>
                    ))}
                  </div>
                )}
                {t.phase === 'retrieving' && (
                  <div className="thinking">
                    <span className="dot" /> 檢索法規與作業程序中…
                  </div>
                )}
                {t.phase === 'thinking' && (
                  <div className="thinking">
                    <span className="dot" /> 已找到 {t.sources.length} 筆相關條文，模型分析中…
                  </div>
                )}
                {t.phase === 'error' && <div className="notice warn">發生錯誤：{t.error}</div>}
                {t.answer && (
                  <Answer text={t.answer} n={t.sources.length} onCite={(n) => { setActive(i); setHl(n) }} hl={active === i ? hl : null} />
                )}
                {t.phase === 'done' && t.sops.length > 0 && !t.refused && (
                  <div className="notice info" style={{ marginTop: 12 }}>
                    本題涉及標準作業程序：
                    <button className="btn" style={{ marginLeft: 8 }} onClick={() => onOpenSop(t.sops[0])}>
                      開啟「取締酒後駕車作業程序」檢核表 →
                    </button>
                  </div>
                )}
                {t.phase === 'done' && t.verification && !t.refused && <VerifyBox v={t.verification} />}
                {t.phase === 'done' && (
                  <div className="meta-row">
                    <span>
                      檢索 {t.timing?.retrieval_ms} ms・總計 {((t.timing?.total_ms ?? 0) / 1000).toFixed(1)} 秒
                    </span>
                    <span>基準日 {t.asOf}</span>
                    {t.cached && <span title="同一題先前已回答過，直接重播，不再呼叫模型">快取答案</span>}
                    {t.expansions && t.expansions.length > 0 && (
                      <span title="員警口語 → 法條用語對照表（法規室可維護）">查詢擴充：{t.expansions.slice(0, 4).join('、')}</span>
                    )}
                    <span className="sp" />
                    {!t.refused && (
                      <button className="btn" onClick={() => runBaseline(i)} disabled={!!t.baseline}>
                        對照：不用知識庫直接問模型
                      </button>
                    )}
                    <button className={`btn ${t.feedback === 1 ? 'on' : ''}`} onClick={() => feedback(i, 1)} title="正確">
                      👍
                    </button>
                    <button className={`btn ${t.feedback === -1 ? 'on' : ''}`} onClick={() => feedback(i, -1)} title="有誤">
                      👎
                    </button>
                    <button className={`btn ${t.feedback === 2 ? 'on' : ''}`} onClick={() => feedback(i, -1, true)}>
                      轉請法制單位確認
                    </button>
                  </div>
                )}
                {t.baseline && (
                  <div className="compare">
                    <div className="col raw">
                      <h4>✗ 同一個模型，不接知識庫</h4>
                      {t.baseline === 'loading' ? (
                        <div className="thinking">
                          <span className="dot" /> 產生中…
                        </div>
                      ) : (
                        <div className="small" style={{ whiteSpace: 'pre-wrap' }}>
                          {t.baseline}
                        </div>
                      )}
                    </div>
                    <div className="col rag">
                      <h4>✓ 本系統（檢索＋引用驗證）</h4>
                      <div className="small">
                        引用 {t.verification?.cited_sources.length} 筆現行條文；數字核對
                        {t.verification?.number_issues.length ? ` 有 ${t.verification.number_issues.length} 處待確認` : '全部一致'}。
                        不接知識庫時，模型可能引用錯誤條號、過時金額，且無法提供出處供員警或民眾查證。
                      </div>
                    </div>
                  </div>
                )}
              </div>
            </Fragment>
          ))}
          <div ref={endRef} />
        </div>
        <div className="composer">
          <textarea
            value={input}
            placeholder="描述現場狀況或輸入問題，例如：酒測值 0.2 毫克、未肇事，要怎麼開單？（Enter 送出，Shift+Enter 換行）"
            onChange={(e) => setInput(e.target.value)}
            onKeyDown={(e) => {
              if (e.key === 'Enter' && !e.shiftKey && !e.nativeEvent.isComposing) {
                e.preventDefault()
                send(input)
              }
            }}
          />
          <button className="btn primary" onClick={() => send(input)} disabled={busy || !input.trim()}>
            送出
          </button>
        </div>
        {turns.length > 0 && (
          <div style={{ textAlign: 'right' }}>
            <button className="btn" onClick={() => { setTurns([]); setActive(-1) }}>
              開新對話
            </button>
          </div>
        )}
      </div>

      <div className="card sources">
        <div className="card-h">
          引用出處
          {cur && <span className="muted">（{cur.sources.length} 筆，依相關度排序）</span>}
        </div>
        {!cur && <div className="card-b muted">提問後，系統檢索到的條文與作業程序會列在這裡。點選可查看全文與修正前後對照。</div>}
        {cur?.sources.map((s) => (
          <SourceItem
            key={s.id}
            s={s}
            used={!cur.verification || cur.verification.cited_sources.includes(s.n)}
            hl={hl === s.n}
            onClick={() => {
              setHl(s.n)
              if (s.source_type === 'law') onOpenArticle({ pcode: s.pcode, article_no: s.article_no, paragraph: s.paragraph })
              else if (s.sop_id) onOpenSop(s.sop_id, s.step_id)
            }}
          />
        ))}
      </div>
    </div>
  )
}

function SourceItem({ s, used, hl, onClick }: { s: Source; used: boolean; hl: boolean; onClick: () => void }) {
  return (
    <div className={`src ${hl ? 'hl open' : ''} ${used ? '' : 'dim'}`} onClick={onClick} id={`src-${s.n}`}>
      <div className="src-h">
        <span className="src-n">S{s.n}</span>
        <span className="src-t">{s.cite}</span>
      </div>
      <div className="chips">
        {s.source_type === 'sop' ? <span className="chip sop">作業程序</span> : <span className="chip">{s.law_level}</span>}
        {s.source_type === 'law' && (
          <span className={`chip ${s.status === 'in_force' ? 'ok' : s.status === 'pending' ? 'pending' : 'old'}`}>{STATUS_LABEL[s.status]}</span>
        )}
        <span className="chip">{s.version_label}</span>
        {s.exact && <span className="chip">條號命中</span>}
        {!used && <span className="chip">未被引用</span>}
        {s.sop_issues && s.sop_issues.length > 0 && <span className="chip warn">⚠ 與現行法規不一致</span>}
      </div>
      <div className="src-body">{s.text}</div>
    </div>
  )
}

function Answer({ text, n, onCite, hl }: { text: string; n: number; onCite: (n: number) => void; hl: number | null }) {
  const renderCites = (children: ReactNode): ReactNode => {
    if (typeof children === 'string') {
      const parts = children.split(/(\[S\d+\])/g)
      return parts.map((p, i) => {
        const m = p.match(/^\[S(\d+)\]$/)
        if (!m) return p
        const k = Number(m[1])
        const bad = k < 1 || k > n
        return (
          <span
            key={i}
            className={`cite ${bad ? 'bad' : ''} ${hl === k ? 'hl' : ''}`}
            title={bad ? '無效的出處編號' : `查看出處 S${k}`}
            onClick={() => {
              onCite(k)
              document.getElementById(`src-${k}`)?.scrollIntoView({ behavior: 'smooth', block: 'nearest' })
            }}
          >
            S{k}
          </span>
        )
      })
    }
    if (Array.isArray(children)) return children.map((c, i) => <Fragment key={i}>{renderCites(c)}</Fragment>)
    return children
  }
  const wrap =
    (Tag: 'p' | 'li' | 'h3' | 'td' | 'strong') =>
    ({ children }: { children?: ReactNode }) =>
      <Tag>{renderCites(children)}</Tag>
  return (
    <ReactMarkdown
      remarkPlugins={[remarkGfm]}
      components={{ p: wrap('p'), li: wrap('li'), h3: wrap('h3'), td: wrap('td'), strong: wrap('strong') }}
    >
      {text}
    </ReactMarkdown>
  )
}

function VerifyBox({ v }: { v: Verification }) {
  const warn = !v.ok || v.uncited_sentences.length > 0
  return (
    <div className={`verify ${warn ? 'warn' : 'ok'}`}>
      {!warn ? (
        <>
          <b>✓ 引用驗證通過</b>：{v.checked_sentences} 句敘述、引用 {v.cited_sources.length} 筆條文；出處編號皆有效，金額／期間／濃度數字均與引用條文一致。
        </>
      ) : (
        <>
          <b>⚠ 引用驗證提醒</b>（建議點開出處核對）
          <ul>
            {v.invalid_citations.length > 0 && <li>無效的出處編號：{v.invalid_citations.map((x) => `S${x}`).join('、')}</li>}
            {v.number_issues.map((x, i) => (
              <li key={i}>數字「{x.value}」未出現在該句引用的條文中</li>
            ))}
            {v.uncited_sentences.length > 0 && <li>{v.uncited_sentences.length} 句敘述未附出處</li>}
          </ul>
        </>
      )}
    </div>
  )
}
