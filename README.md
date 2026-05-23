# kimi-index-plugin

A native plugin for [kimi-cli](https://www.npmjs.com/package/kimi-cli) that brings **semantic code search** and **automatic incremental indexing** to your projects.

[kimi-cli](https://www.npmjs.com/package/kimi-cli) 的原生插件，为项目提供**语义代码搜索**和**自动增量索引**能力。

---

## Features | 特性

- **Semantic Search** — Find code by meaning, not just keywords  
  **语义搜索** — 通过语义理解搜索代码，而非单纯关键词匹配

- **Symbol Tracing** — Trace caller/callee chains across files  
  **符号追踪** — 跨文件追踪函数/类的调用链

- **Auto Incremental Updates** — Background file watcher keeps index fresh  
  **自动增量更新** — 后台文件监控自动保持索引新鲜

- **Remote Embedding** — Uses your existing kimi-cli credentials (no local GPU needed)  
  **远端嵌入** — 复用 kimi-cli 已有凭证，无需本地 GPU

- **Large Project Optimized** — Tested on projects with 1M+ lines of code  
  **大型项目优化** — 已在百万行代码级项目上验证

---

## Requirements | 环境要求

- Python 3.9+
- [kimi-cli](https://www.npmjs.com/package/kimi-cli) installed and logged in (`kimi login`)  
  已安装并登录的 [kimi-cli](https://www.npmjs.com/package/kimi-cli)（`kimi login`）

---

## Installation | 安装

```bash
# 1. Clone or download this repository | 克隆仓库
git clone https://github.com/lqt0707/kimi-index-plugin.git
cd kimi-index-plugin

# 2. Run setup (creates virtual environment + installs dependencies) | 运行安装脚本
./setup.sh

# 3. Install into kimi-cli | 安装到 kimi-cli
kimi plugin install $(pwd)
```

---

## Usage | 使用

Once installed, the plugin tools are automatically available in every kimi-cli session.  
安装后，插件工具会在每个 kimi-cli 会话中自动可用。

| Tool | Description | 描述 |
|------|-------------|------|
| `CodeIndexSearch` | Semantic search with natural language | 自然语言语义搜索 |
| `CodeIndexBuild` | Build or update the code index | 构建或更新代码索引 |
| `CodeIndexWatch` | Start/stop background auto-update watcher | 启停后台自动监控 |
| `CodeIndexTrace` | Trace symbol caller/callee chains | 追踪符号调用链 |
| `CodeIndexStatus` | View index status and watcher state | 查看索引和监控状态 |

### First-time setup in a project | 项目首次使用

Simply ask the LLM:  
直接对 LLM 说：

> "Build a code index for this project and enable auto-updates"  
> "为这个项目构建代码索引并启用自动更新"

The LLM will:  
LLM 会自动：
1. Call `CodeIndexBuild` to create the initial index  
   调用 `CodeIndexBuild` 创建初始索引
2. Call `CodeIndexWatch('start')` to enable background file monitoring  
   调用 `CodeIndexWatch('start')` 启动后台文件监控

### Manual workflow | 手动操作

```bash
cd your-project

# Build index | 构建索引
echo '{"paths": ["src"]}' | ./.venv/bin/python3 tools/build.py

# Start background watcher | 启动后台监控
echo '{"action": "start"}' | ./.venv/bin/python3 tools/watch.py

# Search | 搜索
echo '{"query": "user authentication logic", "limit": 10}' | ./.venv/bin/python3 tools/search.py
```

---

## How It Works | 工作原理

1. **Credential Reuse** — The plugin automatically injects `api_key` and `base_url` from your existing kimi-cli configuration. No extra API key setup needed.  
   **凭证复用** — 插件自动注入 kimi-cli 已配置的 `api_key` 和 `base_url`，无需额外配置。

2. **Layered Indexing** —  
   **分层索引** —
   - **File-level** (default): One vector per source file (~70 API batches for 3,700 files)  
     **文件级**（默认）：每个源码文件一个向量（3700 文件约 70 批 API）
   - **Symbol-level** (optional): One vector per function/class for core directories  
     **符号级**（可选）：核心目录的函数/类级别索引

3. **Storage** — Index data is stored in your project's `.kimi-index/` directory (not committed to git):  
   **存储** — 索引数据存储在项目目录的 `.kimi-index/` 中（不会被 git 提交）：
   - `index.db` — SQLite metadata | SQLite 元数据
   - `file_vectors.npy` — File-level embedding vectors | 文件级向量
   - `symbol_vectors.npy` — Symbol-level embedding vectors | 符号级向量

4. **Background Watcher** — A standalone daemon process monitors file changes and incrementally updates only the changed files.  
   **后台监控** — 独立守护进程监听文件变更，仅增量更新变更的文件。

---

## Configuration | 配置

Edit `config.json` to customize:  
编辑 `config.json` 自定义配置：

```json
{
  "embedding_model": "bge_m3_embed",
  "embedding_batch_size": 64,
  "file_max_lines": 200,
  "symbol_paths": ["src/lib", "src/utils"],
  "exclude_patterns": ["**/*.test.ts", "**/*.d.ts", "**/node_modules/**"]
}
```

---

## Supported Languages | 支持语言

TypeScript, JavaScript, Vue, Python, Go, Rust, Java, Kotlin, Swift, Ruby, PHP

---

## License | 许可证

MIT
