# kimi-index-plugin

> [English README](README.en.md)

[kimi-cli](https://www.npmjs.com/package/kimi-cli) 的原生插件，为项目提供**语义代码搜索**和**自动增量索引**能力。

## 特性

- **语义搜索** — 通过语义理解搜索代码，而非单纯关键词匹配
- **符号追踪** — 跨文件追踪函数/类的调用链
- **自动增量更新** — 后台文件监控自动保持索引新鲜
- **远端嵌入** — 复用 kimi-cli 已有凭证，无需本地 GPU
- **大型项目优化** — 已在百万行代码级项目上验证

## 环境要求

- Python 3.9+
- 已安装并登录的 [kimi-cli](https://www.npmjs.com/package/kimi-cli)（`kimi login`）

## 安装

```bash
# 1. 克隆仓库
git clone https://github.com/lqt0707/kimi-index-plugin.git
cd kimi-index-plugin

# 2. 运行安装脚本（创建虚拟环境 + 安装依赖）
./setup.sh

# 3. 安装到 kimi-cli
kimi plugin install $(pwd)
```

## 使用

安装后，插件工具会在每个 kimi-cli 会话中自动可用。

| 工具 | 描述 |
|------|------|
| `CodeIndexSearch` | 自然语言语义搜索 |
| `CodeIndexBuild` | 构建或更新代码索引 |
| `CodeIndexWatch` | 启停后台自动监控 |
| `CodeIndexTrace` | 追踪符号调用链 |
| `CodeIndexStatus` | 查看索引和监控状态 |

### 项目首次使用

直接对 LLM 说：

> "为这个项目构建代码索引并启用自动更新"

LLM 会自动：
1. 调用 `CodeIndexBuild` 创建初始索引
2. 调用 `CodeIndexWatch('start')` 启动后台文件监控

### 手动操作

```bash
cd your-project

# 构建索引
echo '{"paths": ["src"]}' | ./.venv/bin/python3 tools/build.py

# 启动后台监控
echo '{"action": "start"}' | ./.venv/bin/python3 tools/watch.py

# 搜索
echo '{"query": "用户认证逻辑", "limit": 10}' | ./.venv/bin/python3 tools/search.py
```

## 工作原理

1. **凭证复用** — 插件自动注入 kimi-cli 已配置的 `api_key` 和 `base_url`，无需额外配置。

2. **分层索引** —
   - **文件级**（默认）：每个源码文件一个向量（3700 文件约 70 批 API）
   - **符号级**（可选）：核心目录的函数/类级别索引

3. **存储** — 索引数据存储在项目目录的 `.kimi-index/` 中（不会被 git 提交）：
   - `index.db` — SQLite 元数据
   - `file_vectors.npy` — 文件级向量
   - `symbol_vectors.npy` — 符号级向量

4. **后台监控** — 独立守护进程监听文件变更，仅增量更新变更的文件。

## 配置

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

## 支持语言

TypeScript, JavaScript, Vue, Python, Go, Rust, Java, Kotlin, Swift, Ruby, PHP

## 许可证

MIT
