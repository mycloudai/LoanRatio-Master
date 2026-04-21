# Copilot Instructions — LoanRatio-Master

## 测试要求

每次进行可能影响功能行为的代码修改后，**必须**运行以下测试：

### 1. 单元测试

```bash
uv run pytest tests/ -v
```

- 修改 `app/calculator.py`、`app/main.py`、`app/storage.py`、`app/exporter.py` 后必须运行
- 所有用例必须通过后才可提交

### 2. 端到端 QA 测试

```bash
bash qa/run_qa.sh
```

- 修改 API 行为、前端页面、计算逻辑后必须运行
- 运行前确认端口 5057 未被占用：`lsof -ti:5057 | xargs kill 2>/dev/null || true`
- 所有断言必须通过

## CHANGELOG 更新

每次功能性修改完成后，更新 `CHANGELOG.md`：

- 遵循 [Keep a Changelog](https://keepachangelog.com/zh-CN/1.1.0/) 格式
- 使用中文描述变更内容
- 分类：Added / Changed / Fixed / Removed / Improved
- 在 `## [Unreleased]` 或新版本号下添加条目

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
| `static/index.html` | 单文件前端（Bootstrap + Chart.js） |
| `tests/` | pytest 单元测试 |
| `qa/run_qa.sh` | E2E QA 脚本（Playwright） |

## 注意事项

- 前端修改不得删除或更改任何 `data-testid` 属性，QA 脚本依赖这些属性进行定位
- `calculator.py` 中的四步模型是核心算法，修改前应充分理解其数学逻辑
- forecast API 使用浅拷贝贷款对象，不可改回直接引用（防止 state 污染）
