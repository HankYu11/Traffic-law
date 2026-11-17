#!/usr/bin/env bash
# 重新下載全國法規資料庫並重建知識庫（每週一次；內網改為離線匯入下載好的 zip）。
set -euo pipefail
cd "$(dirname "$0")/.."
PY=${PY:-.venv/bin/python}
mkdir -p data/raw
for kind in law order; do
  for i in 1 2 3 4; do
    curl -sSL -m 600 -o "data/raw/ch_${kind}_json.zip" "https://law.moj.gov.tw/api/ch/${kind}/json" \
      && unzip -tq "data/raw/ch_${kind}_json.zip" && break
    echo "retry ${kind} ($i)"; sleep 5
  done
  unzip -oq "data/raw/ch_${kind}_json.zip" -d data/raw
done
$PY ingest/fetch_old_version.py K0040012
$PY ingest/build_corpus.py
# 向量會在後端啟動時增量計算（只重算有變動的片段）
$PY -c "import sys; sys.path.insert(0,'backend'); from app.index import INDEX; INDEX.load(); print(INDEX.embed_stats)"
# SOP 與現行法規一致性檢查（結果需法規室複核：data/sop_review.yaml）
$PY ingest/sop_check.py
