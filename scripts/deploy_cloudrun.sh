#!/usr/bin/env bash
# 部署到 Google Cloud Run（台灣 asia-east1），產生可分享的網址。
#
# 用法（在你自己的電腦上，已安裝 gcloud 並登入）：
#   gcloud auth login
#   scripts/deploy_cloudrun.sh <GCP 專案 ID>
#
# 這支腳本會：
#   1. 啟用 Cloud Run / Cloud Build / Artifact Registry / Secret Manager
#   2. 把 Gemini API 金鑰與存取碼存進 Secret Manager（不會寫進程式碼或映像檔）
#   3. 用 Dockerfile 在 Cloud Build 建置並部署
#   4. 預先回答幾題示範問題（存進快取，簡報時秒回）
#
# 成本控制：
#   - min-instances=0：沒人用時不收運算費；max-instances=1：最多一台，費用有上限
#   - DAILY_LLM_LIMIT：每天最多呼叫模型幾次（預設 300），超過就只能用「快速查條文」
#   - 存取碼：沒有存取碼的人無法呼叫模型
set -euo pipefail
cd "$(dirname "$0")/.."

PROJECT="${1:?請給 GCP 專案 ID，例如 scripts/deploy_cloudrun.sh my-project}"
REGION="${REGION:-asia-east1}"
SERVICE="${SERVICE:-police-law-demo}"
DAILY_LLM_LIMIT="${DAILY_LLM_LIMIT:-300}"
DEMO_TODAY="${DEMO_TODAY:-2026-11-17}"

gcloud config set project "$PROJECT" >/dev/null
echo "▶ 啟用必要服務…"
gcloud services enable run.googleapis.com cloudbuild.googleapis.com artifactregistry.googleapis.com \
  secretmanager.googleapis.com >/dev/null

ensure_secret() {  # $1=name  $2=value
  if gcloud secrets describe "$1" >/dev/null 2>&1; then
    printf '%s' "$2" | gcloud secrets versions add "$1" --data-file=- >/dev/null
  else
    printf '%s' "$2" | gcloud secrets create "$1" --replication-policy=automatic --data-file=- >/dev/null
  fi
}

if ! gcloud secrets describe gemini-api-key >/dev/null 2>&1 || [ "${UPDATE_KEY:-}" = 1 ]; then
  read -r -s -p "貼上 Gemini API 金鑰（輸入時不會顯示）: " KEY; echo
  ensure_secret gemini-api-key "$KEY"
fi
if ! gcloud secrets describe demo-access-code >/dev/null 2>&1 || [ -n "${ACCESS_CODE:-}" ]; then
  CODE="${ACCESS_CODE:-$(LC_ALL=C tr -dc 'a-z0-9' </dev/urandom | head -c 8)}"
  ensure_secret demo-access-code "$CODE"
  echo "▶ 存取碼：$CODE（分享網址時一併提供；之後可用 ACCESS_CODE=新碼 重新執行本腳本更換）"
fi

# Cloud Run 的服務帳號要能讀這兩個 secret
PROJECT_NUMBER=$(gcloud projects describe "$PROJECT" --format='value(projectNumber)')
SA="${PROJECT_NUMBER}-compute@developer.gserviceaccount.com"
for s in gemini-api-key demo-access-code; do
  gcloud secrets add-iam-policy-binding "$s" --member="serviceAccount:$SA" \
    --role=roles/secretmanager.secretAccessor >/dev/null
done

echo "▶ 建置並部署（第一次約 8–12 分鐘）…"
gcloud run deploy "$SERVICE" \
  --source . \
  --region "$REGION" \
  --port 8000 \
  --memory 2Gi --cpu 2 --cpu-boost \
  --min-instances 0 --max-instances 1 --concurrency 20 \
  --timeout 300 \
  --allow-unauthenticated \
  --set-secrets "LLM_API_KEY=gemini-api-key:latest,ACCESS_CODE=demo-access-code:latest" \
  --set-env-vars "LLM_MODEL=gemma-4-26b-a4b-it,DEMO_TODAY=${DEMO_TODAY},DAILY_LLM_LIMIT=${DAILY_LLM_LIMIT},HF_HUB_OFFLINE=1,DB_PATH=/tmp/app.db"

URL=$(gcloud run services describe "$SERVICE" --region "$REGION" --format='value(status.url)')
echo "▶ 部署完成：$URL"

if [ "${WARM:-1}" = 1 ]; then
  CODE=$(gcloud secrets versions access latest --secret=demo-access-code)
  echo "▶ 預熱示範問題（每題呼叫一次模型，之後走快取）…"
  scripts/warm_cache.sh "$URL" "$CODE" || echo "（預熱失敗不影響服務，可稍後再執行 scripts/warm_cache.sh）"
fi
echo
echo "分享網址：$URL"
echo "存取碼：  $(gcloud secrets versions access latest --secret=demo-access-code)"
