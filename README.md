# kimi-index-plugin

A native plugin for [kimi-cli](https://www.npmjs.com/package/kimi-cli) that brings **semantic code search** and **automatic incremental indexing** to your projects.

## Features

- **Semantic Search** — Find code by meaning, not just keywords
- **Symbol Tracing** — Trace caller/callee chains across files
- **Auto Incremental Updates** — Background file watcher keeps index fresh
- **Remote Embedding** — Uses your existing kimi-cli credentials (no local GPU needed)
- **Large Project Optimized** — Tested on projects with 1M+ lines of code

## Requirements

- Python 3.9+
- [kimi-cli](https://www.npmjs.com/package/kimi-cli) installed and logged in (`kimi login`)

## Installation

```bash
# 1. Clone or download this repository
git clone https://github.com/YOUR_USERNAME/kimi-index-plugin.git
cd kimi-index-plugin

# 2. Run setup (creates virtual environment + installs dependencies)
./setup.sh

# 3. Install into kimi-cli
kimi plugin install $(pwd)
```

## Usage

Once installed, the plugin tools are automatically available in every kimi-cli session:

| Tool | Description |
|------|-------------|
| `CodeIndexSearch` | Semantic search with natural language |
| `CodeIndexBuild` | Build or update the code index |
| `CodeIndexWatch` | Start/stop background auto-update watcher |
| `CodeIndexTrace` | Trace symbol caller/callee chains |
| `CodeIndexStatus` | View index status and watcher state |

### First-time setup in a project

Simply ask the LLM:

> "Build a code index for this project and enable auto-updates"

The LLM will:
1. Call `CodeIndexBuild` to create the initial index
2. Call `CodeIndexWatch('start')` to enable background file monitoring

### Manual workflow

```bash
cd your-project

# Build index
echo '{"paths": ["src"]}' | ./.venv/bin/python3 tools/build.py

# Start background watcher
echo '{"action": "start"}' | ./.venv/bin/python3 tools/watch.py

# Search
echo '{"query": "user authentication logic", "limit": 10}' | ./.venv/bin/python3 tools/search.py
```

## How It Works

1. **Credential Reuse** — The plugin automatically injects `api_key` and `base_url` from your existing kimi-cli configuration. No extra API key setup needed.

2. **Layered Indexing** —
   - **File-level** (default): One vector per source file (~70 API batches for 3,700 files)
   - **Symbol-level** (optional): One vector per function/class for core directories

3. **Storage** — Index data is stored in your project's `.kimi-index/` directory (not committed to git):
   - `index.db` — SQLite metadata
   - `file_vectors.npy` — File-level embedding vectors
   - `symbol_vectors.npy` — Symbol-level embedding vectors

4. **Background Watcher** — A standalone daemon process monitors file changes and incrementally updates only the changed files.

## Configuration

Edit `config.json` to customize:

```json
{
  "embedding_model": "bge_m3_embed",
  "embedding_batch_size": 64,
  "file_max_lines": 200,
  "symbol_paths": ["src/lib", "src/utils"],
  "exclude_patterns": ["**/*.test.ts", "**/*.d.ts", "**/node_modules/**"]
}
```

## Supported Languages

TypeScript, JavaScript, Vue, Python, Go, Rust, Java, Kotlin, Swift, Ruby, PHP

## License

MIT
