import { useEffect, useState } from 'react'
import { fmtDate, getJSON, type Meta } from './api'

interface Amendment {
  pcode: string
  promulgated: string
  order: string
  old_version_label: string
  new_version_label: string
  articles: string[]
  effective: string
  paragraph_overrides?: { article: string; new_paragraphs: number[]; effective: string; note?: string }[]
}

interface EvalItem {
  id: string
  category: string
  question: string
  expected: string[]
  retrieval_hit: boolean
  correct: boolean | null
  judge_reason: string
  verification_ok: boolean
  refused: boolean
  baseline_correct: boolean | null
  baseline_reason?: string
  latency_ms: number
}
interface EvalReport {
  available: boolean
  run_at: string
  baseline_note?: string
  model: string
  embedding: string
  as_of: string
  n: number
  summary: Record<string, number>
  by_category: { category: string; n: number; correct: number; retrieval_hit: number; baseline_correct: number }[]
  items: EvalItem[]
}
interface Stats {
  queries: number
  refused: number
  avg_retrieval_ms: number
  avg_total_ms: number
  avg_first_token_ms: number
  verified_ok: number
  answered: number
  feedback: { n: number; up: number; down: number; needs_human: number }
  by_domain: { d: string; n: number }[]
}
interface HistoryRow {
  id: number
  ts: number
  question: string
  domain: string | null
  as_of: string
  refused: number
  total_ms: number
  verification: { ok?: boolean }
  sources: string[]
}

const pct = (a: number, b: number) => (b ? Math.round((a / b) * 1000) / 10 : 0)

export default function Admin({ meta, onOpenArticle }: { meta: Meta; onOpenArticle: (s: { pcode: string; article_no: string }) => void }) {
  const [tab, setTab] = useState<'eval' | 'kb' | 'stats' | 'history'>('eval')
  return (
    <div>
      <div className="subtabs">
        {(
          [
            ['eval', '評估報告（正確率）'],
            ['kb', '知識庫與版本'],
            ['stats', '使用統計'],
            ['history', '查詢紀錄'],
          ] as const
        ).map(([k, l]) => (
          <button key={k} className={`btn ${tab === k ? 'on' : ''}`} onClick={() => setTab(k)}>
            {l}
          </button>
        ))}
      </div>
      {tab === 'eval' && <EvalTab />}
      {tab === 'kb' && <KbTab meta={meta} onOpenArticle={onOpenArticle} />}
      {tab === 'stats' && <StatsTab meta={meta} />}
      {tab === 'history' && <HistoryTab />}
    </div>
  )
}

function EvalTab() {
  const [r, setR] = useState<EvalReport | null>(null)
  const [filter, setFilter] = useState<'all' | 'wrong'>('all')
  useEffect(() => {
    getJSON<EvalReport>('/api/eval').then(setR)
  }, [])
  if (!r) return null
  if (!r.available) return <div className="card card-b">尚未執行評估。請執行 <code>python eval/run_eval.py</code>。</div>
  const s = r.summary
  const items = filter === 'all' ? r.items : r.items.filter((x) => x.correct === false || !x.retrieval_hit)
  return (
    <>
      <div className="kpis">
        <div className={`card kpi ${s.answer_accuracy >= 90 ? 'good' : 'bad'}`}>
          <div className="v">
            {s.answer_accuracy}
            <small>%</small>
          </div>
          <div className="l">回答正確率（目標 ≥ 90%）</div>
        </div>
        <div className="card kpi">
          <div className="v">
            {s.retrieval_hit_rate}
            <small>%</small>
          </div>
          <div className="l">檢索命中率（正確條文在前 8 筆）</div>
        </div>
        <div className="card kpi">
          <div className="v">
            {s.verification_pass_rate}
            <small>%</small>
          </div>
          <div className="l">引用驗證通過率</div>
        </div>
        <div className="card kpi bad">
          <div className="v">
            {s.baseline_accuracy}
            <small>%</small>
          </div>
          <div className="l">對照：同模型不接知識庫</div>
        </div>
      </div>
      <div className="grid2">
        <div className="card">
          <div className="card-h">依題型</div>
          <table className="tbl">
            <thead>
              <tr>
                <th>題型</th>
                <th className="num">題數</th>
                <th>本系統正確率</th>
                <th>不接知識庫</th>
              </tr>
            </thead>
            <tbody>
              {r.by_category.map((c) => (
                <tr key={c.category}>
                  <td>{c.category}</td>
                  <td className="num">{c.n}</td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <div className="bar ok" style={{ flex: 1 }}>
                        <div style={{ width: `${pct(c.correct, c.n)}%` }} />
                      </div>
                      {pct(c.correct, c.n)}%
                    </div>
                  </td>
                  <td>
                    <div style={{ display: 'flex', gap: 8, alignItems: 'center' }}>
                      <div className="bar bad" style={{ flex: 1 }}>
                        <div style={{ width: `${pct(c.baseline_correct, c.n)}%` }} />
                      </div>
                      {pct(c.baseline_correct, c.n)}%
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
        <div className="card">
          <div className="card-h">評估方法</div>
          <div className="card-b small">
            <ul style={{ margin: 0, paddingLeft: 18 }}>
              <li>題庫 {r.n} 題：常見、少見、特殊情境、版本與施行日、應拒答（範圍外）。每題標註「應引用的條文」與「答案要點」。</li>
              <li>檢索命中：應引用的條文是否出現在系統取回的前 8 筆。</li>
              <li>回答正確：由評分模型依「答案要點」逐項判定，要點缺漏或數字錯誤即判錯；正式驗收時改由法規室人工複核。</li>
              <li>對照組：同一個模型、不接知識庫直接回答，以相同標準評分。{r.baseline_note ? `（${r.baseline_note}）` : ''}</li>
              <li>
                執行時間 {r.run_at}・模型 {r.model}・基準日 {r.as_of}・平均回應 {(s.avg_latency_ms / 1000).toFixed(1)} 秒（評估時多題並行、經雲端 API，非單人使用時間）
              </li>
            </ul>
          </div>
        </div>
      </div>
      <div className="card" style={{ marginTop: 16 }}>
        <div className="card-h">
          逐題結果
          <span style={{ flex: 1 }} />
          <button className={`btn ${filter === 'all' ? 'on' : ''}`} onClick={() => setFilter('all')}>
            全部
          </button>
          <button className={`btn ${filter === 'wrong' ? 'on' : ''}`} onClick={() => setFilter('wrong')}>
            只看錯題
          </button>
        </div>
        <table className="tbl">
          <thead>
            <tr>
              <th>#</th>
              <th>題型</th>
              <th>問題</th>
              <th>應引用</th>
              <th>檢索</th>
              <th>本系統</th>
              <th>不接知識庫</th>
            </tr>
          </thead>
          <tbody>
            {items.map((x) => (
              <tr key={x.id}>
                <td className="muted">{x.id}</td>
                <td className="small">{x.category}</td>
                <td>
                  {x.question}
                  {x.correct === false && <div className="small" style={{ color: 'var(--bad)' }}>{x.judge_reason}</div>}
                </td>
                <td className="small">{x.expected.join('、') || '（應拒答）'}</td>
                <td>{x.retrieval_hit ? '✓' : <span style={{ color: 'var(--bad)' }}>✗</span>}</td>
                <td>{x.correct ? <span style={{ color: 'var(--ok)' }}>✓ 正確</span> : <span style={{ color: 'var(--bad)' }}>✗</span>}</td>
                <td title={x.baseline_reason}>{x.baseline_correct ? '✓' : <span style={{ color: 'var(--bad)' }}>✗</span>}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
    </>
  )
}

function KbTab({ meta, onOpenArticle }: { meta: Meta & { amendments?: Amendment[] }; onOpenArticle: (s: { pcode: string; article_no: string }) => void }) {
  const docs = meta.corpus.documents
  return (
    <div className="grid2">
      <div className="card">
        <div className="card-h">收錄文件（{docs.length} 部法規＋作業程序）</div>
        <table className="tbl">
          <thead>
            <tr>
              <th>名稱</th>
              <th>位階</th>
              <th>最新異動</th>
              <th className="num">片段數</th>
            </tr>
          </thead>
          <tbody>
            {docs.map((d) => (
              <tr key={d.pcode}>
                <td>
                  {d.name}
                  {d.partial && <span className="chip" style={{ marginLeft: 6 }}>節錄</span>}
                </td>
                <td className="small">{d.level}</td>
                <td className="small">{d.modified}</td>
                <td className="num">{d.chunks}</td>
              </tr>
            ))}
            <tr>
              <td>
                取締酒後駕車作業程序 <span className="chip sop">SOP</span>
              </td>
              <td className="small">作業程序</td>
              <td className="small">2018-04</td>
              <td className="num">{meta.corpus.sop_chunks}</td>
            </tr>
          </tbody>
        </table>
        <div className="card-b small muted">
          資料來源：法務部全國法規資料庫 Open API（資料更新 {fmtDate(meta.corpus.dump_updated)}）。切片單位為「項」，每個片段帶有法規代碼、條號、項次、版本、施行日、失效日等
          Metadata。共 {meta.corpus.total_chunks} 個片段。
        </div>
      </div>
      <div>
        <div className="card">
          <div className="card-h">版本異動紀錄</div>
          <div className="card-b">
            <div className="timeline">
              {(meta.amendments ?? []).map((a) => (
                <div key={a.promulgated + a.pcode}>
                  <div className="tl-item">
                    <div className="d">{a.promulgated} 修正公布（{a.new_version_label}）</div>
                    <div className="small">
                      道路交通管理處罰條例 第{' '}
                      {a.articles.map((x, i) => (
                        <span key={x}>
                          {i > 0 && '、'}
                          <a href="#" onClick={(e) => { e.preventDefault(); onOpenArticle({ pcode: a.pcode, article_no: x }) }}>
                            {x}
                          </a>
                        </span>
                      ))}{' '}
                      條
                    </div>
                    <div className="muted">{a.order}</div>
                  </div>
                  <div className={`tl-item ${a.effective > meta.today ? 'future' : ''}`}>
                    <div className="d">{a.effective} 施行</div>
                    <div className="small">上列條文（除另定施行日之項次）新版生效，{a.old_version_label} 同日失效。</div>
                  </div>
                  {a.paragraph_overrides?.map((o) => (
                    <div key={o.article} className={`tl-item ${o.effective > meta.today ? 'future' : ''}`}>
                      <div className="d">
                        {o.effective} 施行 {o.effective > meta.today && <span className="chip pending">尚未施行</span>}
                      </div>
                      <div className="small">
                        第 {o.article} 條第 {o.new_paragraphs.join('、')} 項
                      </div>
                    </div>
                  ))}
                </div>
              ))}
            </div>
          </div>
        </div>
        <div className="card" style={{ marginTop: 16 }}>
          <div className="card-h">更新機制</div>
          <div className="card-b small">
            <ol style={{ margin: 0, paddingLeft: 18 }}>
              <li>每週自全國法規資料庫下載最新資料（內網環境改為離線匯入）。</li>
              <li>以內容雜湊比對，只對有變動的片段重新計算向量（本次啟動：{meta.embed_stats.total} 個片段中重算 {meta.embed_stats.recomputed} 個）。</li>
              <li>修正條文自動保留舊版，依施行日切換；施行日不同的項次可分別設定。</li>
              <li>SOP 引用的法源若有異動，自動重新比對並標示「待法規室確認」。</li>
              <li>各組室透過後台上傳函釋與 SOP，經承辦確認後上線（每季／每月）。</li>
            </ol>
          </div>
        </div>
      </div>
    </div>
  )
}

function StatsTab({ meta }: { meta: Meta }) {
  const [s, setS] = useState<Stats | null>(null)
  useEffect(() => {
    getJSON<Stats>('/api/stats').then(setS)
  }, [])
  if (!s) return null
  return (
    <>
      <div className="kpis">
        <div className="card kpi">
          <div className="v">{s.queries}</div>
          <div className="l">查詢次數</div>
        </div>
        <div className="card kpi">
          <div className="v">
            {s.avg_retrieval_ms}
            <small> ms</small>
          </div>
          <div className="l">平均檢索時間</div>
        </div>
        <div className="card kpi">
          <div className="v">
            {(s.avg_total_ms / 1000).toFixed(1)}
            <small> 秒</small>
          </div>
          <div className="l">平均完整回答時間</div>
        </div>
        <div className="card kpi good">
          <div className="v">
            {pct(s.verified_ok, s.answered)}
            <small>%</small>
          </div>
          <div className="l">引用驗證通過率</div>
        </div>
      </div>
      <div className="grid2">
        <div className="card">
          <div className="card-h">使用者回饋</div>
          <div className="card-b">
            👍 {s.feedback.up}　👎 {s.feedback.down}　轉請法制單位確認 {s.feedback.needs_human}
            <div className="muted" style={{ marginTop: 6 }}>
              「轉請法制單位確認」即需求書所稱「人工詢問次數」，可用來追蹤導入後減少的人工詢問量。
            </div>
          </div>
        </div>
        <div className="card">
          <div className="card-h">今日 AI 呼叫額度</div>
          <div className="card-b">
            {meta.usage.today} / {meta.usage.limit || '不限'} 次・快取答案 {meta.usage.cached_answers} 筆
            <div className="muted" style={{ marginTop: 6 }}>公開示範網址設有每日上限；重複的問題直接重播快取答案，不再呼叫模型。</div>
          </div>
        </div>
        <div className="card">
          <div className="card-h">拒答（查無依據）</div>
          <div className="card-b">
            {s.refused} 次（{pct(s.refused, s.queries)}%）
            <div className="muted" style={{ marginTop: 6 }}>
              拒答代表問題超出知識庫範圍，可作為下一階段擴充資料的依據。
            </div>
          </div>
        </div>
      </div>
    </>
  )
}

function HistoryTab() {
  const [rows, setRows] = useState<HistoryRow[]>([])
  const [q, setQ] = useState('')
  useEffect(() => {
    const t = setTimeout(() => getJSON<HistoryRow[]>(`/api/history${q ? `?q=${encodeURIComponent(q)}` : ''}`).then(setRows), 200)
    return () => clearTimeout(t)
  }, [q])
  return (
    <div className="card">
      <div className="card-h">
        查詢紀錄
        <span style={{ flex: 1 }} />
        <input value={q} onChange={(e) => setQ(e.target.value)} placeholder="搜尋相同情境…" style={{ border: '1px solid var(--line)', borderRadius: 6, padding: '4px 8px', font: 'inherit', fontWeight: 400 }} />
      </div>
      <table className="tbl">
        <thead>
          <tr>
            <th>時間</th>
            <th>問題</th>
            <th>引用</th>
            <th>驗證</th>
            <th className="num">耗時</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r) => (
            <tr key={r.id}>
              <td className="small">{new Date(r.ts * 1000).toLocaleString('zh-TW')}</td>
              <td>{r.question}</td>
              <td className="small">{r.refused ? '（拒答）' : r.sources.slice(0, 3).join('、')}</td>
              <td>{r.refused ? '—' : r.verification.ok ? '✓' : '⚠'}</td>
              <td className="num small">{(r.total_ms / 1000).toFixed(1)}s</td>
            </tr>
          ))}
          {rows.length === 0 && (
            <tr>
              <td colSpan={5} className="muted" style={{ textAlign: 'center', padding: 24 }}>
                {q ? '沒有符合的查詢紀錄' : '尚無查詢紀錄。到「問答」頁提問後，紀錄會出現在這裡。'}
              </td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  )
}
