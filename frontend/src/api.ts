export type Status = 'in_force' | 'pending' | 'superseded'

export interface SopIssue {
  step_id: string
  step_title: string
  sop_text: string
  law_cite: string
  law_text: string
  explanation: string
  status: string
  decision?: 'pending' | 'confirmed' | 'corrected' | 'rejected'
  reviewer?: string
  reviewed_at?: string
  ai_explanation?: string
  ai_note?: string
}

export interface Source {
  n: number
  id: string
  source_type: 'law' | 'sop'
  pcode: string
  law_name: string
  law_level: string
  article_no: string
  article_label: string
  paragraph: number | null
  cite: string
  text: string
  article_text: string
  version_label: string
  effective_from: string | null
  effective_to: string | null
  note: string
  url: string
  status: Status
  score: number
  dense: number
  exact: boolean
  sop_id?: string
  step_id?: string
  source_page?: number
  sop_issues?: SopIssue[]
}

export interface Verification {
  ok: boolean
  cited_sources: number[]
  invalid_citations: number[]
  uncited_sentences: string[]
  number_issues: { sentence: string; value: string }[]
  checked_sentences: number
}

export interface PendingNote {
  cite: string
  effective_from: string
  note: string
  text: string
}

export interface Meta {
  today: string
  domains: { id: string; name: string; desc: string }[]
  corpus: {
    documents: { pcode: string; name: string; level: string; modified: string; domains: string[]; chunks: number; source: string; partial: boolean }[]
    sop_chunks: number
    total_chunks: number
    dump_updated: string
  }
  llm: { model: string; endpoint: string }
  embedding: string
  embed_stats: { total: number; recomputed: number }
  access_required: boolean
  usage: { today: number; limit: number; cached_answers: number }
}

export type AskEvent =
  | { type: 'sources'; sources: Source[]; retrieval_ms: number; pending: PendingNote[]; sops: string[]; expansions: string[] }
  | { type: 'thinking' }
  | { type: 'token'; text: string }
  | {
      type: 'done'
      answer: string
      refused: boolean
      verification: Verification
      timing: { retrieval_ms: number; first_token_ms?: number; total_ms: number }
      query_id: number
      cached?: boolean
    }
  | { type: 'error'; message: string }

// ---------- 存取碼（公開示範網址用） ----------
const CODE_KEY = 'police-demo-access-code'
let accessCode = ''
try {
  accessCode = localStorage.getItem(CODE_KEY) ?? ''
} catch {
  /* 私密視窗等情況：不記住存取碼 */
}
export function getAccessCode() {
  return accessCode
}
export function setAccessCode(code: string) {
  accessCode = code
  try {
    if (code) localStorage.setItem(CODE_KEY, code)
    else localStorage.removeItem(CODE_KEY)
  } catch {
    /* ignore */
  }
}
function authHeaders(): Record<string, string> {
  return accessCode ? { 'X-Access-Code': accessCode } : {}
}
export class ApiError extends Error {
  status: number
  constructor(status: number, message: string) {
    super(message)
    this.status = status
  }
}
async function errorFrom(r: Response): Promise<ApiError> {
  let msg = `${r.status}`
  try {
    const j = await r.json()
    msg = j.detail ?? msg
  } catch {
    /* ignore */
  }
  if (r.status === 401) window.dispatchEvent(new Event('access-denied'))
  return new ApiError(r.status, msg)
}

export async function getJSON<T>(url: string): Promise<T> {
  const r = await fetch(url)
  if (!r.ok) throw new Error(`${r.status} ${url}`)
  return r.json()
}

export async function postJSON<T>(url: string, body: unknown): Promise<T> {
  const r = await fetch(url, { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) })
  if (!r.ok) throw await errorFrom(r)
  return r.json()
}

export async function askStream(
  body: { question: string; domain: string | null; as_of: string; history: { role: string; content: string }[] },
  onEvent: (e: AskEvent) => void,
) {
  const r = await fetch('/api/ask', { method: 'POST', headers: { 'Content-Type': 'application/json', ...authHeaders() }, body: JSON.stringify(body) })
  if (!r.ok || !r.body) throw await errorFrom(r)
  const reader = r.body.getReader()
  const dec = new TextDecoder()
  let buf = ''
  for (;;) {
    const { done, value } = await reader.read()
    if (done) break
    buf += dec.decode(value, { stream: true })
    let i
    while ((i = buf.indexOf('\n\n')) >= 0) {
      const chunk = buf.slice(0, i)
      buf = buf.slice(i + 2)
      const line = chunk.split('\n').find((l) => l.startsWith('data:'))
      if (line) onEvent(JSON.parse(line.slice(5)))
    }
  }
}

export const STATUS_LABEL: Record<Status, string> = {
  in_force: '現行有效',
  pending: '尚未施行',
  superseded: '已失效',
}

/** 字元層級 diff（LCS），用於新舊條文對照 */
export function diffChars(a: string, b: string): { t: 'same' | 'add' | 'del'; s: string }[] {
  const n = a.length
  const m = b.length
  if (n * m > 4_000_000) return [{ t: 'del', s: a }, { t: 'add', s: b }]
  const dp = Array.from({ length: n + 1 }, () => new Uint16Array(m + 1))
  for (let i = n - 1; i >= 0; i--)
    for (let j = m - 1; j >= 0; j--) dp[i][j] = a[i] === b[j] ? dp[i + 1][j + 1] + 1 : Math.max(dp[i + 1][j], dp[i][j + 1])
  const out: { t: 'same' | 'add' | 'del'; s: string }[] = []
  const push = (t: 'same' | 'add' | 'del', s: string) => {
    const last = out[out.length - 1]
    if (last && last.t === t) last.s += s
    else out.push({ t, s })
  }
  let i = 0
  let j = 0
  while (i < n && j < m) {
    if (a[i] === b[j]) { push('same', a[i]); i++; j++ }
    else if (dp[i + 1][j] >= dp[i][j + 1]) { push('del', a[i]); i++ }
    else { push('add', b[j]); j++ }
  }
  if (i < n) push('del', a.slice(i))
  if (j < m) push('add', b.slice(j))
  return out
}

/** 「2026/11/6 上午 12:00:00」或 ISO 字串 → 「2026-11-06」 */
export function fmtDate(s: string): string {
  const m = s?.match(/(\d{4})[/-](\d{1,2})[/-](\d{1,2})/)
  return m ? `${m[1]}-${m[2].padStart(2, '0')}-${m[3].padStart(2, '0')}` : s
}
