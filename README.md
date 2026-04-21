# 共同还贷所有权计算器（LoanRatio-Master）

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](./LICENSE)
[![Python](https://img.shields.io/badge/Python-3.11%2B-blue.svg)](https://www.python.org/)

> **核心思路**：每月还款中，先按上月权益比例扣除各自应承担的利息，剩余部分才是真正在"还本金"——谁多还本金，谁的产权占比就会增长。特殊月份也可手动指定比例，当月的本金按指定比例计入各人账上，下月恢复自动时系统会根据所有历史累计本金重新推算。
>
> **本金归因模型**：首付计入初始累计本金 → 每月用上月权益比例分摊利息 → 实际还款减去利息得到净本金贡献 → 净本金累加到各自的累计本金 → 由累计本金比例得出新的权益占比。其中"上月权益比例"即各人截至上月的累计本金占所有人累计本金总和的比值，首月则以首付比例为初始值。如此逐月迭代，权益比例始终反映每个人实际承担的本金总额。
>
> 一个本地优先的共同还贷产权比例计算器：每月按实际还款金额动态更新各参还人产权，历史可改、自动重算、纯本地数据。

**项目地址**：<https://github.com/mycloudai/LoanRatio-Master>

---

## ✨ 功能亮点

- 🧮 **本金归因模型**：基于「上月权益比例分摊利息 → 净本金贡献 → 累计本金 → 新权益」的严密计算链
- 🔄 **历史可修改**：任意月份调整后自动级联重算后续所有月份
- ✋ **自动 / 手动双模式**：自动按真实还款计算，手动按协商比例直接指定
- 💸 **首付支持**：首付直接计入累计本金，作为第一个月的权益基准
- ⚠️ **负本金再分配**：还款不够利息时按上月权益比例由其他人「垫付」
- 📊 **趋势预测**：基于近 N 月平均值预测未来 12～60 个月权益走势及结清月份
- 📁 **本地 JSON 存储**：数据路径用户自选，方便备份
- 📤 **Excel 导出**：含原生公式，可在 Excel 中改数即时查看变化

---

## 🚀 快速开始

### 环境要求

- **Python 3.11+**
- [uv](https://docs.astral.sh/uv/) （推荐的 Python 包管理器，自动处理依赖与虚拟环境）

> uv 安装：`curl -LsSf https://astral.sh/uv/install.sh | sh`（macOS / Linux）

### 一键启动

```bash
uv sync && uv run loanratio
```

或使用启动脚本（macOS / Linux）：

```bash
./start.sh
```

Windows 用户：双击 `start.bat`。

启动后会自动在浏览器打开 `http://127.0.0.1:5000`。首次启动会引导你设置数据保存路径（默认 `~/LoanRatioData/`）。

---

## 🧮 核心计算模型

### 变量定义

| 符号 | 含义 |
|------|------|
| $n$ | 参还人总数 |
| $I(t)$ | 第 $t$ 月所有贷款合计实际利息金额 |
| $r_i(t)$ | 参还人 $i$ 在第 $t$ 月结束后的累计权益比例 |
| $\text{pay}_i(t)$ | 参还人 $i$ 第 $t$ 月的总还款金额 |
| $p_i(t)$ | 参还人 $i$ 第 $t$ 月归因到自己名下的净本金贡献 |
| $CP_i(t)$ | 参还人 $i$ 截至第 $t$ 月的累计净本金贡献总额 |
| $CP(t)$ | 所有参还人截至第 $t$ 月的累计净本金总额 |
| $P(t)$ | 第 $t$ 月所有贷款合计归还本金金额 |

### 自动模式四步公式

**第一步：分摊利息**

$$\text{interestShare}_i(t) = r_i(t-1) \times I(t)$$

**第二步：原始净本金贡献（可为负）**

$$p_i^{\text{raw}}(t) = \text{pay}_i(t) - \text{interestShare}_i(t)$$

**第三步：负本金再分配**

设正贡献集合 $S^+$、负贡献集合 $S^-$：

$$\text{负贡献总额} = \sum_{j \in S^-} |p_j^{\text{raw}}(t)|$$

$$p_i^{\text{adj}}(t) = p_i^{\text{raw}}(t) + \frac{r_i(t-1)}{\sum_{k \in S^+} r_k(t-1)} \times \text{负贡献总额}, \quad i \in S^+$$

$$p_j^{\text{adj}}(t) = 0, \quad j \in S^-$$

**第四步：更新累计本金与权益比例**

$$CP_i(t) = CP_i(t-1) + p_i^{\text{adj}}(t)$$

$$r_i(t) = \frac{CP_i(t)}{CP(t)}$$

### 手动模式

用户直接指定本月各参还人权益比例（总和 100%），当月本金按手动比例计入 $CP_i$：

$$P^{\text{actual}}(t) = \max\!\Big(0,\;\sum_i \text{pay}_i(t) - I(t)\Big)$$

$$p_i^{\text{adj}}(t) = r_i^{\text{manual}} \times P^{\text{actual}}(t)$$

$$CP_i(t) = CP_i(t-1) + p_i^{\text{adj}}(t)$$

其中 $P^{\text{actual}}(t)$ 为所有参还人实际还款总额减去利息，即真实的本金贡献；若还款不足以覆盖利息则为零。

利息分摊同样按手动比例计算：$\text{interestShare}_i(t) = r_i^{\text{manual}} \times I(t)$。

切回自动模式后，下月的 $r_i(t-1)$ 恢复为 $CP_i(t) / CP(t)$（即累计本金比例），而非沿用手动比例。

---

## 📖 白话示例

以张三、李四共同还贷为例，每月利息 **3000 元**。

### 情景 A：有首付，从第一个月开始

> 张三出 60 万首付，李四出 40 万首付。

1. 首付合计 100 万 → 初始权益：张三 60%，李四 40%
2. 第一个月利息分摊：张三 1800 元、李四 1200 元
3. 后续按情景 C 流程

### 情景 B：0 首付，从第一个月开始

- 没有历史比例，**第一个月利息按 50/50 平摊**（仅此一次）
- 第一个月结算后，按实际本金贡献覆盖 50/50，后续按真实比例走

### 情景 C：正常月份（通用流程）

> 延续情景 A。上月末张三累计 60 万、李四 40 万（首付），权益 60% / 40%。
> 本月利息 3000 元，张三还 4000、李四还 2000。

| 步骤 | 张三 | 李四 |
|---|---|---|
| 应承担利息 | 3000×60% = 1800 | 3000×40% = 1200 |
| 净本金 = 还款 - 利息 | +2200 | +800 |
| 累计本金 | 602200 | 400800 |
| 新权益比例 | ≈ 60.04% | ≈ 39.96% |

### 情景 D：李四还款不够覆盖利息（负本金）

> 同情景 C 的比例基础（60% / 40%），但李四本月只还 1000 元（< 1200 元利息）。

- 张三净本金：4000 - 1800 = +2200
- 李四净本金：1000 - 1200 = **-200** ← 负数

**处理方式**：李四本月本金归 0（白还了），200 元由正贡献人按上月权益比例「垫付」。本情景只有张三是正贡献人：

- 张三最终：2200 + 200 = **2400 元**
- 李四最终：**0 元**（本月对产权毫无贡献）

### 情景 E：手动比例月

- 双方协商本月产权各 50%
- 当月归还的本金按 50/50 计入各自 $CP_i$
- 下月若切回自动模式，利息按累计本金比例 $CP_i/CP$ 分摊（非手动比例）
- ⚠️ 手动比例与累计本金比例偏差较大时，界面显示断点提示

### 一句话核心结论

> 每个月扣掉「按你上月持有比例该承担的利息」后，**剩下的才算你这个月真正还了多少本金**；全部历史本金加起来，就是你的产权比例。

---

## 💾 数据说明

数据保存为本地 JSON 文件，用户可自选路径，便于备份。

```json
{
  "config": {
    "dataPath": "/path/to/data.json"
  },
  "payers": [
    { "id": "p1", "name": "张三" }
  ],
  "loans": [
    { "id": "l1", "name": "商业贷款", "originalAmount": 1000000, "remainingPrincipal": 850000 }
  ],
  "months": [
    {
      "yearMonth": "2024-01",
      "mode": "auto",
      "loanDetails": [
        { "loanId": "l1", "interest": 3200, "principal": 2800 }
      ],
      "payerPayments": [
        { "payerId": "p1", "amount": 3500 },
        { "payerId": "p2", "amount": 2500 }
      ],
      "computed": {
        "p1": { "interestShare": 1800, "rawPrincipal": 1700, "adjPrincipal": 1700, "ratio": 0.52 },
        "p2": { "interestShare": 1400, "rawPrincipal": 1100, "adjPrincipal": 1100, "ratio": 0.48 }
      }
    }
  ]
}
```

- 配置文件：`~/.loanratio_config.json`（记录数据文件路径）
- 默认数据目录：`~/LoanRatioData/`
- 备份方式：直接复制数据 JSON 文件

---

## 🛠️ 开发指南

### 安装与运行

```bash
uv sync                       # 安装依赖
uv run loanratio              # 启动应用
```

### 测试与代码检查

```bash
uv run pytest                 # 运行单元测试
uv run ruff check .           # Lint
uv run ruff format .          # 格式化
```

### 端到端测试（QA）

QA 使用 [`playwright-cli`](https://www.npmjs.com/package/@playwright/cli) + bash 脚本，覆盖全部核心公式场景与边界情况：

```bash
# 默认无头模式，使用端口 5057
bash qa/run_qa.sh

# 有头模式（可视化调试）
bash qa/run_qa.sh --headed

# 自定义端口
bash qa/run_qa.sh --port 8080
```

> 依赖：`uv`、`jq`、`playwright-cli`（`npm i -g @playwright/cli` + `playwright-cli install chromium`）。  
> GitHub Actions 在每次推送 `main` 时自动运行该脚本（见 `.github/workflows/qa.yml`）。

### 项目结构

```
LoanRatio-Master/
├── app/
│   ├── main.py           # Flask 路由入口
│   ├── calculator.py     # 核心计算逻辑
│   ├── storage.py        # JSON 文件读写与配置管理
│   └── exporter.py       # Excel 导出
├── static/
│   └── index.html        # 单文件前端
├── tests/                # pytest 单元测试
├── qa/
│   └── run_qa.sh         # playwright-cli + curl 端到端 QA 脚本
├── start.sh / start.bat  # 启动脚本
├── pyproject.toml        # uv / Python 项目配置 (单一版本源)
├── CHANGELOG.md          # 版本历史 (UI 中可查看)
└── .github/workflows/    # CI 单元测试 + QA E2E
```

---

## 🤝 参与贡献

欢迎提交 Issue 与 Pull Request！请先阅读 [CONTRIBUTING.md](./CONTRIBUTING.md) 与 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。

- 分支策略：`feature/*`、`fix/*`，`main` 受保护
- 提交规范：[Conventional Commits](https://www.conventionalcommits.org/)
- 代码风格：PEP 8 + ruff + black；前端保持单文件

---

## 📄 许可证

本项目基于 [MIT License](./LICENSE) 发布。
