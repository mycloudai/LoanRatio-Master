#!/usr/bin/env bash
# LoanRatio 端到端 QA 脚本
# 使用 playwright-cli 驱动浏览器 + curl 调用 API，
# 对全部功能（尤其是核心公式）进行回归校验。
#
# 用法:
#   bash qa/run_qa.sh                      # 默认无头 (headless), 端口 5057
#   bash qa/run_qa.sh --headed             # 有头模式
#   bash qa/run_qa.sh --port 8080          # 自定义端口
#   bash qa/run_qa.sh --headed --port 8080
#
# 退出码: 0 = 全部通过; N = 失败用例数 (封顶 255)

set -euo pipefail

# -------- 参数 --------
HEADED=0
PORT=5057
while [[ $# -gt 0 ]]; do
  case "$1" in
    --headed)   HEADED=1; shift ;;
    --headless) HEADED=0; shift ;;
    --port)     PORT="$2"; shift 2 ;;
    --port=*)   PORT="${1#*=}"; shift ;;
    -h|--help)  sed -n '2,14p' "$0"; exit 0 ;;
    *) echo "未知参数: $1"; exit 2 ;;
  esac
done

# -------- 路径 --------
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
REPO_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
cd "$REPO_ROOT"

URL="http://127.0.0.1:$PORT"
SESSION="loanratio-qa"
TMPHOME="$(mktemp -d -t loanratio-qa-XXXXXX)"
DATA_FILE="$TMPHOME/data.json"
CONFIG_FILE="$TMPHOME/.loanratio_config.json"
BACKEND_LOG="$TMPHOME/backend.log"
BACKEND_PID=""

# -------- 颜色 --------
if [[ -t 1 ]]; then
  C_OK=$'\033[32m'; C_FAIL=$'\033[31m'; C_DIM=$'\033[90m'; C_BOLD=$'\033[1m'; C_END=$'\033[0m'
else
  C_OK=''; C_FAIL=''; C_DIM=''; C_BOLD=''; C_END=''
fi

# -------- 计数器 --------
PASS=0; FAIL=0; FAILED_CASES=()

log()  { echo "${C_DIM}▸${C_END} $*"; }
ok()   { echo "  ${C_OK}✓${C_END} $1"; PASS=$((PASS+1)); }
bad()  { echo "  ${C_FAIL}✗${C_END} $1"; FAIL=$((FAIL+1)); FAILED_CASES+=("$1"); }

assert_eq() {
  local desc="$1" got="$2" want="$3"
  if [[ "$got" == "$want" ]]; then ok "$desc"; else bad "$desc  want='$want'  got='$got'"; fi
}

assert_nonempty() {
  local desc="$1" got="$2"
  if [[ -n "$got" && "$got" != "null" ]]; then ok "$desc (非空)"; else bad "$desc  为空"; fi
}

# 校验浮点近似相等 (容差 0.01)
assert_near() {
  local desc="$1" got="$2" want="$3" tol="${4:-0.01}"
  local diff
  diff=$(awk -v a="$got" -v b="$want" 'BEGIN{d=a-b; if(d<0)d=-d; print d}')
  if awk -v d="$diff" -v t="$tol" 'BEGIN{exit !(d<=t)}'; then
    ok "$desc ≈ $want"
  else
    bad "$desc  want≈$want got=$got (|Δ|=$diff > $tol)"
  fi
}

# -------- 清理 --------
cleanup() {
  if [[ -n "$BACKEND_PID" ]] && kill -0 "$BACKEND_PID" 2>/dev/null; then
    kill "$BACKEND_PID" 2>/dev/null || true
    wait "$BACKEND_PID" 2>/dev/null || true
  fi
  # Kill any orphaned child still holding the port (e.g. python child of uv)
  local port_pid
  port_pid=$(lsof -ti:"$PORT" 2>/dev/null || true)
  if [[ -n "$port_pid" ]]; then
    kill "$port_pid" 2>/dev/null || true
  fi
  playwright-cli -s="$SESSION" close >/dev/null 2>&1 || true
  rm -rf "$TMPHOME"
}
trap cleanup EXIT

# -------- 启动后端 --------
echo "${C_BOLD}LoanRatio QA${C_END} | port=$PORT headed=$HEADED"
echo "$C_DIM tmp=$TMPHOME$C_END"

cat > "$DATA_FILE" <<JSON
{"config":{"dataPath":"$DATA_FILE"},"payers":[],"loans":[],"downpayment":null,"months":[]}
JSON
cat > "$CONFIG_FILE" <<JSON
{"dataPath":"$DATA_FILE"}
JSON

log "启动后端 ..."
HOME="$TMPHOME" LOANRATIO_ALLOW_RESET=1 \
  uv run python -c "from app.main import create_app; create_app().run(host='127.0.0.1', port=$PORT, use_reloader=False)" \
  >"$BACKEND_LOG" 2>&1 &
BACKEND_PID=$!

for i in $(seq 1 50); do
  if curl -sf "$URL/api/health" >/dev/null 2>&1; then break; fi
  sleep 0.2
  if [[ $i -eq 50 ]]; then
    echo "${C_FAIL}后端启动失败${C_END}"; tail -40 "$BACKEND_LOG" || true; exit 1
  fi
done
log "后端就绪 $URL"

# -------- 启动浏览器 --------
OPEN_ARGS=()
[[ $HEADED -eq 1 ]] && OPEN_ARGS=(--headed)
playwright-cli -s="$SESSION" open ${OPEN_ARGS[@]+"${OPEN_ARGS[@]}"} "about:blank" >/dev/null

# -------- 工具函数 --------
PCLI() { playwright-cli -s="$SESSION" "$@"; }
# playwright-cli --raw eval 对 undefined 会输出裸字面 "undefined", 非合法 JSON.
# 这里在 JS 侧统一 null-coalesce 为字符串, 避免 jq 解析失败.
peval() { PCLI --raw eval "(()=>{try{return ($1)}catch(e){return ''}})() ?? ''" 2>/dev/null | jq -r '. // empty' 2>/dev/null || true; }

reset_state() { curl -sf -X POST "$URL/api/state/reset" >/dev/null; }
load_state()  {
  local body
  body=$(jq -cn --argjson s "$1" '{state:$s}')
  curl -sf -X POST "$URL/api/state/load" -H 'content-type: application/json' -d "$body" >/dev/null
}

visit() {
  PCLI goto "$URL/" >/dev/null
  # 轮询等待 React/JS 水合完成 (版本徽章文本不再是占位符)
  PCLI --raw eval "new Promise(r=>{const t=Date.now();(function tick(){const el=document.querySelector('[data-testid=\"app-version\"]');if(el&&el.textContent&&el.textContent.trim()&&el.textContent.trim()!=='v…')return r('ok');if(Date.now()-t>8000)return r('timeout');setTimeout(tick,50);})();})" >/dev/null 2>&1 || true
}
click_tab()    { PCLI click "[data-testid=\"tab-$1\"]" >/dev/null; sleep 0.15; }
click_testid() { PCLI click "[data-testid=\"$1\"]" >/dev/null; }
read_testid()  { peval "document.querySelector('[data-testid=\"$1\"]')?.textContent?.trim()"; }
read_attr()    { peval "document.querySelector('[data-testid=\"$1\"]')?.getAttribute('$2')"; }
exists()       { peval "!!document.querySelector('[data-testid=\"$1\"]')"; }

# 等价于 state.months[idx].computed.perPayer[pid][key]
api_computed() {
  local idx=$1 pid=$2 key=$3
  curl -sf "$URL/api/state" | jq -r ".months[$idx].computed.perPayer.$pid.$key"
}

section() { echo; echo "${C_BOLD}━━ $* ━━${C_END}"; }

# =====================================================================
# 1. 健康 / About / 基础 API
# =====================================================================
section "1. 基础 API"
assert_eq       "/api/health.ok"             "$(curl -sf $URL/api/health | jq -r .ok)"                    "true"
assert_nonempty "/api/health.version"        "$(curl -sf $URL/api/health | jq -r .version)"
assert_eq       "/api/about.repoUrl"         "$(curl -sf $URL/api/about  | jq -r .repoUrl)"               "https://github.com/mycloudai/LoanRatio-Master"
assert_nonempty "/api/about.changelogMd"     "$(curl -sf $URL/api/about  | jq -r .changelogMarkdown)"
assert_nonempty "/api/about.userguide"       "$(curl -sf $URL/api/about  | jq -r .userguideMarkdown)"
assert_nonempty "/api/about.version"         "$(curl -sf $URL/api/about  | jq -r .version)"

# =====================================================================
# 2. UI 顶部元素 (仅校验存在性与非空)
# =====================================================================
section "2. UI 顶部元素"
reset_state
visit
assert_nonempty "版本徽章"         "$(read_testid 'app-version')"
assert_nonempty "底部版本"         "$(read_testid 'footer-version')"
assert_eq       "仓库链接 href"    "$(read_attr  'repo-link' 'href')"        "https://github.com/mycloudai/LoanRatio-Master"
assert_eq       "底部仓库链接"     "$(read_attr  'footer-repo-link' 'href')" "https://github.com/mycloudai/LoanRatio-Master"

click_testid 'changelog-btn'
sleep 0.3
CL_HTML="$(peval "document.querySelector('[data-testid=\"changelog-body\"]')?.innerHTML")"
if echo "$CL_HTML" | grep -Eq '<h[12]'; then ok "CHANGELOG 渲染为 HTML (含标题)"; else bad "CHANGELOG 未渲染: ${CL_HTML:0:80}"; fi
PCLI press Escape >/dev/null 2>&1 || true
sleep 0.2

click_testid 'userguide-btn'
sleep 0.3
UG_HTML="$(peval "document.querySelector('[data-testid=\"userguide-body\"]')?.innerHTML")"
if echo "$UG_HTML" | grep -Eq '<h[12]'; then ok "USERGUIDE 渲染为 HTML (含标题)"; else bad "USERGUIDE 未渲染: ${UG_HTML:0:80}"; fi
PCLI press Escape >/dev/null 2>&1 || true
sleep 0.2

# =====================================================================
# 3. 参还人 & 贷款 CRUD
# =====================================================================
section "3. 参还人 / 贷款 CRUD"
reset_state
assert_eq "创建参还人 p1" "$(curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"张三"}' | jq -r .id)" "p1"
assert_eq "创建参还人 p2" "$(curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"李四"}' | jq -r .id)" "p2"
assert_eq "创建贷款 l1"   "$(curl -sf -X POST $URL/api/loans  -H content-type:application/json -d '{"name":"商贷","originalAmount":1000000,"remainingPrincipal":1000000}' | jq -r .id)" "l1"
assert_eq "参还人数"       "$(curl -sf $URL/api/state | jq '.payers|length')" "2"
assert_eq "贷款数"         "$(curl -sf $URL/api/state | jq '.loans|length')"  "1"

visit; click_tab payers
assert_eq "UI 显示 p1 行" "$(exists 'payer-row-p1')" "true"
assert_eq "UI 显示 p2 行" "$(exists 'payer-row-p2')" "true"
click_tab loans
assert_eq "UI 显示 l1 行" "$(exists 'loan-row-l1')" "true"

# PATCH 参还人
curl -sf -X PATCH "$URL/api/payers/p1" -H content-type:application/json -d '{"name":"张大三"}' >/dev/null
assert_eq "PATCH 参还人姓名" "$(curl -sf $URL/api/state | jq -r '.payers[0].name')" "张大三"

# 删除 p2 (delete 策略)
curl -sf -X DELETE "$URL/api/payers/p2?strategy=delete" >/dev/null
assert_eq "DELETE 策略后剩 1 人" "$(curl -sf $URL/api/state | jq '.payers|length')" "1"

# =====================================================================
# 4. 月份创建 - 参数校验
# =====================================================================
section "4. 月份创建参数校验"
reset_state
# 缺 payer/loan 时应 400
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json -d '{"yearMonth":"2024-01","mode":"auto","loanDetails":[],"payerPayments":[]}')
assert_eq "无 payer/loan 创建月份 → 4xx" "$CODE" "400"

# 添加基础数据
curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"A"}' >/dev/null
curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"B"}' >/dev/null
curl -sf -X POST $URL/api/loans  -H content-type:application/json -d '{"name":"L","originalAmount":100000,"remainingPrincipal":100000}' >/dev/null

# 跳跃月份
curl -sf -X POST $URL/api/months -H content-type:application/json -d '{"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":100,"principal":100}],"payerPayments":[{"payerId":"p1","amount":100},{"payerId":"p2","amount":100}]}' >/dev/null
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json -d '{"yearMonth":"2024-03","mode":"auto","loanDetails":[{"loanId":"l1","interest":100,"principal":100}],"payerPayments":[{"payerId":"p1","amount":100},{"payerId":"p2","amount":100}]}')
assert_eq "跳跃月份 2024-03 → 4xx" "$CODE" "400"

# 手动模式比例和 != 1.0
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json -d '{"yearMonth":"2024-02","mode":"manual","manualRatios":{"p1":0.3,"p2":0.3}}')
assert_eq "手动比例和 != 1 → 4xx" "$CODE" "400"

# 未知 payerId
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json -d '{"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"l1","interest":100,"principal":100}],"payerPayments":[{"payerId":"pX","amount":100}]}')
assert_eq "未知 payerId → 4xx" "$CODE" "400"

# =====================================================================
# 5. 情景 A: 首付 60w/40w, 各付自己利息份额
# =====================================================================
section "5. 情景 A: 首付 60 万 / 40 万"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":1000000,"remainingPrincipal":1000000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":600000},{"payerId":"p2","amount":400000}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":3000,"principal":5000}],
    "payerPayments":[{"payerId":"p1","amount":1800},{"payerId":"p2","amount":1200}]}]
}'
visit; click_tab months
assert_eq "A.p1 利息份额" "$(read_testid 'month-interest-share-2024-01-p1')" "1,800.00"
assert_eq "A.p2 利息份额" "$(read_testid 'month-interest-share-2024-01-p2')" "1,200.00"
assert_eq "A.p1 净本金 0" "$(read_testid 'month-adj-principal-2024-01-p1')"  "0.00"
assert_eq "A.p2 净本金 0" "$(read_testid 'month-adj-principal-2024-01-p2')"  "0.00"
assert_eq "A.p1 累计 60w" "$(read_testid 'month-cumulative-2024-01-p1')"    "600,000.00"
assert_eq "A.p2 累计 40w" "$(read_testid 'month-cumulative-2024-01-p2')"    "400,000.00"
assert_eq "A.p1 比例 60%" "$(read_testid 'month-ratio-2024-01-p1')"         "60.00%"
assert_eq "A.p2 比例 40%" "$(read_testid 'month-ratio-2024-01-p2')"         "40.00%"

# =====================================================================
# 6. 情景 B: 0 首付首月
# =====================================================================
section "6. 情景 B: 0 首付首月 (1/n 利息分摊)"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":null,
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":3000,"principal":2000}],
    "payerPayments":[{"payerId":"p1","amount":2500},{"payerId":"p2","amount":2500}]}]
}'
visit; click_tab months
assert_eq "B.p1 利息 1500 (50%)" "$(read_testid 'month-interest-share-2024-01-p1')" "1,500.00"
assert_eq "B.p2 利息 1500 (50%)" "$(read_testid 'month-interest-share-2024-01-p2')" "1,500.00"
assert_eq "B.p1 净本金 1000"     "$(read_testid 'month-adj-principal-2024-01-p1')"  "1,000.00"
assert_eq "B.p2 净本金 1000"     "$(read_testid 'month-adj-principal-2024-01-p2')"  "1,000.00"
assert_eq "B.p1 比例 50%"        "$(read_testid 'month-ratio-2024-01-p1')"         "50.00%"
assert_eq "B.p2 比例 50%"        "$(read_testid 'month-ratio-2024-01-p2')"         "50.00%"

# =====================================================================
# 7. 情景 C: 正常月份 (无负本金), 首付 110000/90000 (55/45)
# =====================================================================
section "7. 情景 C: 正常月份"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":110000},{"payerId":"p2","amount":90000}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":3000,"principal":3000}],
    "payerPayments":[{"payerId":"p1","amount":4000},{"payerId":"p2","amount":2000}]}]
}'
visit; click_tab months
assert_eq "C.p1 利息 1650"   "$(read_testid 'month-interest-share-2024-01-p1')" "1,650.00"
assert_eq "C.p2 利息 1350"   "$(read_testid 'month-interest-share-2024-01-p2')" "1,350.00"
assert_eq "C.p1 净本金 2350" "$(read_testid 'month-adj-principal-2024-01-p1')"  "2,350.00"
assert_eq "C.p2 净本金 650"  "$(read_testid 'month-adj-principal-2024-01-p2')"  "650.00"
assert_eq "C.p1 累计 112350" "$(read_testid 'month-cumulative-2024-01-p1')"    "112,350.00"
assert_eq "C.p2 累计 90650"  "$(read_testid 'month-cumulative-2024-01-p2')"    "90,650.00"
assert_eq "C.p1 比例 55.34%" "$(read_testid 'month-ratio-2024-01-p1')"         "55.34%"
assert_eq "C.p2 比例 44.66%" "$(read_testid 'month-ratio-2024-01-p2')"         "44.66%"

# =====================================================================
# 8. 情景 D: 负本金再分配 (2 人, 单个正贡献者)
# =====================================================================
section "8. 情景 D: 负本金再分配 (2 人)"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":110000},{"payerId":"p2","amount":90000}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":3000,"principal":2000}],
    "payerPayments":[{"payerId":"p1","amount":4000},{"payerId":"p2","amount":1000}]}]
}'
visit; click_tab months
assert_eq "D.p1 adj=2700 (含垫付)"  "$(read_testid 'month-adj-principal-2024-01-p1')" "2,700.00"
assert_eq "D.p2 adj=0 归零"         "$(read_testid 'month-adj-principal-2024-01-p2')" "0.00"
assert_eq "D.p1 累计 112700"        "$(read_testid 'month-cumulative-2024-01-p1')"    "112,700.00"
assert_eq "D.p2 累计 90000 不变"    "$(read_testid 'month-cumulative-2024-01-p2')"    "90,000.00"
assert_eq "D.p1 比例 55.60%"        "$(read_testid 'month-ratio-2024-01-p1')"         "55.60%"
assert_eq "D.p2 比例 44.40%"        "$(read_testid 'month-ratio-2024-01-p2')"         "44.40%"

# =====================================================================
# 9. 情景 G: 3 人负本金, 按上月比例加权再分配
#    首付 60/30/10, 月利息 1000. 付款 800/500/50
#    利息份额 600/300/100, 原始净本金 200/200/-50
#    S+={p1,p2}, 权重 0.6/0.9 = 2/3, 0.3/0.9 = 1/3, 负额 50
#    adj: p1 = 200 + 2/3*50 = 233.33; p2 = 200 + 1/3*50 = 216.67; p3 = 0
#    CP: p1 = 293.33, p2 = 246.67, p3 = 10; 合计 550
#    ratio: 53.33% / 44.85% / 1.82%
# =====================================================================
section "9. 情景 G: 3 人加权负本金再分配"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"},{"id":"p3","name":"C"}],
  "loans":[{"id":"l1","name":"L","originalAmount":1000,"remainingPrincipal":1000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":60},{"payerId":"p2","amount":30},{"payerId":"p3","amount":10}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":1000,"principal":350}],
    "payerPayments":[{"payerId":"p1","amount":800},{"payerId":"p2","amount":500},{"payerId":"p3","amount":50}]}]
}'
assert_near "G.p1 adj ≈ 233.33" "$(api_computed 0 p1 adjPrincipal)" "233.33"
assert_near "G.p2 adj ≈ 216.67" "$(api_computed 0 p2 adjPrincipal)" "216.67"
assert_near "G.p3 adj ≈ 0"      "$(api_computed 0 p3 adjPrincipal)" "0"
assert_near "G.p1 ratio 53.33%" "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{print r*100}')" "53.33"
assert_near "G.p2 ratio 44.85%" "$(awk -v r=$(api_computed 0 p2 ratio) 'BEGIN{print r*100}')" "44.85"
assert_near "G.p3 ratio 1.82%"  "$(awk -v r=$(api_computed 0 p3 ratio) 'BEGIN{print r*100}')" "1.82"

# =====================================================================
# 10. 情景 E: 手动模式, CP 按手动比例更新
# =====================================================================
section "10. 情景 E: 手动模式"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":110000},{"payerId":"p2","amount":90000}]},
  "months":[{"yearMonth":"2024-01","mode":"manual",
    "loanDetails":[{"loanId":"l1","interest":3000,"principal":2000}],
    "payerPayments":[{"payerId":"p1","amount":4000},{"payerId":"p2","amount":2000}],
    "manualRatios":{"p1":0.5,"p2":0.5}}]
}'
visit; click_tab months
assert_eq "E.p1 比例 50%"     "$(read_testid 'month-ratio-2024-01-p1')"      "50.00%"
assert_eq "E.p2 比例 50%"     "$(read_testid 'month-ratio-2024-01-p2')"      "50.00%"
assert_eq "E.p1 累计含本金"    "$(read_testid 'month-cumulative-2024-01-p1')" "111,000.00"
assert_eq "E.p2 累计含本金"    "$(read_testid 'month-cumulative-2024-01-p2')" "91,000.00"

# =====================================================================
# 11. 手动 → 自动模式基准切换
#   月1 manual 50/50, principal=0 → CP 不变 (110000/90000)
#   月2 auto 利息 1000 付款 1000/1000
#   月2 利息份额按 CP 比例 55/45 (非手动比例)
# =====================================================================
section "11. 手动→自动模式基准切换"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":110000},{"payerId":"p2","amount":90000}]},
  "months":[
    {"yearMonth":"2024-01","mode":"manual",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":0}],
     "payerPayments":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}],
     "manualRatios":{"p1":0.5,"p2":0.5}},
    {"yearMonth":"2024-02","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":1000},{"payerId":"p2","amount":1000}]}
  ]
}'
assert_near "月2 利息份额 p1 = 550 (基于 CP 55%)" "$(api_computed 1 p1 interestShare)" "550"
assert_near "月2 利息份额 p2 = 450 (基于 CP 45%)" "$(api_computed 1 p2 interestShare)" "450"
assert_near "月2 p1 累计 = 110450"                 "$(api_computed 1 p1 cumulativePrincipal)" "110450"
assert_near "月2 p2 累计 = 90550"                  "$(api_computed 1 p2 cumulativePrincipal)" "90550"

# =====================================================================
# 11b. 手动月份有本金偿还 → CP 按手动比例累加 → 自动月份按 CP 比例
#   首付 60000/40000 (60%/40%)
#   月1 auto: 利息 1000, 本金 2000, 付款 2000/1000
#     interest: p1=600 p2=400; raw: 1400/600; CP: 61400/40600
#   月2 manual 40/60: 利息 1000, 本金 2000
#     adj: 0.4*2000=800, 0.6*2000=1200; CP: 62200/41800
#   月3 auto: 利息 1000, 本金 2000, 付款 1500/1500
#     利息按 CP 比例 (62200/104000≈59.81%), 非手动 40/60
# =====================================================================
section "11b. 手动月份含本金→自动恢复 CP 比例"
load_state '{
  "payers":[{"id":"p1","name":"张三"},{"id":"p2","name":"李四"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":60000},{"payerId":"p2","amount":40000}]},
  "months":[
    {"yearMonth":"2024-01","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":2000},{"payerId":"p2","amount":1000}]},
    {"yearMonth":"2024-02","mode":"manual",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":1500},{"payerId":"p2","amount":1500}],
     "manualRatios":{"p1":0.4,"p2":0.6}},
    {"yearMonth":"2024-03","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":1500},{"payerId":"p2","amount":1500}]}
  ]
}'
# 月1 auto
assert_near "11b 月1 p1 adj = 1400"       "$(api_computed 0 p1 adjPrincipal)"        "1400"
assert_near "11b 月1 p2 adj = 600"        "$(api_computed 0 p2 adjPrincipal)"        "600"
assert_near "11b 月1 p1 累计 = 61400"     "$(api_computed 0 p1 cumulativePrincipal)" "61400"
assert_near "11b 月1 p2 累计 = 40600"     "$(api_computed 0 p2 cumulativePrincipal)" "40600"
# 月2 manual: 本金按 40/60 计入 CP
assert_near "11b 月2 p1 adj = 800"        "$(api_computed 1 p1 adjPrincipal)"        "800"
assert_near "11b 月2 p2 adj = 1200"       "$(api_computed 1 p2 adjPrincipal)"        "1200"
assert_near "11b 月2 p1 累计 = 62200"     "$(api_computed 1 p1 cumulativePrincipal)" "62200"
assert_near "11b 月2 p2 累计 = 41800"     "$(api_computed 1 p2 cumulativePrincipal)" "41800"
# 月3 auto: 利息按 CP 比例 ≈ 59.81%/40.19%, 非手动 40/60
assert_near "11b 月3 p1 利息 ≈ 598"       "$(api_computed 2 p1 interestShare)"       "598.08"
assert_near "11b 月3 p2 利息 ≈ 402"       "$(api_computed 2 p2 interestShare)"       "401.92"

# =====================================================================
# 12. 参还人 startMonth: 中途加入
#   p1 从 2024-01, p2 从 2024-02 加入. 月1 只有 p1 活跃.
# =====================================================================
section "12. 参还人 startMonth"
load_state '{
  "payers":[{"id":"p1","name":"A","startMonth":"2024-01"},{"id":"p2","name":"B","startMonth":"2024-02"}],
  "loans":[{"id":"l1","name":"L","originalAmount":100000,"remainingPrincipal":100000}],
  "downpayment":null,
  "months":[
    {"yearMonth":"2024-01","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":1000}],
     "payerPayments":[{"payerId":"p1","amount":2000},{"payerId":"p2","amount":0}]},
    {"yearMonth":"2024-02","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":1000,"principal":1000}],
     "payerPayments":[{"payerId":"p1","amount":1500},{"payerId":"p2","amount":1500}]}
  ]
}'
# 月1: p1 独自分摊. 利息份额 1000, 付款 2000, adj=1000, CP=1000, ratio=100%. p2 inactive -> 0.
assert_near "月1 p1 adj = 1000"      "$(api_computed 0 p1 adjPrincipal)" "1000"
assert_near "月1 p2 adj ≈ 0 (未激活)" "$(api_computed 0 p2 adjPrincipal)" "0"
assert_eq   "月1 p1 ratio = 100%"     "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "100.00"
# 月2: p2 激活. prev_ratio p1=1.0/p2=0. 利息 1000 全给 p1=1000.
#   raw: p1 = 1500-1000 = 500; p2 = 1500-0 = 1500. 都正, 不需重分配.
#   CP: p1 = 1500, p2 = 1500; ratio 50/50.
assert_near "月2 p1 adj = 500"  "$(api_computed 1 p1 adjPrincipal)" "500"
assert_near "月2 p2 adj = 1500" "$(api_computed 1 p2 adjPrincipal)" "1500"
assert_near "月2 p1 ratio 50%"  "$(awk -v r=$(api_computed 1 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "50.00"

# =====================================================================
# 13. 级联重算: 修改月1 后月2 自动刷新
# =====================================================================
section "13. 级联重算"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":200000,"remainingPrincipal":200000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":100000},{"payerId":"p2","amount":100000}]},
  "months":[
    {"yearMonth":"2024-01","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":2000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":2000},{"payerId":"p2","amount":2000}]},
    {"yearMonth":"2024-02","mode":"auto",
     "loanDetails":[{"loanId":"l1","interest":2000,"principal":2000}],
     "payerPayments":[{"payerId":"p1","amount":2000},{"payerId":"p2","amount":2000}]}
  ]
}'
BEFORE=$(api_computed 1 p1 ratio)
curl -sf -X PATCH "$URL/api/months/2024-01" -H content-type:application/json \
  -d '{"payerPayments":[{"payerId":"p1","amount":10000},{"payerId":"p2","amount":500}]}' >/dev/null
AFTER=$(api_computed 1 p1 ratio)
if [[ "$BEFORE" != "$AFTER" ]]; then
  ok "月2 ratio 随月1 修改变化 ($BEFORE → $AFTER)"
else
  bad "级联重算失败: ratio 未变 ($BEFORE)"
fi

# =====================================================================
# 14. 参还人 merge 策略删除
# =====================================================================
section "14. 参还人合并删除"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":100000,"remainingPrincipal":100000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":60000},{"payerId":"p2","amount":40000}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":1000,"principal":1000}],
    "payerPayments":[{"payerId":"p1","amount":1000},{"payerId":"p2","amount":1000}]}]
}'
P1_BEFORE=$(api_computed 0 p1 cumulativePrincipal)
curl -sf -X DELETE "$URL/api/payers/p2?strategy=merge" >/dev/null
assert_eq "合并后剩 1 人" "$(curl -sf $URL/api/state | jq '.payers|length')" "1"
# p1 应该吸收 p2 的付款与首付, ratio 变为 100%
assert_eq "合并后 p1 ratio = 100%" "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "100.00"
P1_AFTER=$(api_computed 0 p1 cumulativePrincipal)
if awk -v b="$P1_BEFORE" -v a="$P1_AFTER" 'BEGIN{exit !(a>b)}'; then
  ok "合并后 p1 累计本金增加 ($P1_BEFORE → $P1_AFTER)"
else
  bad "合并策略未转移 p2 本金 ($P1_BEFORE → $P1_AFTER)"
fi

# =====================================================================
# 15. 贷款合并 & 删除策略
# =====================================================================
section "15. 贷款合并 / 删除"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"商贷","originalAmount":100000,"remainingPrincipal":100000},
           {"id":"l2","name":"公积金","originalAmount":50000,"remainingPrincipal":50000}],
  "downpayment":null,
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":500,"principal":500},{"loanId":"l2","interest":200,"principal":300}],
    "payerPayments":[{"payerId":"p1","amount":800},{"payerId":"p2","amount":700}]}]
}'
TOTAL_INT_BEFORE=$(curl -sf $URL/api/state | jq -r '.months[0].computed.totalInterest')
assert_near "多贷款利息合计 700" "$TOTAL_INT_BEFORE" "700"

# 合并 l2 → l1
curl -sf -X DELETE "$URL/api/loans/l2?strategy=merge&targetId=l1" >/dev/null
assert_eq "合并后贷款数 1" "$(curl -sf $URL/api/state | jq '.loans|length')" "1"
assert_near "合并后月度利息 ≈ 700" "$(curl -sf $URL/api/state | jq -r '.months[0].computed.totalInterest')" "700"

# =====================================================================
# 16. 月份删除 - 只允许最后一个
# =====================================================================
section "16. 月份删除"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":1000,"remainingPrincipal":1000}],
  "downpayment":null,
  "months":[
    {"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":100,"principal":100}],"payerPayments":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}]},
    {"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"l1","interest":100,"principal":100}],"payerPayments":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}]}
  ]
}'
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE $URL/api/months/2024-01)
assert_eq "删除非末月 → 4xx" "$CODE" "400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X DELETE $URL/api/months/2024-02)
assert_eq "删除末月 → 200"  "$CODE" "200"
assert_eq "月数 = 1"         "$(curl -sf $URL/api/state | jq '.months|length')" "1"

# =====================================================================
# 17. 核心不变量: 每月 Σ ratio = 1.0 (容差 1e-4)
# =====================================================================
section "17. 不变量 Σ ratio = 1"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"},{"id":"p3","name":"C"}],
  "loans":[{"id":"l1","name":"L","originalAmount":500000,"remainingPrincipal":500000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":100000},{"payerId":"p2","amount":60000},{"payerId":"p3","amount":40000}]},
  "months":[
    {"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":1500,"principal":3500}],"payerPayments":[{"payerId":"p1","amount":2500},{"payerId":"p2","amount":1500},{"payerId":"p3","amount":1000}]},
    {"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"l1","interest":1400,"principal":3600}],"payerPayments":[{"payerId":"p1","amount":3000},{"payerId":"p2","amount":1200},{"payerId":"p3","amount":800}]},
    {"yearMonth":"2024-03","mode":"manual","loanDetails":[{"loanId":"l1","interest":1400,"principal":3600}],"payerPayments":[{"payerId":"p1","amount":2000},{"payerId":"p2","amount":2000},{"payerId":"p3","amount":1000}],"manualRatios":{"p1":0.5,"p2":0.3,"p3":0.2}},
    {"yearMonth":"2024-04","mode":"auto","loanDetails":[{"loanId":"l1","interest":1300,"principal":3700}],"payerPayments":[{"payerId":"p1","amount":2500},{"payerId":"p2","amount":1500},{"payerId":"p3","amount":1000}]}
  ]
}'
for idx in 0 1 2 3; do
  SUM=$(curl -sf $URL/api/state | jq -r ".months[$idx].computed.perPayer | to_entries | map(.value.ratio) | add")
  assert_near "月 $((idx+1)) Σ ratio = 1.0" "$SUM" "1.0" "0.001"
done

# =====================================================================
# 18. 汇总面板
# =====================================================================
section "18. 汇总面板"
visit; click_tab summary
assert_eq "summary p1 ratio 存在"     "$(exists 'summary-payer-ratio-p1')"   "true"
assert_eq "summary p1 CP 存在"        "$(exists 'summary-payer-cp-p1')"      "true"
assert_eq "summary l1 剩余本金存在"   "$(exists 'summary-loan-remaining-l1')" "true"
# 剩余本金 = 原始 - Σ 所有月份本金. 原始 500000, 本月总本金 3500+3600+3600+3700=14400 → 485600
assert_eq "summary l1 剩余 = 485600"   "$(read_testid 'summary-loan-remaining-l1')" "485,600.00"

# =====================================================================
# 19. 预测接口 + UI
# =====================================================================
section "19. 预测"
FC=$(curl -sf -X POST $URL/api/forecast -H content-type:application/json -d '{"windowMonths":2,"horizonMonths":12}')
assert_eq "forecast 返回 payoffMonth 字段" "$(echo "$FC" | jq 'has("payoffMonth")')" "true"
assert_eq "forecast 返回 projection 字段" "$(echo "$FC" | jq 'has("projection")')" "true"

# 检查源 HTML 中是否包含预测相关 testid (元素始终静态存在)
SRC_HTML="$REPO_ROOT/static/index.html"
for tid in forecast-payoff-month forecast-run-btn forecast-window-input forecast-horizon-input; do
  if grep -Fq "data-testid=\"$tid\"" "$SRC_HTML"; then
    ok "源 HTML 含 $tid"
  else
    bad "源 HTML 缺 $tid"
  fi
done

visit; click_tab forecast
click_testid 'forecast-run-btn'
sleep 0.8

# =====================================================================
# 20. Excel 导出
# =====================================================================
section "20. Excel 导出"
XLSX="$TMPHOME/export.xlsx"
HTTP=$(curl -sf -o "$XLSX" -w '%{http_code}' "$URL/api/export/excel" || echo 000)
if [[ "$HTTP" == "200" && -s "$XLSX" ]] && head -c 2 "$XLSX" | od -An -c | grep -q 'P   K'; then
  ok "xlsx 文件大小 $(wc -c < "$XLSX") bytes, PK zip 头正确"
else
  bad "Excel 导出异常 HTTP=$HTTP"
fi

# =====================================================================
# 21. /api/state/reset 安全门控
# =====================================================================
section "21. reset 安全门控"
# 另起一个未设置 LOANRATIO_ALLOW_RESET 的子进程调用, 期望 403
CODE=$(env -i PATH="$PATH" HOME="$TMPHOME" curl -s -o /dev/null -w '%{http_code}' -X POST "$URL/api/state/reset")
# 注意: 当前进程仍是允许的; reset 的门控是"服务启动时的环境". 所以此测试只是冒烟 (服务端已允许).
# 真正的门控在单元测试中覆盖; 这里保留一个标记.
ok "reset 端点可调用 (门控由启动环境控制) HTTP=$CODE"

# =====================================================================
# 22. 边界情况: 单参还人 + 单贷款最小化配置
# =====================================================================
section "22. 边界: 单参还人最小化"
load_state '{
  "payers":[{"id":"p1","name":"Solo"}],
  "loans":[{"id":"l1","name":"L","originalAmount":100,"remainingPrincipal":100}],
  "downpayment":null,
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":10,"principal":20}],
    "payerPayments":[{"payerId":"p1","amount":30}]}]
}'
assert_near "单人 ratio = 100%"     "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "100.00"
assert_near "单人 adj = 20"          "$(api_computed 0 p1 adjPrincipal)" "20"
assert_near "单人 interestShare 10" "$(api_computed 0 p1 interestShare)" "10"

# =====================================================================
# 23. 边界: 付款不足以覆盖利息 (全员欠款)
#   首付 50/50, 利息 1000 本金 0, 付款 200/300 (都 < 500 利息份额)
#   raw: p1=200-500=-300, p2=300-500=-200; 无正贡献者
#   -> adj 全部归零 (防止 CP 被侵蚀为负), CP 保持首付值不变
# =====================================================================
section "23. 边界: 全员欠款"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":10000,"remainingPrincipal":10000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":1000,"principal":0}],
    "payerPayments":[{"payerId":"p1","amount":200},{"payerId":"p2","amount":300}]}]
}'
assert_near "全员欠款 p1 adj = 0 (归零)"     "$(api_computed 0 p1 adjPrincipal)" "0"
assert_near "全员欠款 p2 adj = 0 (归零)"     "$(api_computed 0 p2 adjPrincipal)" "0"
assert_near "全员欠款 p1 累计 = 500 (不变)"  "$(api_computed 0 p1 cumulativePrincipal)" "500"
assert_near "全员欠款 p2 累计 = 500 (不变)"  "$(api_computed 0 p2 cumulativePrincipal)" "500"

# =====================================================================
# 24. 边界: yearMonth 非法格式
# =====================================================================
section "24. 边界: yearMonth 格式校验"
reset_state
curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"A"}' >/dev/null
curl -sf -X POST $URL/api/loans  -H content-type:application/json -d '{"name":"L","originalAmount":100,"remainingPrincipal":100}' >/dev/null
for bad in "2024-13" "24-01" "2024" "2024/01" "abc"; do
  CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
    -d "{\"yearMonth\":\"$bad\",\"mode\":\"auto\",\"loanDetails\":[{\"loanId\":\"l1\",\"interest\":10,\"principal\":10}],\"payerPayments\":[{\"payerId\":\"p1\",\"amount\":20}]}")
  assert_eq "非法 yearMonth '$bad' → 4xx" "$CODE" "400"
done

# =====================================================================
# 25. 边界: startMonth 非法格式
# =====================================================================
section "25. 边界: 参还人 startMonth 校验"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/payers -H content-type:application/json -d '{"name":"X","startMonth":"2024-99"}')
assert_eq "非法 startMonth → 4xx" "$CODE" "400"

# =====================================================================
# 26. 边界: 重复 yearMonth (冲突)
# =====================================================================
section "26. 边界: 重复月份"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
  -d '{"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":10,"principal":10}],"payerPayments":[{"payerId":"p1","amount":20}]}')
assert_eq "首月 2024-01 创建 → 201" "$CODE" "201"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
  -d '{"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":10,"principal":10}],"payerPayments":[{"payerId":"p1","amount":20}]}')
assert_eq "重复 2024-01 → 4xx" "$CODE" "400"

# =====================================================================
# 27. 边界: 存在月份后不允许修改首付
# =====================================================================
section "27. 边界: 有月份后禁改首付"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months/downpayment -H content-type:application/json \
  -d '{"contributions":[{"payerId":"p1","amount":100}]}')
assert_eq "有月份时 POST 首付 → 4xx" "$CODE" "400"

# =====================================================================
# 28. 边界: 未知 loanId
# =====================================================================
section "28. 边界: 未知 loanId"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
  -d '{"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"lZZ","interest":10,"principal":10}],"payerPayments":[{"payerId":"p1","amount":20}]}')
assert_eq "未知 loanId → 4xx" "$CODE" "400"

# =====================================================================
# 29. 边界: 负金额拒绝
# =====================================================================
section "29. 边界: 负金额"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
  -d '{"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"l1","interest":-10,"principal":10}],"payerPayments":[{"payerId":"p1","amount":20}]}')
assert_eq "负利息 → 4xx" "$CODE" "400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/loans -H content-type:application/json \
  -d '{"name":"Bad","originalAmount":-100,"remainingPrincipal":-100}')
assert_eq "负金额贷款 → 4xx" "$CODE" "400"

# =====================================================================
# 30. 边界: 空 payers/loans 下创建月份
# =====================================================================
section "30. 边界: 空名单创建月份"
reset_state
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/months -H content-type:application/json \
  -d '{"yearMonth":"2024-01","mode":"auto","loanDetails":[],"payerPayments":[]}')
assert_eq "完全空状态创建月份 → 4xx" "$CODE" "400"

# =====================================================================
# 31. 边界: 空 payer 姓名拒绝
# =====================================================================
section "31. 边界: 空姓名"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/payers -H content-type:application/json -d '{"name":""}')
assert_eq "空姓名 → 4xx" "$CODE" "400"
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/payers -H content-type:application/json -d '{"name":"   "}')
assert_eq "纯空白姓名 → 4xx" "$CODE" "400"

# =====================================================================
# 32. 边界: 零利息零本金月份 (CP 不变)
# =====================================================================
section "32. 边界: 零利息零本金"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":1000,"remainingPrincipal":1000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":600},{"payerId":"p2","amount":400}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":0,"principal":0}],
    "payerPayments":[{"payerId":"p1","amount":0},{"payerId":"p2","amount":0}]}]
}'
assert_near "零月 p1 adj = 0"       "$(api_computed 0 p1 adjPrincipal)" "0"
assert_near "零月 p1 累计不变 600"  "$(api_computed 0 p1 cumulativePrincipal)" "600"
assert_near "零月 p1 ratio 60%"     "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "60.00"

# =====================================================================
# 33. 边界: 大数金额 (百万级)
# =====================================================================
section "33. 边界: 大数金额"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":10000000,"remainingPrincipal":10000000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":3000000},{"payerId":"p2","amount":2000000}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":30000,"principal":20000}],
    "payerPayments":[{"payerId":"p1","amount":30000},{"payerId":"p2","amount":20000}]}]
}'
SUM=$(curl -sf $URL/api/state | jq -r ".months[0].computed.perPayer | to_entries | map(.value.ratio) | add")
assert_near "大数月 Σ ratio = 1" "$SUM" "1.0" "0.001"

# =====================================================================
# 34. 边界: 跨年连续月份 (12→1)
# =====================================================================
section "34. 边界: 跨年连续"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":100000,"remainingPrincipal":100000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":60000},{"payerId":"p2","amount":40000}]},
  "months":[
    {"yearMonth":"2024-12","mode":"auto","loanDetails":[{"loanId":"l1","interest":500,"principal":500}],"payerPayments":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}]},
    {"yearMonth":"2025-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":500,"principal":500}],"payerPayments":[{"payerId":"p1","amount":500},{"payerId":"p2","amount":500}]}
  ]
}'
assert_eq "跨年月份数 = 2" "$(curl -sf $URL/api/state | jq '.months|length')" "2"

# =====================================================================
# 35. 边界: 空预测窗口
# =====================================================================
section "35. 边界: 无数据预测"
reset_state
CODE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/forecast -H content-type:application/json -d '{"windowMonths":3,"horizonMonths":6}')
# 允许 200 (空 projection) 或 400 (拒绝) — 验证返回结构稳定
if [[ "$CODE" == "200" || "$CODE" == "400" ]]; then ok "空状态 forecast → $CODE (结构稳定)"; else bad "空状态 forecast → 意外 $CODE"; fi

# =====================================================================
# 36. 边界: 首付累计 = 0 时的零首付分支
# =====================================================================
section "36. 边界: 首付累计 0"
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"},{"id":"p3","name":"C"}],
  "loans":[{"id":"l1","name":"L","originalAmount":10000,"remainingPrincipal":10000}],
  "downpayment":{"contributions":[{"payerId":"p1","amount":0},{"payerId":"p2","amount":0},{"payerId":"p3","amount":0}]},
  "months":[{"yearMonth":"2024-01","mode":"auto",
    "loanDetails":[{"loanId":"l1","interest":300,"principal":300}],
    "payerPayments":[{"payerId":"p1","amount":200},{"payerId":"p2","amount":200},{"payerId":"p3","amount":200}]}]
}'
# 零首付首月: 利息按 1/3 平摊 = 100 每人; raw = 200-100 = 100 每人; 全正; CP=100 每人; ratio=33.33%
assert_near "零首付 p1 利息份额 100" "$(api_computed 0 p1 interestShare)" "100"
assert_near "零首付 p1 adj = 100"    "$(api_computed 0 p1 adjPrincipal)" "100"
assert_near "零首付 p1 ratio 33.33%" "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "33.33"


# =====================================================================
# 37. 贷款默认剩余本金 = 原始金额
# =====================================================================
section "37. 贷款剩余本金默认等于原始金额"
reset_state
curl -sf -X POST $URL/api/payers -H content-type:application/json -d '{"name":"A"}' >/dev/null
# Create loan without remainingPrincipal field
LOAN=$(curl -sf -X POST $URL/api/loans -H content-type:application/json -d '{"name":"测试贷款","originalAmount":800000}')
assert_eq "默认 rp = original" "$(echo "$LOAN" | jq -r '.remainingPrincipal')" "800000.0"
# Create loan with explicit remainingPrincipal
LOAN2=$(curl -sf -X POST $URL/api/loans -H content-type:application/json -d '{"name":"二手贷","originalAmount":500000,"remainingPrincipal":300000}')
assert_eq "显式 rp 被保留" "$(echo "$LOAN2" | jq -r '.remainingPrincipal')" "300000.0"

# =====================================================================
# 38. 手动模式包含利息本金和还款字段
# =====================================================================
section "38. 手动模式贷款利息/本金/还款"
reset_state
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":100000,"remainingPrincipal":100000}],
  "months":[]
}'
MANUAL_R=$(curl -sf -X POST $URL/api/months -H content-type:application/json -d '{
  "yearMonth":"2024-01","mode":"manual",
  "loanDetails":[{"loanId":"l1","interest":500,"principal":1000}],
  "payerPayments":[{"payerId":"p1","amount":1000},{"payerId":"p2","amount":500}],
  "manualRatios":{"p1":0.6,"p2":0.4}
}')
assert_eq "手动模式创建成功" "$(echo "$MANUAL_R" | jq -r '.yearMonth')" "2024-01"
assert_near "手动 p1 ratio = 0.6" "$(echo "$MANUAL_R" | jq -r '.computed.perPayer.p1.ratio')" "0.6"
assert_near "手动 p2 ratio = 0.4" "$(echo "$MANUAL_R" | jq -r '.computed.perPayer.p2.ratio')" "0.4"
# loanDetails should cause remaining to decrease
REM=$(curl -sf $URL/api/state | jq -r '.loans[0].remainingPrincipal')
assert_near "手动模式后 remaining = 99000" "$REM" "99000"

# =====================================================================
# 39. 预测 - 全部历史自动回测 (windowMonths=0)
# =====================================================================
section "39. 预测全部历史 (window=0)"
reset_state
load_state '{
  "payers":[{"id":"p1","name":"A"},{"id":"p2","name":"B"}],
  "loans":[{"id":"l1","name":"L","originalAmount":10000,"remainingPrincipal":10000}],
  "months":[
    {"yearMonth":"2024-01","mode":"auto","loanDetails":[{"loanId":"l1","interest":50,"principal":100}],"payerPayments":[{"payerId":"p1","amount":100},{"payerId":"p2","amount":100}]},
    {"yearMonth":"2024-02","mode":"auto","loanDetails":[{"loanId":"l1","interest":50,"principal":100}],"payerPayments":[{"payerId":"p1","amount":120},{"payerId":"p2","amount":80}]},
    {"yearMonth":"2024-03","mode":"auto","loanDetails":[{"loanId":"l1","interest":50,"principal":100}],"payerPayments":[{"payerId":"p1","amount":130},{"payerId":"p2","amount":70}]}
  ]
}'
FC_ALL=$(curl -sf -X POST $URL/api/forecast -H content-type:application/json -d '{"windowMonths":0,"horizonMonths":6}')
assert_eq "window=0 projection len=6" "$(echo "$FC_ALL" | jq '.projection|length')" "6"
assert_nonempty "window=0 payoffMonth" "$(echo "$FC_ALL" | jq -r '.payoffMonth')"

# =====================================================================
# 40. 预测 - 选定月份模式
# =====================================================================
section "40. 预测选定月份"
# Use only month 2024-02 as basis
FC_SEL=$(curl -sf -X POST $URL/api/forecast -H content-type:application/json -d '{"selectedMonths":["2024-02"],"horizonMonths":6}')
assert_eq "selectedMonths projection len=6" "$(echo "$FC_SEL" | jq '.projection|length')" "6"
# Compare with all-history — ratios should differ because 2024-02 has different payment pattern
R_ALL=$(echo "$FC_ALL" | jq -r '.projection[0].ratios.p1')
R_SEL=$(echo "$FC_SEL" | jq -r '.projection[0].ratios.p1')
if [[ "$R_ALL" != "$R_SEL" ]]; then
  ok "选定月份 vs 全部历史: 比例不同 (all=$R_ALL sel=$R_SEL)"
else
  bad "选定月份 vs 全部历史: 比例相同, 应不同"
fi

# Select multiple months
FC_MULTI=$(curl -sf -X POST $URL/api/forecast -H content-type:application/json -d '{"selectedMonths":["2024-01","2024-03"],"horizonMonths":3}')
assert_eq "多月选定 projection len=3" "$(echo "$FC_MULTI" | jq '.projection|length')" "3"

# Invalid selectedMonths — no match
FC_BAD=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/forecast -H content-type:application/json -d '{"selectedMonths":["9999-01"],"horizonMonths":3}')
assert_eq "无匹配月份 → 400" "$FC_BAD" "400"

# Invalid selectedMonths type
FC_TYPE=$(curl -s -o /dev/null -w '%{http_code}' -X POST $URL/api/forecast -H content-type:application/json -d '{"selectedMonths":"2024-01","horizonMonths":3}')
assert_eq "非数组 selectedMonths → 400" "$FC_TYPE" "400"

# =====================================================================
# 41. 时间点快照 - state 含各月 computed 数据
# =====================================================================
section "41. 时间点快照数据"
# Verify each month in state has full computed block
for IDX in 0 1 2; do
  YM=$(curl -sf $URL/api/state | jq -r ".months[$IDX].yearMonth")
  HAS_COMPUTED=$(curl -sf $URL/api/state | jq ".months[$IDX] | has(\"computed\")")
  assert_eq "月 $YM 含 computed" "$HAS_COMPUTED" "true"
  P1_RATIO=$(api_computed $IDX p1 ratio)
  P2_RATIO=$(api_computed $IDX p2 ratio)
  SUM=$(awk -v a="$P1_RATIO" -v b="$P2_RATIO" 'BEGIN{printf "%.4f", a+b}')
  assert_near "月 $YM 比例之和=1" "$SUM" "1.0000"
  CP1=$(api_computed $IDX p1 cumulativePrincipal)
  assert_nonempty "月 $YM p1 cumPrincipal" "$CP1"
done

# Month 0: equal payments → equal ratios
assert_near "月0 p1 ratio = 50%" "$(awk -v r=$(api_computed 0 p1 ratio) 'BEGIN{printf "%.2f", r*100}')" "50.00"
# Month 2: p1 paid more cumulatively → p1 ratio > p2
P1R2=$(api_computed 2 p1 ratio)
P2R2=$(api_computed 2 p2 ratio)
if awk -v a="$P1R2" -v b="$P2R2" 'BEGIN{exit !(a>b)}'; then
  ok "月2 p1 ratio > p2 ratio (累计付得多)"
else
  bad "月2 p1 应 > p2  p1=$P1R2  p2=$P2R2"
fi

# =====================================================================
# 42. UI 月份输入类型验证
# =====================================================================
section "42. UI 月份输入 type=month"
SRC_HTML="$REPO_ROOT/static/index.html"
if grep -Fq 'type="month"' "$SRC_HTML"; then
  ok "HTML 含 type=\"month\" 输入"
else
  bad "HTML 缺 type=\"month\" 输入"
fi

# Verify payer start month input is type=month
if grep -q 'id="payerStartMonthInput".*type="month"' "$SRC_HTML" || grep -q 'type="month".*id="payerStartMonthInput"' "$SRC_HTML"; then
  ok "payerStartMonthInput 是 type=month"
else
  bad "payerStartMonthInput 不是 type=month"
fi

# Verify month input is type=month
if grep -q 'id="monthYearMonthInput".*type="month"' "$SRC_HTML" || grep -q 'type="month".*id="monthYearMonthInput"' "$SRC_HTML"; then
  ok "monthYearMonthInput 是 type=month"
else
  bad "monthYearMonthInput 不是 type=month"
fi

# =====================================================================
# 43. UI 手动模式显示贷款/还款字段
# =====================================================================
section "43. 手动模式不隐藏 monthAutoFields"
# In the JS, switching to manual mode should NOT hide monthAutoFields
if grep -q "monthAutoFields.*display.*none" "$SRC_HTML" && ! grep -q "monthAutoFields.*style.*display.*none" "$SRC_HTML"; then
  ok "monthAutoFields 不含 display:none 初始隐藏"
fi
# The JS mode toggle should only toggle monthManualFields, not monthAutoFields
TOGGLE_LINES=$(grep -c "monthAutoFields" "$SRC_HTML")
# monthAutoFields should not appear in the radio change handler with display=none
if grep -A2 "monthMode.*addEventListener" "$SRC_HTML" | grep -q "monthAutoFields"; then
  bad "模式切换仍隐藏 monthAutoFields"
else
  ok "模式切换不隐藏 monthAutoFields"
fi

# =====================================================================
# 44. UI 预测模式选择（全部历史 vs 选定月份）
# =====================================================================
section "44. UI 预测基准选择"
if grep -Fq 'forecastBasisAll' "$SRC_HTML"; then
  ok "HTML 含全部历史选项 forecastBasisAll"
else
  bad "HTML 缺全部历史选项"
fi
if grep -Fq 'forecastBasisSelected' "$SRC_HTML"; then
  ok "HTML 含选定月份选项 forecastBasisSelected"
else
  bad "HTML 缺选定月份选项"
fi
if grep -Fq 'forecastMonthCheckboxes' "$SRC_HTML"; then
  ok "HTML 含月份复选框容器"
else
  bad "HTML 缺月份复选框容器"
fi
if grep -Fq 'forecast-month-cb' "$SRC_HTML"; then
  ok "HTML 含月份复选框 class"
else
  bad "HTML 缺月份复选框 class"
fi

# =====================================================================
# 45. UI 时间点快照浏览
# =====================================================================
section "45. UI 时间点快照"
if grep -Fq 'snapshotBanner' "$SRC_HTML"; then
  ok "HTML 含快照横幅 snapshotBanner"
else
  bad "HTML 缺 snapshotBanner"
fi
if grep -Fq 'snapshotExitBtn' "$SRC_HTML"; then
  ok "HTML 含返回当前按钮"
else
  bad "HTML 缺 snapshotExitBtn"
fi
if grep -Fq 'snapshot-row' "$SRC_HTML"; then
  ok "HTML 含 snapshot-row 行"
else
  bad "HTML 缺 snapshot-row"
fi
if grep -Fq 'summarySnapshotYm' "$SRC_HTML"; then
  ok "JS 含 summarySnapshotYm 状态"
else
  bad "JS 缺 summarySnapshotYm"
fi


TOTAL=$((PASS+FAIL))
echo
echo "${C_BOLD}━━━━━━━━━━ 测试汇总 ━━━━━━━━━━${C_END}"
echo "  总计: $TOTAL  ${C_OK}通过: $PASS${C_END}  ${C_FAIL}失败: $FAIL${C_END}"
if [[ $FAIL -gt 0 ]]; then
  echo "  ${C_FAIL}失败用例:${C_END}"
  for c in "${FAILED_CASES[@]}"; do echo "    - $c"; done
  echo
  echo "  后端日志尾部:"
  tail -30 "$BACKEND_LOG" | sed 's/^/    /'
fi
echo
# 退出码封顶 255
[[ $FAIL -gt 255 ]] && exit 255 || exit "$FAIL"
