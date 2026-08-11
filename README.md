# 🚀 出海镜 SailMirror — AI跨境合规检测系统

**AI 双轨合规检测：硬合规（能不能卖）+ 文化合规（卖不卖得动）**

面向跨境卖家的上新合规检测器——上传商品图和 Listing，10 秒输出目标市场的双轨风险报告与整改建议。

---

## 核心亮点

- **10/10 测试用例全部命中** — 覆盖中东、美国、日本、东南亚等典型文化禁忌与硬合规场景，批量检测 100% 命中
- **60 条规则库，覆盖 5 大市场** — 中东 20 + 美国 20 + 欧盟 10 + 日本 5 + 东南亚 5，结构化入库，支持 RAG 检索
- **多 Agent 架构** — Qwen3-VL-Plus 负责视觉文化合规，Qwen3-Max 负责文案硬合规，并行检测、结果聚合
- **规则可溯源** — 每条判定标注 A/B/C/D 来源分级（官方法规 / 平台政策 / 行业案例 / 文化惯例），不是黑盒结论

---

## 在线 Demo

👉 **https://sailmirror-production.up.railway.app/**

上传商品图、选择目标市场，体验完整检测流程与双轨报告 UI。

---

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置 API Key

在项目根目录创建 `.env` 文件：

```
ALIYUN_API_KEY=sk-your-api-key
```

### 3. 运行双轨检测（main.py）

```bash
# 文化合规检测
python main.py TC-001.jpg middle_east

# 文化 + 硬合规双轨检测
python main.py TC-004.jpg us "Best Bluetooth Headphones #1 Quality"
```

**市场代码：** `middle_east` / `japan` / `us` / `eu` / `southeast_asia`

### 4. 批量测试（10 张用例）

```bash
python batch_test.py
```

---

## 技术栈

| 层级 | 技术 |
|------|------|
| 语言 | Python 3.12 |
| 视觉模型 | 阿里云百炼 Qwen3-VL-Plus |
| 文本模型 | 阿里云百炼 Qwen3-Max |
| 规则库 | JSON + RAG 检索（`knowledge/culture_rules_v1.json`） |
| 前端 | HTML Demo / Streamlit（规划中） |
| 云平台 | 阿里云百炼 DashScope API |

---

## 项目结构

```
sailmirror/
├── main.py              # 双 Agent 主流程（文化 + 硬合规）
├── batch_test.py        # 10 张测试图批量检测
├── convert_rules.py     # Excel 规则库 → JSON 转换
├── knowledge/           # 60 条结构化规则库
├── index.html           # 前端 Demo 页面
└── TC-001~010.jpg       # 测试用例图片
```

---

## 团队

**5 人团队** | 队长：吴宇 | AI + 跨境黑客松参赛作品

GitHub：https://github.com/xuehualiange/sailmirror

---

**让每一个中国商品，在出海前都能通过文化与法规的双重审查。**
