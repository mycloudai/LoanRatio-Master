# 贡献指南 / Contributing

感谢你对 **LoanRatio-Master** 的关注！本指南介绍如何参与本项目的开发与维护。

---

## 🌿 分支策略

- `main` — 主分支，**受保护**，只接受通过 PR 合入的代码
- `feature/<short-desc>` — 新功能开发（例：`feature/excel-export`）
- `fix/<short-desc>` — Bug 修复（例：`fix/negative-principal`）
- `docs/<short-desc>` — 仅文档变更
- `refactor/<short-desc>` — 重构（不改变外部行为）

请不要直接向 `main` 推送代码。

---

## ✍️ 提交信息规范（Conventional Commits）

所有提交必须遵循 [Conventional Commits](https://www.conventionalcommits.org/) 规范：

```
<type>[optional scope]: <description>

[optional body]

[optional footer(s)]
```

常用类型：

| 类型 | 含义 |
|------|------|
| `feat` | 新功能 |
| `fix` | Bug 修复 |
| `docs` | 文档变更 |
| `style` | 代码格式（不影响逻辑） |
| `refactor` | 重构 |
| `test` | 增删测试 |
| `chore` | 构建 / 工具链 / 杂项 |
| `ci` | CI 配置 |

示例：

```
feat(calculator): 支持负本金按上月权益比例再分配
fix(storage): 修复数据路径含中文时无法读取
docs(readme): 补充情景 D 的计算示例
```

---

## 🔁 PR 流程

1. **Fork** 本仓库，并基于 `main` 创建你的特性分支
2. 在本地完成开发，确保：
   - `uv run pytest` 全部通过
   - `uv run ruff check .` 无报错
   - 新增功能附带单元测试
3. 推送到你的 fork，开启 Pull Request 指向本仓库 `main`
4. 在 PR 描述中清晰说明：
   - 解决了什么问题 / 新增了什么功能
   - 关联的 Issue 编号（如有）
   - 测试方式与预期结果
5. 等待 CI 通过 + Code Review
6. 至少需要 1 位维护者 Approve 后方可合并
7. 合并方式：**Squash and merge**，并保持提交信息符合 Conventional Commits

---

## 🎨 代码风格

### Python

- 遵循 [PEP 8](https://peps.python.org/pep-0008/)
- 使用 `ruff` 进行 lint：`uv run ruff check .`
- 使用 `black`（或 `ruff format`）进行格式化：`uv run ruff format .`
- 函数 / 类需有 docstring，关键计算步骤附公式说明
- 类型注解尽量完整（`from __future__ import annotations` 视情况使用）

### 前端（`static/index.html`）

- **保持单文件**：HTML / CSS / JS 全部在一个 `index.html` 中（这是项目的核心架构约束，不要拆分）
- JS 风格保持一致，关键函数附注释
- 仅通过 CDN 引入第三方库（Bootstrap、Chart.js）
- 不引入任何前端构建工具（webpack / vite / npm 包等）

### 通用

- 不提交敏感信息（密钥、个人路径、真实贷款数据等）
- `~/.loanratio_config.json` 与用户数据文件已在 `.gitignore` 中排除
- 文件末尾保留一个空行，使用 LF 换行符

---

## ✅ 测试要求

- **核心计算逻辑** (`app/calculator.py`) **必须**有单元测试，覆盖率目标 ≥ 90%
- 修改历史月份的级联重算路径必须有回归测试
- PR 提交前必须本地通过：

  ```bash
  uv run pytest
  uv run ruff check .
  ```

- E2E 测试（如修改前端流程）：

  ```bash
  cd qa && npm test
  ```

---

## 🐛 报告问题

请使用 [Issue 模板](./.github/ISSUE_TEMPLATE/) 提交 Bug 或功能建议，提供尽可能详细的复现步骤、环境信息与期望行为。

---

## 📜 行为准则

参与本项目即表示你同意遵守 [CODE_OF_CONDUCT.md](./CODE_OF_CONDUCT.md)。

---

谢谢你的贡献！🎉
