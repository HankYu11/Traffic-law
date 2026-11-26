import { useEffect, useState } from 'react'
import { getJSON, type SopIssue } from './api'

interface LegalRef {
  cite: string
  pcode: string
  article_no: string
  paragraph?: number
  text: string
  version_label: string
}
interface Step {
  id: string
  phase: string
  title: string
  role?: string
  source_page?: number
  decision?: string
  actions: string[]
  cautions?: string[]
  forms?: string[]
  legal_refs: LegalRef[]
  issues: SopIssue[]
}
interface Sop {
  id: string
  title: string
  version_label: string
  source_url: string
  basis: string[]
  forms: string[]
  steps: Step[]
  check: { checked_at?: string; model?: string; as_of?: string }
}

export default function SopView({
  sopId,
  focusStep,
  asOf,
  onOpenArticle,
}: {
  sopId: string
  focusStep?: string
  asOf: string
  onOpenArticle: (s: { pcode: string; article_no: string; paragraph?: number | null }) => void
}) {
  const [sop, setSop] = useState<Sop | null>(null)
  const [done, setDone] = useState<Record<string, boolean>>({})
  const [onlyIssues, setOnlyIssues] = useState(false)

  useEffect(() => {
    getJSON<Sop>(`/api/sop/${sopId}?as_of=${asOf}`).then(setSop)
  }, [sopId, asOf])

  useEffect(() => {
    if (sop && focusStep) setTimeout(() => document.getElementById(`step-${focusStep}`)?.scrollIntoView({ behavior: 'smooth', block: 'center' }), 100)
  }, [sop, focusStep])

  if (!sop) return null
  const phases = [...new Set(sop.steps.map((s) => s.phase))]
  const nDone = sop.steps.filter((s) => done[s.id]).length
  const allIssues = sop.steps.flatMap((s) => s.issues)
  const issues = allIssues.filter((i) => i.decision !== 'rejected')
  const nRejected = allIssues.length - issues.length

  return (
    <div className="sop-layout">
      <div className="card sop-side">
        <div className="card-h">{sop.title}</div>
        <div className="card-b small">
          <div>版本：{sop.version_label}</div>
          <div className="progress">
            <div style={{ width: `${(nDone / sop.steps.length) * 100}%` }} />
          </div>
          <div className="muted">
            已完成 {nDone} / {sop.steps.length} 步
          </div>
          <button className="btn" style={{ marginTop: 8 }} onClick={() => setDone({})}>
            清除勾選
          </button>
          <hr style={{ border: 0, borderTop: '1px solid var(--line)', margin: '12px 0' }} />
          <b>應備表單</b>
          <ul className="forms">
            {sop.forms.map((f) => (
              <li key={f}>{f}</li>
            ))}
          </ul>
          <b>原始文件</b>
          <div>
            <a href={`/api/sop-file/${sop.id}`} target="_blank" rel="noreferrer">
              PDF 原檔
            </a>
            {' ・ '}
            <a href={sop.source_url} target="_blank" rel="noreferrer">
              公告來源
            </a>
          </div>
        </div>
      </div>

      <div>
        {issues.length > 0 && (
          <div className="notice warn">
            <b>⚠ 法規異動影響分析：</b>系統比對本 SOP 與基準日 {sop.check.as_of} 的現行法規，AI 標示 {allIssues.length} 處疑似過時，經法規室複核：<b>{issues.length}</b> 處確認需更新、{nRejected} 處排除。
            <button className={`btn ${onlyIssues ? 'on' : ''}`} style={{ marginLeft: 8 }} onClick={() => setOnlyIssues(!onlyIssues)}>
              {onlyIssues ? '顯示全部步驟' : '只看有問題的步驟'}
            </button>
          </div>
        )}
        {phases.map((ph) => {
          const steps = sop.steps.filter((s) => s.phase === ph && (!onlyIssues || s.issues.some((i) => i.decision !== 'rejected')))
          if (!steps.length) return null
          return (
            <div className="phase" key={ph}>
              <div className="phase-h">{ph}</div>
              <div className="card">
                {steps.map((s) => (
                  <div className={`step ${done[s.id] ? 'done' : ''}`} key={s.id} id={`step-${s.id}`} style={focusStep === s.id ? { background: 'var(--blue-soft)' } : undefined}>
                    <input type="checkbox" checked={!!done[s.id]} onChange={(e) => setDone({ ...done, [s.id]: e.target.checked })} />
                    <div>
                      <div className="step-t">
                        {s.title}
                        {s.role && <span className="chip">{s.role}</span>}
                        {s.source_page && <span className="chip">原文第 {s.source_page} 頁</span>}
                      </div>
                      {s.decision && <div className="decision">判斷：{s.decision}</div>}
                      <ul>
                        {s.actions.map((a, i) => (
                          <li key={i}>{a}</li>
                        ))}
                        {s.cautions?.map((a, i) => (
                          <li key={`c${i}`} style={{ color: 'var(--warn)' }}>
                            注意：{a}
                          </li>
                        ))}
                      </ul>
                      {s.forms && s.forms.length > 0 && <div className="muted">應備表單：{s.forms.join('、')}</div>}
                      {s.legal_refs.length > 0 && (
                        <div className="legal">
                          {s.legal_refs.map((r) => (
                            <button key={r.cite} title={r.text} onClick={() => onOpenArticle({ pcode: r.pcode, article_no: r.article_no, paragraph: r.paragraph })}>
                              {r.cite}
                            </button>
                          ))}
                        </div>
                      )}
                      {s.issues.map((iss, i) =>
                        iss.decision === 'rejected' ? (
                          <div className="issue rejected" key={i}>
                            <b>AI 疑似不一致・{iss.status}</b>
                            <div className="small">AI 初判：{iss.ai_explanation}</div>
                            <div className="small">排除理由：{iss.explanation}</div>
                          </div>
                        ) : (
                          <div className="issue" key={i}>
                            <b>⚠ 與現行法規不一致</b>
                            <span className="chip" style={{ marginLeft: 6 }}>
                              {iss.status}
                              {iss.reviewer && `・${iss.reviewer}`}
                            </span>
                            <div className="q">SOP 原文：{iss.sop_text}</div>
                            <div className="q law">
                              {iss.law_cite}：{iss.law_text}
                            </div>
                            <div>{iss.explanation}</div>
                            {iss.ai_note && <div className="small muted" style={{ marginTop: 4 }}>複核備註：{iss.ai_note}</div>}
                          </div>
                        ),
                      )}
                    </div>
                  </div>
                ))}
              </div>
            </div>
          )
        })}
      </div>
    </div>
  )
}
