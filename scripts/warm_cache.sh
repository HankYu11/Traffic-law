#!/usr/bin/env bash
# 預先回答簡報會用到的問題，存進答案快取（之後同一題秒回，不再呼叫模型）。
# 用法：scripts/warm_cache.sh <網址> [存取碼]
set -uo pipefail
URL="${1:?網址}"; CODE="${2:-}"
ask() {  # $1=question  $2=as_of(optional)
  local body
  body=$(python3 -c 'import json,sys; q,a=sys.argv[1],sys.argv[2]; print(json.dumps({"question":q,**({"as_of":a} if a else {})}, ensure_ascii=False))' "$1" "${2:-}")
  curl -sS -N -m 240 -X POST "$URL/api/ask" -H 'Content-Type: application/json' -H "X-Access-Code: $CODE" \
    --data-binary "$body" | grep -q '"type": "done"' && echo "  ✓ $1 ${2:-}" || echo "  ✗ $1 ${2:-}"
}
ask "機車駕駛人酒測值 0.3 毫克，現場要怎麼處理？罰多少？"
ask "駕駛人拒絕酒測，要告知哪些法律效果？"
ask "汽車駕駛人酒駕第一次被查獲，罰鍰是多少？"
ask "汽車駕駛人酒駕第一次被查獲，罰鍰是多少？" "2026-10-01"
ask "實施酒測前要先問什麼？可以讓他漱口嗎？"
ask "行經設有告示的酒測站不停車就跑了，要怎麼舉發？"
ask "酒精濃度超過標準但只超過一點點，可以勸導不舉發嗎？"
ask "酒駕肇事駕駛人拒絕酒測，警察可以強制抽血嗎？"
ask "家暴保護令怎麼聲請？"
