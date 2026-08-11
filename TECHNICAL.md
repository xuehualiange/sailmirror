# 出海镜 SailMirror — 技术文档

> 本文档汇总自 `README.md` 技术说明、各模块代码注释（docstring）及实现细节，便于开发维护与二次集成。

---

## 1. 产品与技术定位

### 1.1 产品定位（来自 README）

面向跨境卖家的 AI 上新合规检测器。上传商品图和 Listing，输出目标市场的：

- **能不能卖**（硬合规）
- **卖不卖得动**（文化合规）

双轨风险报告。

### 1.2 核心功能（来自 README）

| 维度 | 检测内容 |
|------|----------|
| 文化合规 | 宗教符号、禁忌色、人物着装、手势姿态 |
| 硬合规 | 敏感词、平台规则、认证要求 |
| 报告 | 一份报告，两个维度 |

### 1.3 技术架构（README + 实际实现）

README 原始描述：

- 多 Agent 协同 + 阿里云百炼
- `qwen3-vl-plus`：视觉理解
- `qwen3.7-max`：推理与报告生成
- Streamlit：前端展示（**规划中**；当前主入口为 CLI `main.py`，另有静态原型 `index.html`）

**当前已实现架构：**

```
用户输入（图片 + Listing + 目标市场）
        │
        ▼
┌───────────────────────────────────────┐
│  启动校验：市场 / 图片 / API Key       │
│  规则库预加载（knowledge/*.json）      │
└───────────────────────────────────────┘
        │
        ▼
┌───────────────────────────────────────┐
│  RAG 并行预取（knowledge/retrieval.py）│
│  · 文化规则 Top-K                      │
│  · 硬合规规则 Top-K                    │
└───────────────────────────────────────┘
        │
        ▼
┌──────────────────┐    ┌──────────────────┐
│  文化 Agent      │    │  硬合规 Agent     │
│  RAG + VL 模型   │    │  RAG + 文本模型   │
│  (并行 Thread)   │    │  (并行 Thread)    │
└──────────────────┘    └──────────────────┘
        │                        │
        └──────────┬─────────────┘
                   ▼
           双轨合规报告 + performance.log
```

---

## 2. 项目结构

```
sailmirror-main/
├── main.py                 # 主程序：双 Agent 并行检测、RAG 集成、性能日志
├── knowledge/
│   ├── retrieval.py        # 规则库 RAG 检索模块
│   └── culture_rules_v1.json  # 文化/硬合规规则库（60 条）
├── convert_rules.py        # Excel 规则库 → JSON 转换工具
├── test_vl.py              # 单图视觉检测测试脚本
├── batch_test.py           # 10 张测试图批量文化合规检测
├── index.html              # 前端 UI 原型（静态 Demo，未接后端）
├── performance.log         # 运行时性能日志（自动生成）
├── requirements.txt        # Python 依赖
└── README.md               # 项目说明
```

---

## 3. 依赖与环境

### 3.1 Python 依赖（requirements.txt）

| 包 | 用途 |
|----|------|
| `dashscope>=1.24.6` | 阿里云百炼 SDK |
| `python-dotenv>=1.0.0` | 环境变量加载 |
| `Pillow>=10.0.0` | 大图压缩（加速视觉推理） |

`convert_rules.py` 额外需要 `openpyxl`（未写入 requirements.txt）。

### 3.2 环境变量

```bash
# .env
ALIYUN_API_KEY=sk-xxxxxxxx
```

### 3.3 快速开始（来自 README，已扩展）

```bash
pip install -r requirements.txt

# 创建 .env 并填入 API Key
# ALIYUN_API_KEY=sk-xxxxxxxx

# 主程序双轨检测
python main.py <图片路径> <目标市场> [Listing文案]

# 示例
python main.py TC-001.jpg middle_east "Best product ever"

# 单图视觉测试（需自备 test.jpg）
python test_vl.py

# 规则检索 Demo
python knowledge/retrieval.py middle_east "女性 宗教 绿色"
```

---

## 4. 主程序 main.py

### 4.1 模型与配置常量

| 常量 | 值 | 说明 |
|------|-----|------|
| `VL_MODEL` | `qwen3-vl-plus` | 文化合规视觉模型 |
| `TEXT_MODEL` | `qwen3.7-max` | 硬合规文本模型 |
| `TARGET_SECONDS` | `15` | 性能目标（秒） |
| `API_TIMEOUT_SEC` | `45` | 单次 API 超时 |
| `MAX_RETRIES` | `2` | API 最大重试次数 |
| `RETRY_BASE_DELAY_SEC` | `1` | 重试退避基数 |
| `RAG_TOP_K_CULTURE` | `5` | 文化规则检索条数 |
| `RAG_TOP_K_HARD` | `5` | 硬合规规则检索条数 |
| `TEXT_MAX_TOKENS` | `512` | 文本模型输出上限 |
| `CULTURE_MAX_TOKENS` | `512` | 视觉模型输出上限 |
| `API_TEMPERATURE` | `0.2` | 推理温度 |
| `MAX_IMAGE_PIXELS` | `1280` | 图片长边压缩阈值 |
| `MAX_IMAGE_SIZE_MB` | `10` | 图片大小上限 |
| `PERFORMANCE_LOG` | `performance.log` | 性能日志路径 |

### 4.2 支持的目标市场

| 代码 | 说明 |
|------|------|
| `middle_east` | 中东（沙特/阿联酋） |
| `japan` | 日本 |
| `us` | 美国 |
| `eu` | 欧盟 |
| `southeast_asia` | 东南亚 |

### 4.3 异常类（代码注释）

| 类 | 说明 |
|----|------|
| `SailMirrorError` | 出海镜业务异常基类 |
| `ConfigError` | 配置错误（如 API Key 未设置） |
| `ImageValidationError` | 图片校验失败 |
| `APIError` | API 调用失败 |

### 4.4 核心函数说明（docstring 汇总）

| 函数 | 说明 |
|------|------|
| `validate_image()` | 校验图片路径、格式与大小，返回规范化 Path |
| `prepare_image_for_api()` | 压缩过大图片以加速视觉模型推理（需 Pillow） |
| `cache_validated_image()` | 校验并缓存图片路径，避免重复校验 |
| `preload_rule_index()` | 启动时预加载规则库索引 |
| `call_with_retry()` | 带超时重试的 API 调用包装 |
| `detect_culture()` | 文化合规检测（RAG 增强） |
| `detect_hard()` | 硬合规检测（RAG 增强） |
| `prefetch_rag_context()` | 并行预取双轨 RAG 上下文 |
| `run_culture_agent()` | 文化合规 Agent：RAG 检索 + 视觉模型推理 |
| `run_hard_agent()` | 硬合规 Agent：RAG 检索 + 文本模型推理 |
| `run_parallel_detection()` | 双 Agent 并行检测，返回 culture、hard、rag_meta、总耗时、分环节耗时 |
| `generate_report()` | 生成双轨报告 |

### 4.5 检测流程

1. **启动校验**：市场代码 → 图片魔数/大小 → API Key → 规则库预加载
2. **RAG 预取**：文化 + 硬合规规则并行检索，格式化为紧凑 Prompt
3. **双 Agent 并行**：
   - 文化 Agent：注入规则 → `qwen3-vl-plus` 分析图片
   - 硬合规 Agent：注入规则 → `qwen3.7-max` 分析 Listing
4. **报告输出**：控制台双轨报告 + JSON 结构 + `performance.log`

### 4.6 输出 JSON 结构（报告字段）

```json
{
  "image": "TC-001.jpg",
  "market": "middle_east",
  "listing": "...",
  "parallel": true,
  "target_seconds": 15,
  "within_target": true,
  "elapsed_seconds": 8.42,
  "rag": {
    "culture_rules": [...],
    "hard_rules": [...]
  },
  "performance": {
    "wall_time": 8.42,
    "agents": { "culture": {...}, "hard": {...} },
    "stages": {...},
    "status": "success"
  },
  "culture_compliance": {
    "risks": [{"element":"","severity":"","reason":"","rule_id":""}],
    "overall_risk": "",
    "retrieved_rules": [...]
  },
  "hard_compliance": {
    "violations": [{"type":"","content":"","severity":"","suggestion":"","rule_id":""}],
    "certifications": [],
    "overall_status": "合规/警告/违规",
    "retrieved_rules": [...]
  },
  "timestamp": "2026-08-11T17:00:00+08:00"
}
```

### 4.7 性能日志 PerformanceTracker

> 各环节耗时统计，每次运行结束写入 `performance.log`（幂等，仅写一次）。

**统计环节：**

| Stage | 说明 |
|-------|------|
| `startup.validate_market` | 市场校验 |
| `startup.validate_image` | 图片校验与压缩 |
| `startup.ensure_api_key` | API Key 检查 |
| `startup.preload_rules` | 规则库索引预加载 |
| `rag.prefetch` | 双轨 RAG 并行预取 |
| `culture.rag` / `culture.ai` | 文化 Agent 各环节 |
| `hard.rag` / `hard.ai` | 硬合规 Agent 各环节 |
| `parallel.wall_time` | 并行检测墙钟时间 |
| `report.output` | 报告打印 |
| `api.retry_wait` | API 重试等待累计 |

日志包含 `status`（success / failed / interrupted）、各环节耗时占比及 JSON 快照。

---

## 5. 规则库 RAG 模块 knowledge/retrieval.py

### 5.1 模块说明（文件头注释）

> 规则库 RAG 检索模块。从 knowledge 目录加载 JSON 规则文件，按目标市场与查询文本检索最相关规则，供合规检测 Prompt 注入使用。

### 5.2 规则字段（RULE_FIELDS）

| 字段 | 说明 |
|------|------|
| `id` | 规则编号（如 ME-001） |
| `market` | 目标市场代码 |
| `category` | 规则类别（人物着装、宗教符号、广告法等） |
| `rule` | 规则内容 |
| `severity` | 风险等级：极高 / 高 / 中 / 低 |
| `source` | 规则来源 |
| `source_level` | 来源等级：A / B / C / D |
| `source_url` | 来源链接 |

### 5.3 评分权重

```python
SEVERITY_WEIGHT = {"极高": 4, "高": 3, "中": 2, "低": 1}
SOURCE_LEVEL_WEIGHT = {"A": 4, "B": 3, "C": 2, "D": 1}
```

检索打分逻辑：

1. 按 `market` 过滤候选规则（市场索引加速）
2. 对 query 做中英文混合分词（轻量版，无第三方依赖）
3. Token 重叠 + 子串命中 + 类别匹配
4. 叠加 severity 与 source_level 权重
5. 返回 Top-K，附带 `score`

### 5.4 核心类与函数（docstring 汇总）

| 名称 | 说明 |
|------|------|
| `RuleRecord` | 单条规则数据结构 |
| `RuleLoadError` | 规则库加载失败 |
| `RuleRetriever` | 文化/硬合规规则检索器 |
| `RuleRetriever.reload()` | 重新加载 knowledge 目录下全部 JSON 规则文件 |
| `RuleRetriever.preload_index()` | 预构建市场索引与分词缓存，加速检索 |
| `RuleRetriever.retrieve()` | 检索与 query 最相关的规则，返回带 score 的字典列表 |
| `RuleRetriever.format_for_prompt()` | 将检索结果格式化为可注入 LLM Prompt 的文本（支持 `compact=True` 紧凑模式） |
| `RuleRetriever.build_context()` | 检索并直接返回 Prompt 上下文 |
| `tokenize()` | 中英文混合分词（轻量版，无第三方依赖） |
| `get_retriever()` | 获取全局单例检索器 |
| `retrieve_rules()` | 便捷函数：检索规则列表 |
| `build_rule_context()` | 便捷函数：检索并格式化为 Prompt 上下文 |

### 5.5 CLI 用法

```bash
# 内置 Demo
python knowledge/retrieval.py

# 自定义检索
python knowledge/retrieval.py middle_east "女性暴露 宗教符号 绿色"
```

---

## 6. 辅助脚本

### 6.1 convert_rules.py

将 Excel 文件 `规则库填写.xlsx` 转换为 `knowledge/culture_rules_v1.json`。

- 支持中英文列名映射（`COLUMN_ALIASES`）
- 自动跳过说明行、模板行
- 输出各市场规则条数统计

### 6.2 test_vl.py

单图文化合规视觉检测测试脚本。

- 模型：`qwen3-vl-plus`
- 默认图片：`test.jpg`
- 默认市场：中东（沙特/阿联酋）
- 输出 JSON：`risks` + `overall_risk`

### 6.3 batch_test.py

10 张测试图批量文化合规检测。

```python
# 10张测试图配置
test_cases = [
    {"id": "TC-001", "file": "TC-001.jpg", "market": "中东", "expected": "极高风险-女性着装"},
    ...
]
```

检测项：人物着装、宗教符号、饮食禁忌、颜色、数字、手势、绝对化用语等。

### 6.4 index.html

静态前端原型「跨规雷达 CompliScan」。

- 功能：上传商品图、Listing 输入、市场选择、双轨报告展示
- 技术：纯 HTML/CSS/JS，**结果为前端 Mock 数据，未连接 Python 后端**
- JS 注释分区：`State` / `DOM Elements` / `Event Listeners` / `Functions`

---

## 7. API 调用说明

### 7.1 文化合规（视觉）

```python
MultiModalConversation.call(
    api_key=API_KEY,
    model="qwen3-vl-plus",
    messages=[{
        "role": "user",
        "content": [
            {"image": "file:///path/to/image.jpg"},
            {"text": prompt_with_rag_rules},
        ],
    }],
    timeout=45,
    temperature=0.2,
    max_tokens=512,
)
```

### 7.2 硬合规（文本）

```python
Generation.call(
    api_key=API_KEY,
    model="qwen3.7-max",
    messages=[...],
    result_format="message",
    timeout=45,
    temperature=0.2,
    max_tokens=512,
    enable_thinking=False,  # 关闭思考链以加速
)
```

### 7.3 重试策略

可重试错误码：`Timeout`、`Throttling`、`ServiceUnavailable`、`InternalError` 等。

- 最多 2 次重试，退避 1s → 2s
- 重试等待计入 `api.retry_wait`

---

## 8. 规则库数据

当前文件：`knowledge/culture_rules_v1.json`

- 规则总数：**60 条**
- 覆盖市场：`middle_east`、`japan`、`us`、`eu`、`southeast_asia`
- 类别示例：人物着装、宗教符号、饮食禁忌、颜色禁忌、广告法、认证要求等

---

## 9. README 与实现差异说明

| README 描述 | 当前实现 |
|-------------|----------|
| Streamlit 前端 | 未实现；CLI + `index.html` 静态原型 |
| `.env.example` | 需手动创建 `.env` |
| 多 Agent 协同 | ✅ 双 Agent 线程池并行 |
| RAG 规则库 | ✅ `knowledge/retrieval.py` |
| 性能目标 | ✅ ≤15s，`performance.log` 追踪 |

---

## 10. 退出码

| 码 | 含义 |
|----|------|
| `0` | 成功 |
| `1` | 未预期错误 |
| `2` | 配置错误（API Key） |
| `3` | 图片校验失败 |
| `4` | 业务错误（如不支持的 market） |
| `130` | 用户中断（Ctrl+C） |

---

*文档生成依据：README.md、main.py、knowledge/retrieval.py、convert_rules.py、test_vl.py、batch_test.py、index.html 中的注释与实现。*
