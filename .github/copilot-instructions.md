# Copilot Instructions — LoanRatio-Master

## 测试要求

每次进行可能影响功能行为的代码修改后，**必须**运行以下测试：

### 1. 单元测试

```bash
uv run pytest tests/ -v
```

- 修改 `app/calculator.py`、`app/main.py`、`app/storage.py`、`app/exporter.py` 后必须运行
- 所有用例必须通过后才可提交

### 2. Sanity Test（综合计算准确性校验）

```bash
bash qa/run_qa.sh --sanity-only
```

- QA 脚本的 section 54，使用 Playwright + curl 双端校验
- 使用预构建的 mock 数据集验证全部计算路径：自动/手动模式、首付、负本金再分配、预测回测
- 同时校验 API 返回值和 UI 渲染结果的一致性
- **适用场景**：代码重构、Bug 修复、样式调整等 **不涉及功能新增或 UI 按钮变更** 的修改

### 3. 端到端 QA 测试（完整回归）

```bash
bash qa/run_qa.sh
```

- 包含 Sanity Test (section 54) + 全部 UI 交互测试 (sections 1-53)
- **必须运行的场景**：
  - 新增功能
  - 修改 API 行为
  - 修改前端页面（按钮、布局、交互逻辑）
  - 修改 `data-testid` 属性
- 运行前确认端口 5057 未被占用：`lsof -ti:5057 | xargs kill 2>/dev/null || true`
- 所有断言必须通过

### 测试选择指南

| 修改类型 | 单元测试 | Sanity Test | 完整 QA |
|---------|---------|-------------|---------|
| 计算引擎 bug 修复 | ✅ | ✅ | ❌ |
| 代码重构（无功能变更） | ✅ | ✅ | ❌ |
| CSS/样式调整 | ❌ | ✅ | ❌ |
| 新增 API 端点 | ✅ | ✅ | ✅ |
| 前端新功能/按钮 | ✅ | ✅ | ✅ |
| 修改现有 UI 交互 | ✅ | ✅ | ✅ |

## CHANGELOG 更新

每次功能性修改完成后，更新 `CHANGELOG.md`：

- 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式
- 使用中文描述变更内容
- 分类：Added / Changed / Fixed / Removed / Improved
- **每次新修改必须创建新版本号条目**，不得将新变更追加到已有版本中；已发布的版本条目视为不可变
- 在文件顶部（`---` 分隔线之后）添加新版本块

## USERGUIDE 更新

每次涉及用户可感知的功能新增或变更后，同步更新 `USERGUIDE.md`：

- 使用通俗易懂的中文，面向普通用户（非开发者）
- 涵盖新功能的操作步骤和注意事项
- 如有 UI 变化，更新对应章节的描述

## 版本号更新

- 遵循 [Semantic Versioning](https://semver.org/)
- 修改 `pyproject.toml` 中的 `version` 字段
- Bug 修复 → patch（如 0.1.0 → 0.1.1）
- 新功能 → minor（如 0.1.1 → 0.2.0）
- 破坏性变更 → major（如 0.2.0 → 1.0.0）

## 项目结构

| 目录/文件 | 说明 |
|-----------|------|
| `app/calculator.py` | 核心四步计算引擎 |
| `app/main.py` | Flask 路由与 API |
| `app/storage.py` | JSON 文件存储 |
| `app/exporter.py` | Excel 导出 |
| `static/index.html` | 单文件前端（Bootstrap + 内联 SVG 图表） |
| `USERGUIDE.md` | 用户使用指南（前端可查看） |
| `tests/` | pytest 单元测试 |
| `qa/run_qa.sh` | E2E QA 脚本（Playwright），含 sanity test（`--sanity-only` 可单独运行） |

## 注意事项

- 前端修改不得删除或更改任何 `data-testid` 属性，QA 脚本依赖这些属性进行定位
- `calculator.py` 中的四步模型是核心算法，修改前应充分理解其数学逻辑
- forecast API 使用浅拷贝贷款对象，不可改回直接引用（防止 state 污染）
