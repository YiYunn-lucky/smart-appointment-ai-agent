# 安居家电售后智能客服 + 上门报修预约 Agent

一个演示级「企业售后智能客服」多 Agent 系统：客户通过自然语言完成**家电报修登记 → 工程师上门预约 → 工单状态跟踪 → 保修查询 → 投诉转人工**的完整售后闭环；客服运营侧配套**报修工单管理、工程师排班、知识库管理、售后回访、操作审计**等后台页面。

## 核心能力

面向客户（聊天窗口）：

- **报修登记与上门预约**：自动识别品类/故障/地址/手机号/期望上门时间，逐项追问补齐；按品类+故障技能与区域软偏好匹配工程师；指定工程师没空时推荐相似工程师并征求确认
- **保修查询**：按手机号查询名下订单，返回每台家电在保/超保状态与保修截止日
- **工单进度查询**：报修单号（AX…）随时查状态、工程师与上门时间
- **投诉转人工**：登记诉求并承诺 30 分钟内回电，落库为转人工记录
- **售后知识问答**：保修政策、收费标准、故障自查等通过知识库 RAG 检索回答
- **多会话隔离 + 分层记忆**：每会话独立运行时与最近 10 轮短期窗口（互不串场，刷新/重启可续谈）；报修与咨询要点沉淀为长期记忆，老客户再对话按 `0.6 语义 + 0.3 时效 + 0.1 重要度` 召回 Top-5 注入客服背景
- **主管 ReAct 工具化**：子 Agent 与确定性流程登记为主管「工具」（注册表带风险/模型分级元数据）；主管每轮 观察→选工具→执行→再观察：分类即工具选择、工具清单注入分类提示词、子任务无关信号触发重新规划（带轮次上限）
- **模型分级（双通道）**：`main` 通道承接话术/RAG 回答生成，`fast` 通道（本地小/轻量模型）承接任务分类、咨询相关性判定、槽位抽取等高频结构化短调用——`.env` 配 `LLM_FAST_MODEL` 即可启用，未配置自动透明回退 main 模型
- 服务窗口统一 **9:00–18:00**，默认上门维修窗口 2 小时

面向运营（后台页面）：

- `/tickets` 报修工单管理：状态筛选、派单（校验工程师档期）、开工/完成/取消，转人工记录
- `/engineers` 工程师管理：品类技能、服务区域、今日忙闲
- `/engineer_schedules` 今日排班网格（9:00-18:00）
- `/knowledge` 知识库管理：条目增删改查与语义搜索
- `/follow_ups` 售后回访：按客户手机号生成保养/保修到期/满意度回访话术，附工程师可约时段
- **AutoDream 离线沉淀**：累计 ≥5 次会话且首次到最近一次活动跨度 ≥24h 的老客户，后台定时把分散的行为事件**增量回放**成画像——偏好置信度逐条累计、同维度未再确认的旧偏好**减半降权**（偏好漂移自动淡出）、画像摘要写入长期记忆（向量/内容去重，每人至多一条可召回画像）；任务锁 + 事件ID checkpoint 保证幂等、中断可续跑
- **权限与安全治理**：全链路操作审计落库（`audit_logs`：主管工具选择/报修建单/派单流转/转人工登记/知识库变更/AutoDream 沉淀，含操作方、场景、动作、对象、风险分级、IP）；风险分级纯策略（read/confirm/write × 工具白名单，与主管注册表同源测试锁一致）；后台写接口统一幂等（`Idempotency-Key` 头，成功记录占用幂等键、同键重放短路返回首次结果、失败不占位可重试）；`/audit_logs` 审计页按场景/风险/结果过滤可查

## 系统架构

五层架构，依赖方向自上而下单向：

```text
Web 层     web/（routes.py + templates）   页面渲染、SSE 流式聊天接口
API 层     api/                            各业务 REST 接口（薄层，仅参数校验与转发）
Agents 层  agents/                         多 Agent 编排（业务大脑，唯一允许调 LLM 的层）
Services 层 services/                      业务逻辑与种子数据（工单/订单/知识检索/行为分析）
DB 层      db/                             模型 ORM / 仓储 / 路由，统一会话与建表
```

- Web → API → Agents 直连（FastAPI 进程内）
- Agents 与 Services 通过依赖注入的仓储访问 DB，**Agents 不直接写 SQL**
- Services 间可复用（如售后顾问查询工单状态复用 TicketService）

### 允许的调用方向

```
Web → API → Agent → Service → Repository → ORM
Web → API → Service → Repository → ORM（管理页等纯数据场景）
```

### 禁止的调用方式

- Web / API 直接调 LLM（模型访问集中在 Agents 层）
- Agents / Services 直接 import 数据库 Session（一律经仓储注入）
- 低层反向调用高层（Service 不得 import Agent）

## Agent 设计

四个 Agent 由「任务分类 Agent」统一调度，按状态机流转：

### 任务分类 Agent —— 客服调度（主调度器）

- 将用户请求分为四类：`appointment`（报修/预约上门）、`query`（售后咨询与订单/保修/工单进度查询）、`complaint`（投诉/转人工）、`other`（无关请求兜底）
- **工具化调度**：子 Agent 入口登记为工具（`agents/supervisor/tool_registry.py`：报修登记/售后咨询/转人工/兜底，标注风险与模型分级）；主管按分类结果从注册表选工具执行——分类即工具选择，每轮回合记录规划复盘（category→tool_id，供审计）
- 持有状态机（CLASSIFY/APPOINTMENT/CONSULT）与路由器，负责把消息分发给对应子 Agent
- 投诉类在**调度层**直接落库转人工记录并回执，无需进入子流程
- 子 Agent 将请求判为无关时转回调度重新分类（主管重新规划）；设有递归轮次上限防止死循环

### 报修专员 Agent（Appointment Agent）—— 核心业务流程

- 结构化抽取 JSON 契约：`{product_type, fault_desc, address, phone, start_time, engineer_name, confirmation, info_complete, unrelated, missing_info}`
- 信息不完整逐项追问；完整后经工程师查找器（EngineerFinder）匹配工程师
- 匹配策略：指定工程师 → 查档期；档期冲突 → 按技能向量相似度 + 区域软偏好推荐替代并请求用户确认；未指定 → 技能相似排序逐个查空闲
- 派单成功即生成工单号 `AX+YYYYMMDD+序号`，写工程师忙档；同轮给出保修提示（按手机号查订单）

### 售后顾问 Agent（Consultant Agent）—— RAG 知识问答

- **查询前置短路**：订单保修 / 工单进度类意图由纯函数正则直接查库给出模板化答复，不消耗 LLM
- 其他售后问题：FAISS（IndexFlatIP）语义检索知识库 → LLM 生成答复
- 判断与咨询无关的请求转回客服调度

### 用户行为 Agent（User Behavior Agent）—— 后台偏好学习

- 记录每次报修的客户（手机号）、品类、故障、上门时段与工程师，维护偏好置信度
- 30 天无报修客户生成保养回访提醒；保修 30 天内到期订单生成续保提醒（调度器定期产出）
- 支撑 `/follow_ups` 回访页：常用工程师 + 可约时段生成个性化回访话术

### AutoDream 沉淀服务（DreamService）—— 离线画像沉淀

把分散在多会话的客户行为离线回放成长期记忆画像（后台定时 + 手动触发，口径见上文「核心能力」）：
资格判定 → 增量回放 → 偏好置信度更新 → 冲突降权 → 画像记忆写入（LLM 润色失败自动走确定性模板）→ checkpoint 落库。降级原则与全项目一致：无 Key 环境下资格判定、置信度、降权、模板画像全部可用，仅 LLM 润色自动跳过。

## 核心设计思想

1. **任务分类降低系统复杂度**：一次对话只处理一个主任务，状态机保证上下文不串场，坏输入（无关请求）在入口就被兜住
2. **RAG 回答专业知识**：保修政策/收费标准/故障自查等随知识库热更新，不写死在代码
3. **纯函数先行**：订单/工单查询这类高确定性需求走查库+模板，杜绝幻觉与无效 LLM 轮次
4. **分层架构保证可维护性**：Agent 只做编排，数据访问统一收口到仓储，删库即可重建全量种子
5. **确定性优先的演示闭环**：生成工单号、派单冲突校验、状态机白名单、回电承诺均为确定性逻辑，离线可测

## 架构图

业务链路（一次完整报修 + 售后跟进）：

```
客户 ──> /chat/stream（SSE 令牌流）
            │
        [客服调度] 分类
            ├─ appointment ─> [报修专员] 追问补齐 → 匹配工程师（档期/技能/区域）
            │                    └─ 创建工单 AX… → 派单写忙档 → 保修提示
            ├─ query ───────> [售后顾问] 订单保修 / 工单进度（查库模板）
            │                    └─ 其他售后问题（FAISS 检索 → LLM 回答）
            ├─ complaint ───> 登记转人工记录 + 回电承诺回执
            └─ other ───────> 抱歉兜底，列出可提供的能力
后台：/tickets 派单/状态流转 · /engineers 排班 · /follow_ups 回访 · /knowledge 知识库
```

## 会话与分层记忆

会话通过 `agents/session/` 的运行时注册表（`AgentSessionRegistry`）管理：

- **每会话一张 Agent 图**：按 `session_id` 惰性构建（客服调度 + 报修专员 + 售后顾问），进程内 LRU 上限 8；同会话并发以 per-session 锁串行，多会话互不串场
- **单一真相源 `SessionContext`**：客户绑定（手机号）、预约槽位、短期窗口、滚动摘要；每轮结束整份快照写穿 `chat_sessions` 表 → 刷新页面、LRU 淘汰、进程重启均可按行还原（含状态机值）
- **短期记忆窗口**：一问一答为一轮，容量 10 轮；占用达 60% 时把最旧轮次滚入滚动摘要（LLM 摘要失败自动降级为截断拼接并标记「早期对话截断」），窗口保留近约 6 轮原文
- **长期记忆**：报修成功 / 咨询完成沉淀为 `user_memories`（类型 repair/consult/preference、重要度 0.8/0.5/0.6、语义向量）；AutoDream 沉淀把老客户画像写入 `profile` 型记忆（重要度 0.7，去重轮换至多一条）；客户再次出现（消息带手机号自动绑定）时按召回分召回 Top-5，注入咨询背景与提示词，实现跨会话「记得老客户」
- **Web 会话标识**：浏览器 `localStorage` 保存 `session_id`（请求头透传，后端缺省时生成并回传 `X-Session-Id`），无需登录即保持同一会话

召回分 = `0.6 × 语义相似度 + 0.3 × 时效（30 天线性衰减）+ 0.1 × 重要度`；Embedding 不可用时自动降级为 `(0.3×时效 + 0.1×重要度) / 0.4` 归一排序（无 Key 环境可完整运行）。

## 技术栈

| 类别 | 选型 |
|---|---|
| 语言/框架 | Python 3.10+ / FastAPI + Uvicorn |
| Web 页面 | Jinja2 模板 + 原生 JS（SSE 流式渲染） |
| LLM | LangChain + OpenAI 兼容接口（可换 qwen/deepseek/zhipu/openai/azure；`main` 生成 + `fast` 结构化双通道分级） |
| 向量检索 | FAISS（IndexFlatIP 语义检索 / IndexFlatL2 相似匹配） |
| 存储 | SQLite + SQLAlchemy 2.0（声明式 ORM + 仓储模式） |
| 测试 | pytest（193 项离线单测）+ tests/eval EDD 评测（30 例离线）+ scripts/run_quality_gate.py 本地门禁 |

## 项目结构

```
├── app.py                 # FastAPI 入口：路由注册 + 启动初始化
├── web/                   # Web 层：routes.py + templates + static
│   ├── routes.py          #   页面路由与 /chat/stream SSE 流式聊天
│   └── templates/         #   index / engineers / engineer_schedules / tickets / knowledge_management / follow_ups / audit_logs
├── api/                   # API 层：engineer / ticket / knowledge / follow_ups / task / consultation / appointment / audit（+ audit_guard 幂等公共件）
├── agents/                # Agents 层
│   ├── task_classification_agent.py   # 任务分类 Agent（客服调度）
│   ├── task_classification/           #   classifier / state_manager / agent_router / unrelated_handler / processor
│   ├── supervisor/                    #   主管工具注册表（工具元数据 + 清单注入 + 规划复盘）
│   ├── appointment_agent.py           # 报修专员 Agent
│   ├── appointment/                   #   input_parser / engineer_finder / message_builder / processor
│   ├── consultant_agent.py            # 售后顾问 Agent
│   ├── consultant/                    #   knowledge_retriever / consultation_classifier / response_generator / prompt_builder
│   ├── session/                       # 会话运行时：session_context / session_window / agent_session_registry
│   └── user_behavior_agent.py + user_behavior/   # 用户行为与回访
├── services/              # Services 层：engineer / ticket / order / handover / knowledge / mcp_rag_client / text_embedding / recommendation / user_behavior / chat_session / memory（含 memory_scoring）/ dream_service（含 dream_policy 纯策略）/ audit_service / permission_policy
├── db/                    # DB 层：models.py / db_router.py / repositories（含 chat_session / user_memory / audit_log）/ base（session_manager、interfaces）
├── config/                # 模型提供方（main/fast 分级通道）、常量、时区与营业时间
├── tests/                 # 193 项离线单测 + eval/（EDD 评测 30 例：单步/组件/端到端）
├── scripts/               # 本地质量门禁 run_quality_gate.py（pytest + EDD 双闸）
└── data/                  # SQLite 库与向量索引（运行时生成，已 gitignore）
```

## 数据模型（13 张表）

| 表 | 说明 |
|---|---|
| `engineers` | 工程师：姓名、品类技能（Text，供向量匹配）、服务区域 |
| `engineer_schedules` | 工程师排班/忙档：时段、状态、绑定的工单 |
| `repair_tickets` | 报修工单：工单号 AX…、客户、品类、故障、地址、上门时段、状态机、工程师 |
| `orders` | 客户购买订单：手机号、品类、型号、购买日期、保修年限（保修期动态计算） |
| `human_handovers` | 投诉/转人工记录（可能在无工单时发生，故独立成表） |
| `knowledge_documents` | 售后知识库（软删除，重建 FAISS 索引时剔除） |
| `user_behaviors` | 用户行为流水（报修/咨询，客户=手机号） |
| `user_preferences` | 偏好与置信度（品类/故障/时段/工程师） |
| `user_recommendations` | 回访/提醒任务产出 |
| `chat_sessions` | 会话快照：session_id（唯一）、绑定客户、状态机值、预约槽位/短期窗口（JSON）、滚动摘要 |
| `user_memories` | 客户长期记忆：类型（repair/consult/preference/profile）、重要度、语义向量（JSON）、来源会话，软删除 |
| `dream_checkpoints` | AutoDream 沉淀检查点：每人一行，已回放事件ID（幂等断点）+ 任务锁（超时接管）+ 累计计数 |
| `audit_logs` | 操作审计日志：操作方/场景/动作/对象/工具/风险分级/成败/详情/IP；`idem_key` 唯一索引支撑后台写接口幂等重放 |

工单状态机：`pending → assigned → in_progress → completed`，前三态可 → `cancelled`；完成/取消即释放工程师忙档。保修期 = 购买日 + 保修年限（超出判超保，提示付费维修）。

演示数据（启动自动播种，删 `data/` 即重置）：

- 8 名工程师覆盖 空调/冰箱/洗衣机/热水器/净水器/烟灶 × 海淀/朝阳/丰台/西城/东城/石景山
- 手机号 `13800138000`（王芳）：空调 2024-06 购（在保至 2030-06）、冰箱 2025-01 购（在保至 2028-01）、洗衣机 2021-03 购（已超保）——可直接体验「在保/超保」双话术
- 手机号 `13900000000`（李强）：演示第二客户

## 快速开始

### 1. 安装依赖

```bash
pip install -r requirements.txt
```

### 2. 配置环境变量

复制 `.env.example` 为 `.env` 并填入模型配置（聊天与 Embedding 可不同厂商，DeepSeek 需另配嵌入）：

```bash
cp .env.example .env
```

**无 Key 也能启动**：系统正常建表并播种种子数据；LLM 调用降级为确定性兜底话术，知识库跳过向量索引，报修登记/工单/保修查询/投诉登记等核心链路仍可用。

### 3. 启动

```bash
python -m uvicorn app:app --host 127.0.0.1 --port 8001
```

打开 <http://127.0.0.1:8001> 进入聊天页。

**体验路径建议**：报修「空调不制冷，地址…，手机 13800138000，今天下午 3 点上门」→ 拿工单号去问进度 → 去 `/tickets` 完成状态流转 → `/follow_ups` 生成回访。

## 接入外部 RAG MCP 知识库（可选）

知识库检索可委托给外部 **MODULAR-RAG-MCP-SERVER**（MCP stdio 协议，chroma 向量 + BM25 + rerank 混合检索）：开关开启后 `KnowledgeService.search` 优先调用外部 RAG（工具 `query_knowledge_hub`），无结果或调用异常时自动回退本地 FAISS 检索；开关关闭时行为与本地版完全一致。RAG 服务端由本进程作为子进程拉起，不随本仓库分发。

```dotenv
# .env
RAG_MCP_ENABLED=true
RAG_MCP_CWD=C:/Users/Cloud/Desktop/RAG项目/MODULAR-RAG-MCP-SERVER-main   # RAG 服务端项目根目录
# RAG_MCP_COMMAND=python                              # 默认 sys.executable
# RAG_MCP_ARGS=["-m", "src.mcp_server.server"]        # 默认同左
# RAG_MCP_COLLECTION=knowledge_hub                    # 集合名，留空用服务端默认
```

联调三步：

1. 确认 RAG 服务端可被拉起（其环境需装好 chromadb 等依赖，embedding 可走本地 ollama）：

   ```bash
   cd <RAG项目根目录> && python scripts/test_mcp_client.py
   ```

2. 开启 `RAG_MCP_ENABLED=true` 后启动本项目：

   ```bash
   python -m uvicorn app:app --host 127.0.0.1 --port 8001
   ```

3. 冒烟：知识库搜索接口返回应来自外部 RAG（首次调用需拉起子进程，等待数秒）：

   ```bash
   curl -X POST http://127.0.0.1:8001/api/knowledge/search \
        -H "Content-Type: application/json" \
        -d '{"query": "空调不制冷", "top_k": 3}'
   ```

   集合名可用下面的脚本调 `list_collections` 确认（在本项目根目录运行）：

   ```bash
   python -c "import asyncio; from services.mcp_rag_client import RagMcpClient; print(asyncio.run(RagMcpClient().list_collections()))"
   ```

   若 RAG 服务停用，检索请求自动回退本地结果。

## 流式令牌协议

`/chat/stream` 以 SSE 流式返回，文本按行解析令牌渲染：

| 令牌 | 含义 | 前端渲染 |
|---|---|---|
| `[THOUGHT][角色]` | 思考过程（角色：客服调度/报修专员/售后顾问） | 灰字小号 |
| `[REPLY][角色]` | 对用户说的话 | 气泡主文本 |
| `[SIGNAL]recommendation_pending` | 推荐工程师待确认信号 | 不渲染 |
| `[ERROR]` | 异常信息 | 红色提示 |

## 测试

```bash
pytest                    # 193 项全部离线运行，不依赖 LLM/Embedding Key
pytest tests/test_offline_services.py -q   # 工单生命周期/保修边界/档期冲突等纯逻辑
pytest tests/test_session_isolation.py tests/test_chat_handler_session.py -q  # 会话隔离/写穿/重启恢复
pytest tests/test_dream_policy.py tests/test_dream_service.py -q  # AutoDream 资格/幂等/降权/画像/任务锁
pytest tests/test_tool_registry.py tests/test_model_tier.py -q  # 主管工具化选择/规划复盘/模型分级通道装配
pytest tests/test_audit_logging.py tests/test_permission_policy.py -q  # 审计落库/幂等键治理/风险分级与白名单同源
python tests/eval/run_eval.py        # EDD 评测：单步 19 + 组件 6 + 端到端 5（成功率/P95/步数/token，全离线）
python scripts/run_quality_gate.py   # 质量门禁：pytest 193 + EDD 30 例 100% 通过才算绿（本地执行）
```

覆盖：分类枚举与兜底、信息抽取契约、工单状态机白名单、档期冲突与释放、保修期边界、偏好置信度、回访判定（30 天）、会话窗口滚动、长期记忆召回打分、多会话隔离、绑定/写穿/重启还原、AutoDream 沉淀资格边界（≥5 会话且跨度 ≥24h）、增量回放幂等、偏好冲突降权、画像记忆去重轮换、任务锁与崩溃残留接管、主管工具注册表（类别↔工具映射/未知兜底/清单注入提示词）、模型分级（fast 未配置透明回退/独立覆盖/分类·判定·抽取接 fast、生成接 main）、审计落库与过滤、幂等键治理（成功占位重放短路/失败不占位可重试）、服务层写路径审计插桩、风险分级矩阵（read/confirm/write × 工具白名单与注册表同源）等；另 `tests/eval/` 三层 EDD 评测：**单步**（19 例纯函数确定性断言：风险档位/工单号格式/保修口径/召回打分归一/幂等键…）、**组件**（6 例真实对象协作：主管规划审计/会话重启还原/滚动摘要降级/记忆绑定/派单冲突/资格边界）、**端到端**（5 例走真实会话链路：建单闭环/保修查询短路/投诉转人工/提示注入拦截/双会话隔离），以脚本替身 LLM 离线驱动真实 Agent 图，E2E 按场景重置计数器，LLM 调用次数精确命中步数预算（2/1/1/1/5），并度量输入字符估算 token 成本。

质量门禁说明：门禁为本地脚本（`scripts/run_quality_gate.py`），两道闸门——① pytest 193 项单测；② EDD 30 例须 100% 通过。评测全程离线：临时沙箱目录隔离默认库（业务代码零改动）、脚本替身替换模型工厂（等价生产装配）、Embedding 工厂强制不可用走语义降级路径；沙箱自建自清，重复执行结果确定。

## 主要页面

| 路由 | 页面 |
|---|---|
| `/` | 智能客服聊天（SSE 流式） |
| `/tickets` | 报修工单管理（派单/状态流转/转人工记录） |
| `/engineers` | 工程师管理（技能/区域/今日忙闲） |
| `/engineer_schedules` | 今日排班网格（9:00-18:00） |
| `/knowledge` | 知识库管理（增删改查 + 搜索） |
| `/follow_ups` | 售后回访（档案分析 + 话术生成） |
| `/audit_logs` | 操作审计（全链路写留痕，场景/风险/结果过滤） |

## 已知边界（演示范围外）

- **无登录/权限体系**：会话按浏览器 `session_id` 隔离，客户身份以消息内手机号绑定为准；后台写操作以「操作审计 + 风险分级 + 幂等键治理」兜底（当前无账号体系，无法区分具体操作员权限）
- **不接支付**：超保维修费用仅话术提示，不产生真实交易
- **转人工为登记制**：回执承诺 30 分钟回电，无真实外呼
- 业务窗口 9:00-18:00 统一在 `config/time_config.py` 配置（唯一事实源）

## 后续规划

- SSE 心跳与断线重连
- 售后顾问对工程师闲忙态的实时感知与「顺路派单」建议
- 满意度评价闭环：完成后邀请打分，评价进入行为画像
- 超保付费维修报价流程与配件库存查询
