---
name: kimi-index-plugin
description: Semantic code indexing for Kimi CLI. Automatically activated when the project has a .kimi-index/ directory. Enforces fully automated indexing workflow — auto-build on first use, auto-watch for incremental updates, and mandatory CodeIndexSearch before any file operations.
---

# Kimi Index Plugin — 全自动语义代码索引

## 何时激活

当当前项目目录包含 `.kimi-index/`（语义索引数据）时，本 skill **自动激活**。

## 核心能力

### 1. 全自动索引工作流

本插件设计为**零手动维护**。你必须遵循以下自动化规则：

#### 规则 A：首次使用自动构建
**如果项目没有 `.kimi-index/` 目录，你必须立即调用 `CodeIndexBuild` 构建索引。**
- 不要等待用户请求，检测到无索引时主动构建
- 构建完成后，立即调用 `CodeIndexWatch('start')` 启动后台监控

#### 规则 B：后台自动增量更新
**索引构建完成后，必须启动 `CodeIndexWatch`。**
- 启动后，文件系统变更会自动触发增量索引更新
- 你无需手动调用 `CodeIndexBuild` 来更新索引
- 如果 `CodeIndexStatus` 显示 watcher 未运行，立即启动它

#### 规则 C：语义搜索强制优先
**在调用 ReadFile 读取任何文件之前，你必须先调用 `CodeIndexSearch` 搜索相关代码。**
- 如果你尚未调用 `CodeIndexSearch` 就直接读取文件，**必须立即停止当前操作，返回并执行搜索**
- 这是硬性要求，不是建议

### 2. 搜索工具使用顺序

1. `CodeIndexSearch` — 用自然语言搜索代码和文档
   - 示例："用户登录逻辑"、"订单状态机"、"JWT token 刷新"
   - 比关键词匹配更精准，能找到语义相关但命名不同的代码
2. `CodeIndexTrace` — 理清调用关系（如需要）
   - 追踪函数/类的调用方或被调用方

### 3. 搜索后再读取文件

语义搜索找到相关文件后，再用 `ReadFile` 读取具体内容。

### 4. 基于搜索结果分析修改

基于 `CodeIndexSearch` 返回的搜索结果进行分析，然后执行修改。

## 自动化检查清单（每次会话必须执行）

在开始任何代码相关任务前，确认以下检查项：

- [ ] **索引存在**：检查 `CodeIndexStatus`，如果不存在索引 → 调用 `CodeIndexBuild`
- [ ] **监控运行**：检查 `CodeIndexStatus` 中的 watcher 状态，如果停止 → 调用 `CodeIndexWatch('start')`
- [ ] **已搜索**：确认已通过 `CodeIndexSearch` 搜索了与任务相关的代码
- [ ] **已追踪**：如涉及函数修改，确认已通过 `CodeIndexTrace` 理清了调用链

## 为什么不能用内置 Grep 代替 CodeIndexSearch？

- 内置 Grep 只能做关键词匹配，无法理解语义
- CodeIndexSearch 基于向量索引，能找到语义相关但关键词不同的代码
- 项目已建立完整的语义索引，不利用等于浪费

**唯一例外（可用内置 Grep）**
仅以下情况可用内置 Grep：
- 查找已知确切文件名的文件路径
- 查找配置项、常量等精确字符串匹配

## 快速参考

| 需求 | 操作 |
|------|------|
| 首次使用项目 | `CodeIndexBuild` → `CodeIndexWatch('start')` |
| 搜索代码 | `CodeIndexSearch "自然语言描述"` |
| 查找调用方 | `CodeIndexTrace "函数名"` |
| 查看索引状态 | `CodeIndexStatus` |
| 手动更新索引 | `CodeIndexBuild`（通常不需要，watcher 自动处理） |
| 启停自动监控 | `CodeIndexWatch('start')` / `CodeIndexWatch('stop')` |

## 工作流示例

```
用户：帮我找一下用户认证的代码

正确做法（全自动）：
1. CodeIndexStatus          ← 检查索引和监控状态
2. CodeIndexBuild           ← 如无索引则自动构建
3. CodeIndexWatch('start')  ← 启动后台自动监控
4. CodeIndexSearch "用户认证逻辑"
5. CodeIndexSearch "JWT token 刷新"
6. 基于搜索结果读取相关文件
7. 分析并回答

错误做法：
❌ 直接用内置 Grep 搜 "auth" — 会遗漏语义相关但命名不同的代码
❌ 不检查索引状态就开始搜索 — 可能搜索空索引
❌ 不启动 watcher — 文件变更后索引会过时
```
