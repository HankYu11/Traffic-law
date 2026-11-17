# 警政法規智慧助理（實證 Demo）

臺北市政府警察局「運用生成式 AI 與法規知識庫 Chatbot 強化同仁業務指引及案件處理效率」提案用 Demo。
實證範圍：**交通執法＋酒（毒）駕**。資料全部來自公開來源（全國法規資料庫 Open API、分局公告之作業程序）。

## 與一般 RAG Chatbot 的差異

| 功能 | 做法 | 對應需求 |
|---|---|---|
| **法條版本控管** | 切片到「項」，每項帶施行日／失效日。依「查詢基準日」只引用當天有效的版本；修正前後可並列對照、標示差異；已公布未施行的新規定主動提醒 | F3 版本管理 |
| **引用驗證** | 每句附出處 `[S#]`；系統自動檢查出處編號有效、金額／期間／濃度數字確實出現在被引用的條文中；條文原文直接從資料庫帶出，不由模型生成 | F2 附出處、防幻覺 |
| **SOP 流程化** | 作業程序轉成結構化步驟（權責、判斷點、法源、表單），可逐步勾選；SOP 步驟被檢索到時，自動帶入它引用的法源 | F1 下一步處置建議 |
| **法規異動影響分析** | 自動比對 SOP 與現行條文，找出已過時的內容（例如拒測罰鍰 9 萬 → 18 萬）；AI 初判＋法規室複核（確認／修正／排除） | F3、各組室更新責任 |
| **外勤快速查詢** | 不經 AI，條號／關鍵字毫秒級查詢 | F5 |
| **口語 → 法條用語** | 「拒測」「強制抽血」「扣車」等員警用語自動對應到法條用語，對照表由業務單位維護 | 檢索準確度 |
| **查無依據就拒答** | 檢索分數低於門檻直接回答「查無足夠依據」，不交給模型猜 | 正確率 |
| **評估框架** | 50 題題庫（常見／少見／特殊／版本／範圍外），自動算檢索命中率、正確率、引用驗證率，並與「同模型不接知識庫」對照 | KPI 正確率 ≥ 90% |
| **數據紀錄** | 查詢紀錄、耗時、回饋、「轉請法制單位確認」次數（＝人工詢問次數） | F6、F7 |

## 架構

```
全國法規資料庫 Open API（JSON）＋ 舊版條文 ＋ SOP（PDF → 結構化 YAML）
  → ingest/build_corpus.py   依條／項切片＋版本 Metadata（data/processed/chunks.jsonl）
  → backend/app/index.py     BM25（中文 bigram）＋ EmbeddingGemma 向量 ＋ 條號精確比對 → RRF 融合；依基準日過濾
  → backend/app/answer.py    組提示 → LLM 串流生成 → 引用驗證
  → frontend（React）         問答／快速查條文／SOP 檢核表／管理後台
```

- **LLM**：任何 OpenAI 相容端點。開發時用 Gemini API 上的 `gemma-4-26b-a4b-it`；警局內網用 vLLM 在 H200 上跑**同一組開放權重**，只要改 `LLM_BASE_URL`。
  - 26B MoE、每 token 只啟用約 4B 參數，單張 H200 可支撐 50 人同時在線。
- **Embedding**：`google/embeddinggemma-300m`（ONNX，CPU 即可），地端執行，不送外部服務。
- **非陸資**：模型（Google Gemma）、向量模型、斷詞（自寫字元 bigram，不用 jieba）、OCR 都避開陸資元件。

## 快速開始

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cd frontend && npm ci && npm run build && cd ..
export GEMINI_API_KEY=...            # 或設定 LLM_BASE_URL / LLM_API_KEY / LLM_MODEL 指向 vLLM
cd backend && DEMO_TODAY=2026-11-17 ../.venv/bin/uvicorn app.main:app --port 8000
```

打開 http://localhost:8000 。第一次啟動會下載向量模型（約 1.2 GB）；`data/processed/vectors.npz` 已附向量快取，不必重算。

前端開發：`cd frontend && npm run dev`（Vite 會把 `/api` 代理到 8000）。

### Docker

```bash
docker compose up --build app                 # 簡報／開發：LLM 走 Gemini API
docker compose --profile onprem up --build    # 內網：vLLM + H200，完全不對外連線
```

### 更新法規資料

```bash
scripts/rebuild_data.sh     # 下載最新法規 → 重建切片 → 增量計算向量 → SOP 一致性檢查
```

### 跑評估

```bash
.venv/bin/python eval/run_eval.py               # 50 題＋對照組，結果在 eval/reports/latest.{json,md}
.venv/bin/python eval/run_eval.py --only C01,V01 --no-baseline
```

## 簡報 Demo 腳本（約 8 分鐘）

`DEMO_TODAY=2026-11-17`（固定示範日期，確保情境可重現）

1. **一般問答＋引用**：點「機車駕駛人酒測值 0.3 毫克，現場要怎麼處理？罰多少？」
   - 看結論、法律依據（道交條例 §35-1、**刑法 §185-3** 由 SOP 法源自動帶入）、處理步驟、應備文件。
   - 點 `S#` 標籤 → 右側出處亮起；點出處 → 條文全文。
   - 下方綠色「引用驗證通過」。
2. **對照組**：同一題按「對照：不用知識庫直接問模型」→ 模型自己回答時會引用錯條號／過時金額，且無出處。
3. **版本控管**：問「汽車駕駛人酒駕第一次被查獲，罰鍰是多少？」→ 3.6 萬～15 萬（115.08.17 修正版）。
   - 右上「查詢基準日」改成 **2026-10-01** 再問一次 → 3 萬～12 萬（修正前版本），並提醒新規定 10/31 施行。
   - 點第 35 條出處 → 修正前後**並列對照、差異標色**。
4. **尚未施行提醒**：問「酒駕肇事拒絕酒測，警察可以強制抽血嗎？」→ 系統標示第 35 條第 6 項 **115.12.31 才施行**。
5. **SOP 過時偵測**：問「駕駛人拒絕酒測，要告知哪些法律效果？」→ 注意事項出現「SOP 內容待更新」。
   - 開啟「作業程序檢核」→ 拒測步驟：SOP 寫 9 萬元，現行 18 萬元；說明 AI 初判＋法規室複核的流程（包含 AI 判錯被人工修正、被排除的例子）。
6. **外勤快速查詢**：輸入「35條」「拒測」→ 毫秒級結果，不經 AI。
7. **管理後台**：評估報告（正確率、檢索命中率、與對照組比較、逐題結果）、知識庫與版本異動時間軸、使用統計、查詢紀錄。
8. **範圍外**：問「家暴保護令怎麼聲請？」→ 直接回答查無依據，不編造。

## 目錄

```
backend/app/      FastAPI：config, index（檢索）, answer（生成＋驗證）, sop, db, main
frontend/src/     React：Chat, ArticleModal（版本對照）, QuickSearch, SopView, Admin
ingest/           build_corpus, fetch_old_version, sop_check, lawtext
data/corpus.yaml  收錄法規清單與版本異動（施行日可細到「項」）
data/sop/         SOP 結構化（dui.yaml）
data/sop_review.yaml  法規室複核紀錄
data/synonyms.yaml    員警口語 → 法條用語
data/processed/   切片、向量快取、SOP 檢查結果
eval/             題庫、評估腳本、報告
```

## 限制與正式實證時要補的

- SOP 來源為分局公告的 2018 年版，僅作示範；正式版由各組室提供最新 SOP，並由法規室確認。
- 函釋、內部規定、司法院裁判書尚未收錄（裁判書可改為離線定期匯入）。
- 評估題庫為示範用，正式驗收題目由實務員警出題、法規室標註並人工複核評分。
- 版本控管示範於道交條例 115.08.17 修正；全面套用需逐部法規建立沿革資料。
