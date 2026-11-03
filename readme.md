# OmniPilot 面向银行业监管制度与统计报表的可信 RAG 问答系统

面向银行业监管制度与统计报表的**可信 RAG 问答系统**。基于 LangGraph 多智能体协作框架，集成 Milvus 向量检索、BM25 关键词检索、RRF 融合与 Reranker 重排序，支持 Word / Excel / PDF 多源异构文档的精准检索、跨文件多跳推理与可验证问答。

![Python](https://img.shields.io/badge/Python-3.10%20%7C%203.11-3776AB?logo=python&logoColor=white)
![Node](https://img.shields.io/badge/Node.js-%E2%89%A518-339933?logo=node.js&logoColor=white)
![Docker](https://img.shields.io/badge/Docker-Compose%20v2-2496ED?logo=docker&logoColor=white)
![Status](https://img.shields.io/badge/status-active-success)

---

## 评测结果（300 题）

> 评测时间：**2026-11-03**（去标题泄漏 + BM25F 双字段版本）· 300 题均衡测试集 · 端到端真实调用

| 指标 | 本次（去泄漏） | 上一版（含 `[Source:]` 前缀） | 变化 |
|------|------|------|------|
| **整体准确率** | **298 / 300 = 99.33%** | 298 / 300 = 99.33% | 持平 |
| 解析失败 / API 错误 | 0 / 0 | 0 / 0 | — |
| **Recall@5**（文档级） | 284 / 300 = **94.67%** | 284 / 300 = 94.67% | 持平 |
| **MRR@10** | **0.9467** | 0.9467 | 持平 |
| **P50 端到端延迟** | **20.50 s** | 27.35 s | **−25%** |
| **P95 端到端延迟** | **35.29 s** | 72.75 s | **−51%** |
| 平均端到端延迟 | **23.00 s** | 35.98 s | −36% |
| 首 token P50 / P95 | 2.24 s / 3.51 s | 2.33 s / 3.43 s | 基本持平 |
| 总耗时 | 115.0 min | 179.9 min | −36% |
| 总 Token | 2,995,620 input / 831,204 output | 2,702,698 / 859,632 | +11% input |
| 总成本 / 单题成本 | ¥3.4679 / ¥0.011560 | ¥3.3691 / ¥0.011230 | +3% |

**关键结论**

- **去掉"标题泄漏"后准确率没有下降**：说明准确率不是靠"文档名出现在每个块里"撑的，Planner 的文档定位本来就是合法线索。
- **延迟反而腰斩**：BM25F 用 numpy 倒排打分（单次 4–6 ms，替代"扫 11 万候选再排序"），P95 从 72.75 s 降到 35.29 s。
- 成本的 +3% 来自输入 token 增加（+11%），与检索变更无关，属于 rerank 返回条数与上下文长度的波动。

**分难度准确率**

| 难度 | 题数 | 答对 | 准确率 |
|------|:----:|:----:|:------:|
| easy | 102 | 101 | 99.02% |
| medium | 99 | 99 | 100.00% |
| hard | 99 | 98 | 98.99% |

**分文档类型准确率**

| 类型 | 题数 | 答对 | 准确率 |
|------|:----:|:----:|:------:|
| Word | 100 | 100 | 100.00% |
| PDF | 100 | 99 | 99.00% |
| Excel | 100 | 99 | 99.00% |

**错题分析**

| 题目 | 难度 / 类型 | 期望 | 实际 | 检索是否命中 |
|------|-------------|:----:|:----:|:------------:|
| Q019 | easy / Excel | C | A | ✅ gold 在 Top-5 |
| Q251 | hard / PDF | C | X（拒答） | ✅ gold 在 Top-5 |

两题仍是"检索已命中、生成/判断阶段出错"，误差来源与上一版一致：表格读数与个别条款的选项判别。
上一版的 Q162（Word 误拒）本次通过，新增 Q251（PDF 误拒），说明拒答阈值对 PDF 长文档偏紧。

**复现方式**

```bash
python valuation/evaluate_all.py --start 0 --end 300
```

- 评测模型：DeepSeek（由 `.env` 的 `LLM_MODEL` 指定，默认 `deepseek-v4-flash`）
- 嵌入模型：`Qwen/Qwen3-Embedding-0.6B`
- 温度：`0.0`
- 结果写入 `valuation/reports/test_300_result.json` 与 `test_300_report.md`（该目录默认不入库，需本地重新生成）
- 结果会随模型版本、API Key 配额、机器性能与 Milvus 索引状态变化，建议在相同配置下对比

**检索指标的两个口径**

- `evaluate_all.py` 报的 Recall@5 / MRR@10 是**定位之后**的指标：题库 95% 的题引用文档名，
  Planner 会把检索范围锁到 1–3 份文档，金标准几乎总排第 1，所以 MRR 与 Recall 会重合（都是 0.9467）。
  它衡量的是"定位 + 靶向检索"，不能当成"全库检索能力"。
- 要量全库检索（不加 `file_name` 过滤），用 `valuation/eval_retrieval_only.py`：
  向量 + BM25F 双路召回 → RRF → rerank，**不调用 LLM**，300 题约 15 分钟。
  2026-11-03 实测（300 题全量）：**Recall@1 88.33%、Recall@5 94.00%、Recall@10 94.33%、MRR@10 0.9081**，
  16 题连前 24 条都召不回来（与 evaluate_all 报告的 16 道检索失败完全一致）。
  这组数字才是真正的检索能力，也说明 MRR 现在与 Recall 拉开了差距（0.9081 vs 0.94）。

```bash
python valuation/eval_retrieval_only.py --q 300        # 全库检索评测
python valuation/eval_retrieval_only.py --no-rerank    # 看 rerank 的增量
```

---

## 核心功能

| 功能 | 说明 |
|------|------|
| 多源文档解析 | Word 章节层级、Excel 表头检测与行列切分、PDF 文本 + OCR |
| 混合检索 | Milvus 向量 + BM25 关键词 + RRF 融合 + Reranker 重排序 |
| 元数据过滤 | 文件名、年份、发文机关、指标、时间维度、条款号等多字段过滤 |
| 多跳推理 | Planner 问题拆分与子任务规划，跨文件证据聚合 |
| 反幻觉 | 文档来源校验、OOKB 拒答、约束生成 |
| 多模型切换 | DeepSeek / 豆包 / MiniMax / Kimi / 自定义提供商 |
| 用户与权限 | JWT 认证 + 管理员 / 普通用户角色 |
| 可视化评测 | 前端批量测试面板 + 命令行 300 题评测脚本 |
| 可观测性 | Prometheus 指标 + Grafana 面板 |

---

## 系统架构

```mermaid
flowchart LR
    U[用户 / 浏览器] --> FE[React + TypeScript 前端]
    FE --> API[FastAPI 后端]
    API --> PLAN[Planner 规划]
    PLAN --> RAG[RAG Agent]
    RAG --> V[(Milvus 向量库)]
    RAG --> B[(BM25 关键词索引)]
    RAG --> RR[Reranker]
    RAG --> LLM[DeepSeek / 豆包 / MiniMax / Kimi]
    RAG --> SUM[Summarizer 生成与自检]
    SUM --> API
    API --> PG[(PostgreSQL 用户与会话)]
    API --> PROM[Prometheus / Grafana]
```

---

## 环境要求

| 组件 | 版本 | 说明 |
|------|------|------|
| Python | **3.10 / 3.11** | 后端运行环境；`pandas==1.5.3` 在 3.12+ 无预编译包，不建议使用 |
| Node.js | ≥ 18 | 前端构建；已在 Node 24 下验证 |
| Docker | ≥ 24 | 一键部署基础设施与服务 |
| Docker Compose | v2 | 使用 `docker compose`，不再使用旧的 `docker-compose` |
| 内存 / 磁盘 | 8 GB+ / 10 GB+ | Milvus、镜像与知识库索引需要空间 |
| Tesseract OCR | ≥ 5.0 | 可选；PDF 中文 OCR 需要 `chi_sim` |
| LibreOffice | ≥ 7.0 | 可选；解析 `.doc` 老格式，Docker 镜像内已安装 |
| Microsoft Word + pywin32 | — | 可选；Windows 下转换 `.doc` 的另一种方式 |

> 如果本机是 Python 3.12 / 3.13，建议直接用 Docker 方式，或在 3.10 / 3.11 虚拟环境中安装依赖。

---

## 快速开始

### 方式一：Docker 一键部署（推荐）

**0. 前置**

- 安装并启动 Docker Desktop（Windows / macOS）或 Docker Engine（Linux）
- 国内网络建议先配置镜像加速：Docker Desktop → Settings → Docker Engine，加入：

```json
{
  "registry-mirrors": [
    "https://docker.m.daocloud.io",
    "https://hub-mirror.c.163.com",
    "https://mirror.baidubce.com"
  ]
}
```

**1. 克隆并配置**

```bash
git clone https://github.com/WQNF/rag-ominipilot-system.git
cd rag-ominipilot-system
```

Windows PowerShell：

```powershell
Copy-Item .env.example .env
notepad .env
```

macOS / Linux：

```bash
cp .env.example .env
```

至少填写：

```dotenv
SILICON_FLOW_API_KEY=你的硅基流动密钥   # 必填：向量嵌入
deepseek_API_KEY=你的DeepSeek密钥      # 必填：默认 LLM
JWT_SECRET_KEY=至少32位随机字符串       # 强烈建议修改
```

**2. 启动全部服务**

```bash
docker compose -f docker/docker-compose.yml up -d --build
docker compose -f docker/docker-compose.yml ps
```

首次构建会安装 LibreOffice、Tesseract、中文字体并打包 500 份文档，通常需要 10–20 分钟。`postgres` 显示 `healthy`、`milvus` / `backend` 为 `running` 后再继续。

**3. 初始化数据库与知识库（在 backend 容器内执行）**

```bash
docker compose -f docker/docker-compose.yml exec backend python tools/init_db.py
docker compose -f docker/docker-compose.yml exec backend python tools/init_milvus.py --mode all
```

- `--mode all` 依次构建 Word、PDF、Excel 知识库，首次约 **3 小时**（Excel 约 10.9 万块，主要耗时在嵌入 API）
- 只想先跑通问答，可只建 Word：`--mode word`；之后再补 `--mode pdf`、`--mode excel`
- 初始化期间请保持终端连接；中断后重新执行即可（脚本按文件清理旧块，不会重复堆积）

**4. 访问**

| 服务 | 地址 |
|------|------|
| 前端 | http://localhost |
| 后端 API 文档 | http://localhost:8000/docs |
| Prometheus | http://localhost:9090 |
| Grafana | http://localhost:3000（默认 admin / admin） |

默认账号（首次登录后请修改密码）：

| 角色 | 用户名 | 密码 |
|------|--------|------|
| 管理员 | `admin` | `admin123` |
| 普通用户 | `zhangwei` | `secret123` |

**常用 Docker 命令**

```bash
# 查看日志
docker compose -f docker/docker-compose.yml logs -f backend

# 改代码后重建后端
docker compose -f docker/docker-compose.yml up -d --build backend

# 停止（保留数据卷）
docker compose -f docker/docker-compose.yml down

# 停止并清空数据库 / Milvus 数据（谨慎）
docker compose -f docker/docker-compose.yml down -v
```

> Docker 模式与下面的本地开发模式**不要同时启动**，否则 8000 端口会冲突。

---

### 方式二：本地开发

**1. 创建 Python 环境（3.10 / 3.11）**

Windows PowerShell：

```powershell
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

macOS / Linux：

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
pip install -r requirements.txt
```

**2. 配置 `.env`**

```powershell
Copy-Item .env.example .env
notepad .env
```

macOS / Linux 使用 `cp .env.example .env`。

**3. 只启动基础设施**

```bash
docker compose -f docker/docker-compose.yml up -d postgres etcd minio milvus
```

此时 PostgreSQL 暴露在 `localhost:5432`，Milvus 暴露在 `localhost:19530`，与 `.env` 默认值一致。

**4. 初始化数据库与知识库**

```bash
python tools/init_db.py
python tools/init_milvus.py --mode all
```

**5. 启动后端**

```bash
uvicorn api.main:app --host 0.0.0.0 --port 8000 --reload
```

**6. 新开终端，启动前端**

```bash
cd frontend-react
npm install
npm run dev
```

访问 **http://localhost:5173**。Vite 已代理 `/token`、`/chat`、`/files`、`/register`、`/admin`、`/settings`、`/knowledge`、`/test`、`/users`、`/metrics` 到 `127.0.0.1:8000`。

> 如果修改了 `vite.config.ts`，需要重启 `npm run dev` 才会生效。

---

## 配置说明

`.env` 已被 `.gitignore` 排除，请勿提交真实密钥。

| 变量 | 必填 | 说明 |
|------|:----:|------|
| `SILICON_FLOW_API_KEY` | 是 | 硅基流动密钥，用于文档嵌入 |
| `SILICON_FLOW_API_BASE` | 否 | 默认 `https://api.siliconflow.cn/v1` |
| `EMBEDDING_MODEL` | 否 | 默认 `Qwen/Qwen3-Embedding-0.6B` |
| `deepseek_API_KEY` | 是 | 默认 LLM 密钥 |
| `deepseek_API_BASE` | 否 | 默认 `https://api.deepseek.com` |
| `LLM_MODEL` | 否 | 默认 `deepseek-v4-flash` |
| `JWT_SECRET_KEY` | 建议 | JWT 签名密钥，生产环境务必改成 32 位以上随机串 |
| `MILVUS_HOST` / `MILVUS_PORT` | 否 | 本地默认 `localhost:19530`；Docker 内由 Compose 覆盖为 `milvus:19530` |
| `POSTGRES_HOST` / `POSTGRES_PORT` | 否 | 本地默认 `localhost:5432`；Docker 内由 Compose 覆盖为 `postgres:5432` |
| `POSTGRES_USER` / `PASSWORD` / `DB` | 否 | 默认 `omnipilot` / `omnipilot123` / `omnipilot` |
| `RETRIEVAL_TOP_K` | 否 | 默认 80 |
| `RETRIEVAL_NPROBE` | 否 | 默认 16 |
| `RETRIEVAL_THRESHOLD` | 否 | 默认 0.3 |
| `LLM_TEMPERATURE` | 否 | 默认 0.0 |
| `PDF_OCR_IMAGES` / `PDF_OCR_FULL_PAGE` | 否 | 默认 `true`；不需要 OCR 时设为 `false` 可明显提速 |
| `PDF_OCR_LANG` | 否 | 默认 `chi_sim+eng` |
| `TESSERACT_CMD` | 否 | `tesseract` 不在 PATH 时填写绝对路径 |
| `LIBREOFFICE_PATH` | 否 | `soffice` 不在默认路径时填写绝对路径 |
| `xiaomi_API_KEY` / `xiaomi_API_BASE` | 否 | 可选提供商 |

**模型提供商**

| 提供商 | API Base | 示例模型 |
|--------|----------|----------|
| DeepSeek | `https://api.deepseek.com` | `deepseek-v4-flash` / `deepseek-v4-pro` |
| 豆包（Doubao） | `https://ark.cn-beijing.volces.com/api/v3` | `doubao-seed-evolving` 等 |
| MiniMax | `https://api.minimaxi.com/anthropic` | `MiniMax-M2.7` 等 |
| Kimi | `https://api.moonshot.cn/v1` | `kimi-k2.7-code-highspeed` 等 |
| 自定义 | 任意 OpenAI 兼容地址 | 自行填写 |

密钥获取入口：

- DeepSeek：https://platform.deepseek.com
- 硅基流动：https://www.siliconflow.cn
- 火山方舟（豆包）：https://console.volcengine.com/ark
- MiniMax：https://platform.minimaxi.com
- Moonshot（Kimi）：https://platform.moonshot.cn

---

## 数据与知识库

| 类型 | 文件数 | 说明 |
|------|:------:|------|
| Excel | 389 | 监管统计报表（`.xls` / `.xlsx`） |
| PDF | 45 | 监管制度、年报、附件 |
| Word | 66 | 32 份 `.doc` + 34 份 `.docx` |
| **合计** | **500** | 首次全量嵌入约 3 小时 |

- Word 约 1,200 个知识块，PDF 约 1,000 个，Excel 约 10.9 万个
- 知识库重建：`python tools/init_milvus.py --mode word|pdf|excel|all`
- `bm25_index.pkl` 首次查询时自动构建，文件约 200 MB，已加入 `.gitignore`
- 删除某类旧数据后重建即可，不会影响其他类型；重建会先按文件名删除旧块
- `MilvusRetriever.insert()` 已改为**批量写入**（默认 500 条/次）：10.7 万条从约 40 分钟降到分钟级，
  峰值内存也从 4 GB+ 降到单个批次的规模

**BM25 中文分词（jieba + 领域词典）**

BM25 的切词统一走 `tools/bm25_tokenizer.py`：jieba 精确模式 + 领域词典 + 条款号通道。

- 词典 **自动生成**：从 doc metadata 的指标名、发文机关、章节、文档标题与《》制度名抽取，
  清洗去重后得到约 620 条，另含 50 条人工种子术语；落盘为 `bm25_domain_dict.txt`（可入库、可版本化）
- 条款号（`第X条`）单独成 token，数字/百分比/英文缩写保持整词
- 索引文件写入 tokenizer 签名（版本 / backend / 词典 hash）。签名不一致时**自动停用 BM25 这一路**并提示重建，
  避免用错位 token 给出"看起来正常、实际有毒"的排序
- 重建索引：`python valuation/rebuild_bm25.py --eval-q 80`（会先备份旧索引，并打印新旧分词在测试集上的命中对比）
- 回滚到正则切分：`BM25_TOKENIZER=regex`（需配合 `--allow-regex` 重建）

**BM25F 双字段检索（标题/元数据 + 正文）**

`tools/bm25f.py` 把 chunk 拆成两个字段分别打分：`title`（文件名/章节/指标名/发文机关）与
`body`（去掉 `[Source: …]`、`| Chapter: …` 前缀后的正文），字段内先做长度归一化、乘权重合并成
加权词频，**最后只饱和一次**——这是 BM25F 与"两个 BM25 分数相加"的本质区别。

- 构建：`python valuation/build_bm25f.py`（流式切词+建倒排，11 万块约 10–15 分钟；产物 `bm25_index_f.pkl`）
- 检索器会优先加载 `bm25_index_f.pkl`，没有则回退 `bm25_index.pkl`（可用 `BM25_INDEX_PATH` 指定）
- 为什么要拆：每个 chunk 正文原本带着 `[Source: 文件名]` 前缀，等于把"标题"这个强信号在正文里又算了一遍。
  题目里 95% 引用文档名，于是 BM25 退化成"标题精确匹配器"（旧版 @1 86%、@5 90%）。
  拆开字段后标题走独立通道、正文去掉前缀，@1 升到 90.3%、@10 到 93.3%

三路对比（`data/test_dataset/all.py` 前 300 题，文档级命中，2026-11-03）：

| 配置 | Recall@1 | Recall@5 | Recall@10 |
|------|:--------:|:--------:|:---------:|
| 旧·正则单字段（含 Source 前缀） | 258 (86.0%) | 271 (90.3%) | 276 (92.0%) |
| jieba·单字段（含 Source 前缀） | 231 (77.0%) | 280 (93.3%) | 280 (93.3%) |
| BM25F 双字段（正文带前缀，旧版） | 271 (90.3%) | 275 (91.7%) | 280 (93.3%) |
| **BM25F 双字段（正文干净，本次）** | **276 (92.0%)** | **284 (94.7%)** | **284 (94.7%)** |

注：这三组是**文档级**指标（金标准文件名是否出现在 top-k 的来源里），不是片段级；
且 chunk 正文携带 Source 前缀本身会抬高检索指标，是已知的"标题泄漏"，BM25F 的 body 字段已去掉前缀。

**入库只存干净正文（标题不再进正文）**

旧实现把 `[Source: 文件名] | Chapter: … | Clause: …` 拼进 chunk 正文后再入库，问题有三个：
向量与 BM25 都能靠"标题命中"取巧（题目里 95% 引用文档名）、标题在正文和 metadata 里被算两遍、
以及评估指标被系统性抬高。

现在改为：

- **入库只存干净正文**（`tools/init_milvus.py`、`tools/user_knowledge.py`），归属信息只留在 metadata；
- **展示层还原**：`tools/context_format.ensure_header(text, meta)` 在检索返回时补回 `[Source: …]` 头部，
  保证引用、`sources` 字段与总结 Agent 的引用规则不受影响；对旧数据（正文已带头部）幂等；
- **PDF 例外**：页码（Page）在 Milvus 里没有对应字段，只能留在头部（45 份 PDF、约 1k 块，占比 <1%）。

重建顺序（改完代码后必须整体重建一次，否则库里还是旧文本）：

```bash
python tools/init_milvus.py --mode all      # 重新嵌入 + 入库，约 3 小时
python valuation/rebuild_bm25.py            # 单字段 BM25（从 Milvus 读干净正文）
python valuation/build_bm25f.py             # BM25F 双字段
```




**`.doc` 老格式**

代码会自动把 `.doc` 转成临时 `.docx` 再解析：

1. Windows 且安装了 Word：`pip install pywin32`（未包含在 `requirements.txt`）
2. 否则安装 LibreOffice，并在 `.env` 设置 `LIBREOFFICE_PATH`（Docker 镜像已内置 `libreoffice-writer`）

**OCR 说明**

- Docker 镜像已安装 `tesseract-ocr`、`tesseract-ocr-chi-sim`、`tesseract-ocr-chi-tra`
- Windows 仓库内置 `OCR/tesseract.exe`，但 `OCR/tessdata` 只有 `eng` 与 `osd`；中文 OCR 需额外把 `chi_sim.traineddata` 放入 `OCR/tessdata/`，并设置：

```dotenv
TESSERACT_CMD=D:\你的路径\大赛项目最终版\OCR\tesseract.exe
```

- 不需要 OCR 时，将 `PDF_OCR_IMAGES=false`、`PDF_OCR_FULL_PAGE=false` 可显著缩短建库时间
- Word 初始化时出现少量 OCR 失败日志属于正常现象（Word 解析不依赖 OCR）

---

## 功能使用说明

### 1. 登录与注册

系统内置默认账号，无需注册即可登录；也可注册新账号。

| 角色 | 用户名 | 密码 | 权限 |
|------|--------|------|------|
| 管理员 | `admin` | `admin123` | 用户管理 + 全部功能 |
| 普通用户 | `zhangwei` | `secret123` | 问答 + 知识库管理 |

- **登录**：输入用户名和密码，获取 JWT Token
- **注册**：用户名、密码（≥ 6 位且不能为纯数字），可选姓名、部门、邮箱
- **退出**：侧边栏底部退出按钮

### 2. 对话问答

- **发起提问**：底部输入框，按 Enter 发送
- **流式响应**：思考过程、答案正文、来源引用、耗时统计
- **来源引用**：标注文件名、页码、章节、检索分数，可展开原文预览
- **文档筛选**：侧边栏勾选知识库文档，问答仅检索选定范围
- **会话统计**：顶部显示知识库文档数量与更新时间

### 3. 会话管理

- 新建、重命名（右键 / 双击）、删除、置顶
- 自动按时间分组：置顶 / 今天 / 7 天内 / 更早
- 会话保存在本地，刷新不丢失

### 4. 知识库管理

- **公共知识库**：所有用户共享，按 Word / PDF / Excel 分类
- **个人知识库**：仅本人可见，支持上传、删除、下载
- **文档筛选**：勾选后问答仅检索选中文档；标签可单个移除或全部清除

### 5. 模型切换

顶部模型选择器可一键切换提供商，切换后立即生效，无需刷新。

### 6. LLM 参数设置

侧边栏设置面板支持：

- 提供商 / 模型选择（含自定义模型名）
- 各提供商独立 API Key
- 温度 0.0–2.0
- Top-K、nprobe、相似度阈值

### 7. 批量测试

- 从下拉列表选择 `data/test_dataset/` 下的数据集
- 实时进度、当前题目 ID
- 支持停止 / 从中断点继续
- 测试历史：数据集、时间、进度、正确 / 错误 / 异常数

### 8. 管理员用户管理

仅管理员可见：用户列表、新增、编辑（密码 / 角色 / 姓名 / 部门 / 邮箱 / 激活状态）、删除。

### 9. 主题切换

顶部 🌙 / ☀️ 按钮切换亮色 / 暗色模式，偏好自动保存。

---

## 项目结构

```text
├── agents/                 # Planner / Dispatcher / RAG / Summarizer
├── api/                    # FastAPI 后端、JWT 认证、用户管理
│   └── dependencies/
├── config/                 # Prompt 模板与文档配置
├── core/                   # LangGraph 工作流、LLM 客户端、状态管理
├── data/                   # 知识库文档（excel / word / pdf，共 500 份）
│   ├── excel/
│   ├── pdf/
│   ├── word/
│   │   ├── doc/            # .doc 老格式
│   │   └── docx/
│   ├── test_dataset/       # 300 题测试集与分难度子集
│   └── test_outside_dataset/  # 拒答题测试集
├── docker/                 # Docker Compose（PostgreSQL + Milvus + 监控）
├── frontend-react/         # React + TypeScript + Vite 前端
├── nginx/                  # 生产环境反向代理配置
├── OCR/                    # Windows 内置 Tesseract（可选）
├── skills/                 # 扩展技能占位
├── tools/                  # 解析、检索、嵌入、重排序、初始化脚本
├── valuation/              # 评测脚本；reports/ 为本地生成结果
├── Dockerfile              # 后端镜像
├── requirements.txt        # Python 依赖
└── readme.md
```

---

## API 接口

| 端点 | 方法 | 说明 |
|------|------|------|
| `/chat` | POST | 非流式问答 |
| `/chat/stream` | POST | 流式问答（SSE，需登录） |
| `/token` | POST | 用户登录 |
| `/register` | POST | 用户注册 |
| `/users/me` | GET | 当前用户信息 |
| `/settings` | GET/POST | LLM 配置管理 |
| `/admin/users` | GET/POST | 用户列表 / 新增用户 |
| `/admin/users/{username}` | PUT/DELETE | 编辑 / 删除用户 |
| `/knowledge/docs` | GET | 公共知识库文档列表 |
| `/knowledge/upload` | POST | 上传文档到公共知识库 |
| `/knowledge/personal/docs` | GET | 个人知识库文档列表 |
| `/knowledge/personal/upload` | POST | 上传文档到个人知识库 |
| `/knowledge/personal/docs/{document_id}` | DELETE | 删除个人文档 |
| `/knowledge/personal/docs/{document_id}/file` | GET | 下载个人文档 |
| `/knowledge/stats` | GET | 知识库统计 |
| `/files/{file_path:path}` | GET | 文件下载 / 静态资源 |
| `/test/datasets` | GET | 测试数据集列表 |
| `/test/run` | POST | 批量测试（实时进度） |
| `/test/stop` | POST | 停止测试 |
| `/metrics` | GET | Prometheus 监控指标 |

---

## 常见问题

**Q1：Docker 启动后登录失败 / 后端连接不上数据库或 Milvus**

先确认容器状态：

```bash
docker compose -f docker/docker-compose.yml ps
docker compose -f docker/docker-compose.yml logs --tail=100 backend
```

Compose 已为 backend 注入 `MILVUS_HOST=milvus`、`POSTGRES_HOST=postgres`。如果手动改过 `.env`，不要在里面把这两个值改回 `localhost`。

**Q2：`pip install -r requirements.txt` 在 Windows 失败**

- 确认 Python 是 3.10 / 3.11，不要用 3.12+
- `psycopg2` 已替换为 `psycopg2-binary`，无需本地 PostgreSQL 开发包
- 如果仍失败，单独执行 `pip install psycopg2-binary==2.9.12`

**Q3：`init_milvus.py --mode all` 太慢或中途断开**

- 全量约 3 小时，建议分类型执行：`--mode word` → `--mode pdf` → `--mode excel`
- 确认 `SILICON_FLOW_API_KEY` 有余额；429 限流时脚本会自动退避重试
- 中断后重新执行即可，已完成的文件会先删旧块再写入

**Q4：PDF 中文 OCR 不生效**

- Docker 镜像已带 `chi_sim`；本地 Windows 需把 `chi_sim.traineddata` 放到 `OCR/tessdata/`
- 在 `.env` 设置 `TESSERACT_CMD` 指向 `tesseract.exe` 绝对路径
- 扫描版 PDF 才需要 OCR；普通文本 PDF 可关闭 OCR 提速

**Q5：`.doc` 解析失败**

- Windows：安装 Word 后 `pip install pywin32`，或安装 LibreOffice
- Linux / Docker：安装 `libreoffice-writer`；Docker 镜像已内置
- 设置 `LIBREOFFICE_PATH` 指向 `soffice.exe`

**Q6：`npm run dev` 打开后知识库 / 测试页面 404**

Vite 代理已补齐 `/knowledge`、`/test`、`/users`、`/metrics`。如果你是在修改配置前启动的，重启 `npm run dev` 即可。

**Q7：端口被占用**

| 端口 | 服务 |
|------|------|
| 80 | Docker 前端 |
| 8000 | 后端 |
| 5173 | Vite 开发服务器 |
| 5432 | PostgreSQL |
| 19530 / 9091 | Milvus |
| 9090 | Prometheus |
| 3000 | Grafana |

前端 80 被占用时，可把 compose 中的 `"80:80"` 改为 `"8080:80"`，然后访问 http://localhost:8080。

**Q8：Docker 内存不足 / Milvus 反复重启**

Milvus 建议至少分配 4 GB 内存，整机建议 8 GB+。在 Docker Desktop → Resources 中调高内存后重启。

---

## 安全与合规

- `.env`、`bm25_index.pkl`、`valuation/reports/`、`api/user_settings.json`、`data/conversations.json` 均已加入 `.gitignore`，不要提交真实密钥或用户数据
- 生产环境务必修改默认账号密码和 `JWT_SECRET_KEY`
- 设置面板中的 API Key 保存在本地 `api/user_settings.json`（已忽略），请勿打包分发
- 仓库中的示例文档来自公开监管制度与统计报表；如替换为内部数据，请先完成脱敏并确认授权
- 对外部署时建议在 Nginx 层增加 HTTPS、访问控制与速率限制

---

## 致谢

- [LangGraph](https://github.com/langchain-ai/langgraph) / [LangChain](https://github.com/langchain-ai/langchain)
- [Milvus](https://milvus.io/) / [PyMilvus](https://github.com/milvus-io/pymilvus)
- [FastAPI](https://fastapi.tiangolo.com/) / [React](https://react.dev/) / [Vite](https://vitejs.dev/)
- [Tesseract OCR](https://github.com/tesseract-ocr/tesseract) / [LibreOffice](https://www.libreoffice.org/)
