# 学习指南：安居家电售后智能客服 + 上门报修预约 Agent

> 本文是配套 `README.md` 的深度教学文档：README 讲"是什么"，本文讲"为什么这么设计、代码怎么读、改哪里能学会什么"。
> 建议按章节顺序精读，每章末尾有「本章要点」小结；第 12 章是按难度分级的动手练习。
> 精读前先跑通项目（见 README「快速开始」），并准备一个能查看调用链的编辑器（VS Code 的 "Go to Definition" 足够）。

---

## 目录

1. 项目定位：它到底解决什么问题
2. 一次用户消息的完整旅程（全局时序）
3. 技术栈选型理由
4. 五层架构：职责与依赖纪律
5. 数据层：9 张表 + 状态机 + 事务
6. Agent 内部机制精读
7. LLM 与 Embedding 的抽象与降级
8. 流式协议（SSE）精讲
9. 知识库与 RAG（本地 FAISS + 外部 MCP 双路）
10. 时间与业务规则的"单一事实源"
11. 测试体系：68 项为什么能全离线跑
12. 动手练习（读代码 / 改代码 / 加功能）
13. 面试与汇报可提炼的亮点

---

## 1. 项目定位：它到底解决什么问题

### 1.1 业务场景

家电品牌售后客服每天的重复劳动高度结构化：

- 客户来电/来消息说"空调不制冷"，客服要先**问齐**品类、故障、地址、手机号、期望时间；
- 再查工程师**档期**，冲突时要给出替代方案；
- 客户事后要查**保修期**、查**工单进度**；
- 解决不了的投诉要**转人工**，且要留记录、给承诺；
- 运营还要管理工程师排班、维护知识库、做售后回访。

这个项目把上述闭环做成一个**演示级多 Agent 系统**：客户一侧是自然语言聊天窗口，运营一侧是 6 个后台管理页面。它是"业务 CRUD 系统如何长出 AI 能力"的最小但完整的样本。

### 1.2 学习价值（它适合学什么）

| 想学的主题 | 本项目给你的载体 |
|---|---|
| 分层架构如何约束 AI 代码 | 五层单向依赖，LLM 只能在 Agents 层被调用 |
| 多 Agent 编排与状态机 | 任务分类调度器 + 3 个子 Agent + 状态白名单 |
| LLM 应用的工程化降级 | 无 Key 也能跑：模板兜底 + 纯函数短路 + 热线引导 |
| RAG 最小实现 | SQLite 存文档 → Embedding → 内存 FAISS → 检索拼 Prompt |
| 可测试的 AI 代码 | 68 项 pytest 全部离线：LLM 可注入、DB 可换临时文件 |
| 真实感的业务规则 | 工单状态机、工程师档期冲突、保修期动态计算 |

全书反复出现的一组"设计母题"，建议带着读：

1. **确定性优先**：能用正则/查库/状态机解决的，绝不消耗 LLM；
2. **白名单思维**：状态转换要 `can_transition_to`，LLM 输出要 `VALID_CATEGORIES` 校验；
3. **降级链**：外部能力（LLM、Embedding、外部 RAG）失效时，系统退回确定性路径而不是崩溃；
4. **单一事实源**：营业时间、时区、DB 路径都有唯一配置点。

---

## 2. 一次用户消息的完整旅程（全局时序）

以客户输入"空调不制冷，地址海淀黄庄 1 号，手机 13800138000，今天下午 3 点上门"为例，从浏览器到 SQLite：

```
浏览器 index.html
  │  fetch POST /chat/stream（body 含 message），用 ReadableStream 读 SSE
  ▼
web/routes.py  chat_stream_endpoint            ← 页面层：把请求交给处理器，把结果流回前端
  ▼
api/chat_handler.py  ProcessUserInput_stream() ← 调度入口（api 层，进程内直连）
  ▼
agents/task_classification_agent.py            ← 任务分类 Agent：LLM 把这句话判为 appointment
  ▼
task_classification/classification_processor.py + agent_router.py
  │  state: CLASSIFY → APPOINTMENT（经 state_manager 校验白名单）
  ▼
agents/appointment_agent.py  run_stream()
  ▼
appointment/appointment_processor.py           ← 抽取 JSON 契约、校验、发现字段齐全
  ▼
appointment/engineer_finder.py                 ← 匹配工程师：技能相似度 + 区域 + 查忙档
  ▼
services/ticket_service.py（经 services/__init__ 注入的仓储）
  ▼
db/repositories/ticket_repository.py           ← 唯一写 SQL 的地方
  ▼
SQLite（data/smart_appointment.db）
```

回程同样逐层返回，最终以 SSE 令牌形式流到浏览器：

```
[THOUGHT]报修专员 → [REPLY]已为您登记……您的工单号是 AX202609030001……
```

两个方向都值得注意：**下行**（用户输入 → 数据库）经过了全部 Agent 编排与业务校验；**上行**（回复）以令牌流增量到达，前端边收边渲染。

> 阅读技巧：第一次读代码时，从 `web/routes.py` 的 `chat_stream_endpoint` 出发，用 IDE 跳转功能一步步追到 `db/repositories/`，画一张你自己的时序图。这张图比任何文档都值钱。

**本章要点**：一次对话 = 页面层接入 → 调度层分流 → 子 Agent 执行业务流程 → Service/仓储落库 → 令牌流返回。

---

## 3. 技术栈选型理由

| 类别 | 选型 | 为什么（教学视角） |
|---|---|---|
| Web 框架 | FastAPI + Uvicorn | 原生 async 支持 SSE 流式；类型校验（Pydantic）省掉大量参数解析代码 |
| 页面 | Jinja2 + 原生 JS | 不引前端框架，整个系统一个 `pip install` 能跑，方便通读 |
| LLM | LangChain + OpenAI 兼容接口 | `config/model_provider.py` 一处切换 qwen/deepseek/zhipu/openai/azure；"换厂商不改代码"是很好的抽象示例 |
| 向量检索 | FAISS | 进程内、文件级、无需起服务；IndexFlatIP 做语义检索，IndexFlatL2 做相似匹配 |
| 存储 | SQLite + SQLAlchemy 2.0 | 零运维；ORM 声明式 + 仓储模式是可面试的数据层样板 |
| 测试 | pytest | 全离线：见第 11 章 |

技术选型的隐藏逻辑：**演示项目的可运行性优先**——所有重型组件（DB、向量库）都选进程内的，唯一的外部依赖（LLM）允许缺失。

---

## 4. 五层架构：职责与依赖纪律

README 有一张五层图，这里展开讲每一层的"纪律"以及代码里怎么体现。

```
Web 层    web/      页面渲染、/chat/stream SSE 接入       —— 不写业务
API 层    api/      路由 + 参数模型 + 薄转发             —— 不写业务
Agents 层 agents/   多 Agent 编排，LLM 唯一调用点        —— 写"对话逻辑"
Services 层 services/ 业务逻辑 + 种子数据 + 仓储注入       —— 写"业务逻辑"
DB 层     db/       ORM 模型 / 仓储实现 / 会话管理         —— 写"SQL"
```

### 4.1 Web 层（`web/routes.py` + `web/templates/`）

- 8 个路由，两类职责：
  - 页面路由：`/`（聊天）、`/knowledge`、`/engineers`、`/engineer_schedules`、`/follow_ups`、`/tickets`；
  - 聊天数据路由：`chat_stream_endpoint`（SSE 流式）与 `chat_endpoint`（一次性返回，内部同样是 token_generator 但一次性聚合）。
- 前后端通过**明文令牌协议**通信（第 8 章），前端 JS 用正则切分令牌渲染，逻辑全在 `templates/index.html`。

### 4.2 API 层（`api/`）

每个业务域一个模块：`appointment / chat_handler / consultation / engineer / knowledge / task / ticket / user_behavior_analysis`，另有 `api/core/`（统一异常与响应模型）。

它存在的意义是**给非页面调用方一个稳定入口**（curl、联调、未来做 App 都能用），例如：

- `POST /api/knowledge/search` —— 知识库语义检索（外部 RAG 冒烟也用它，见第 9 章）；
- `POST /api/task/classify` —— 单次任务分类（可用于观察分类器的裸输出）；
- `GET /api/engineers`、`GET /api/engineers/schedules` —— 后台页面数据源。

纪律：API 层只做参数校验与转发，不 import Agent 内部类、不写 SQL、不调 LLM。它是 Web 与 Agents 之间的"隔板"。

### 4.3 Agents 层（`agents/`）

结构是"4 个 Agent + 每个 Agent 一个内聚子包"：

| Agent | 文件 | 职责 | 子包 |
|---|---|---|---|
| 任务分类 Agent | `task_classification_agent.py` | 主调度器：意图分类 + 状态机 + 分发 | `task_classification/`：classifier / state_manager / agent_router / unrelated_handler / classification_processor |
| 报修专员 Agent | `appointment_agent.py` | 报修登记 → 匹配工程师 → 建单 | `appointment/`：input_parser / engineer_finder / message_builder / appointment_processor |
| 售后顾问 Agent | `consultant_agent.py` | 查询短路 + RAG 问答 | `consultant/`：consultation_classifier / consultation_processor / knowledge_retriever / response_generator / prompt_builder |
| 用户行为 Agent | `user_behavior_agent.py` | 后台偏好学习与回访 | `user_behavior/`：behavior_recorder / pattern_analyzer / preference_manager |

三个要点：

1. **Agent = 编排者，不是数据库访问者**。Agent 通过构造注入的 Service/仓储读数据（如 `AppointmentAgent` 的 processor 依赖 `EngineerFinder`，后者注入仓储）。代码里 Agents 层不出现 SQLAlchemy Session。
2. **Agent 之间有路由器，无直接耦合**。子 Agent 不认识彼此，都只认"任务分类 Agent 分给我的消息"；子 Agent 觉得无关时通过 `unrelated_callback`（转回调度）把控制权交还。
3. **状态机在调度器手里**（见 6.1），子 Agent 只在自己的状态内工作，结束必须把状态重置回 `CLASSIFY`——防止上下文串场。

### 4.4 Services 层（`services/`）

- 每域一个 Service：`engineer_service / ticket_service / order_service / handover_service / knowledge_service / recommendation_service / user_behavior_service`，`text_embedding.py` 与 `mcp_rag_client.py` 是支撑工具。
- 它们持有"业务规则与种子数据"：工单号生成规则、保修期计算、档期冲突校验、30 天回访判定都在这一层或之下的仓储。
- 层间可复用：如售后顾问查工单状态直接复用 `TicketService`——这正是分层的红利。

### 4.5 DB 层（`db/`）

- `models.py`：9 张表的 SQLAlchemy 声明式模型；
- `repositories/`：每表一个仓储类，方法返回 dict 而非 ORM 对象（隔离 ORM 细节）；
- `base/interfaces.py`：每个仓储的抽象基类（`BaseEngineerRepository` 等）——仓储实现可替换（测试时可注入 fake）；
- `db_router.py`：`DatabaseRouter` 聚合各仓储并提供统一入口；
- `base/session_manager.py`：见 5.3。

### 4.6 依赖纪律小结（代码里怎么自我约束）

- 目录树天然可见：`services/` 不 import `agents/`；`agents/` 不 import `db/` 的 Session；
- `config/model_provider.py` 只有 Agents 层在真正构造模型的地方 import；
- 仓储是**唯一** import ORM/Session 的模块。

**本章要点**：分层不是为了好看，是为了回答三个问题——"AI 逻辑放哪（Agents）、业务放哪（Services）、数据放哪（DB）"，以及"任何一层坏了，影响面在哪"。

---

## 5. 数据层：9 张表 + 状态机 + 事务

### 5.1 数据模型总览

| 表 | 一句话职责 | 关键字段 | 与业务规则的对应 |
|---|---|---|---|
| `engineers` | 工程师档案 | 姓名、品类技能(Text)、服务区域 | 技能文本用于 Embedding 向量匹配 |
| `engineer_schedules` | 排班/忙档 | 时段、状态、绑定工单 | 档期冲突校验的数据源 |
| `repair_tickets` | 报修工单 | 工单号 AX…、客户、品类、故障、地址、上门时段、状态、工程师 | 状态机流转主体 |
| `orders` | 客户购机订单 | 手机号、品类、型号、购买日、保修年限 | 保修期 = 购买日 + 年限（动态计算） |
| `human_handovers` | 转人工/投诉记录 | 诉求、联系方式 | 投诉可能无工单，故独立成表 |
| `knowledge_documents` | 知识库条目 | 内容、分类、关键词、Embedding 列 | 软删除，重建 FAISS 时剔除 |
| `user_behaviors` | 行为流水 | 客户(手机号)、动作类型、明细 | 每次报修/咨询追加一条 |
| `user_preferences` | 偏好与置信度 | 品类/故障/时段/工程师偏好 | 由流水聚合而来 |
| `user_recommendations` | 回访/提醒产出 | 类型、建议、状态 | 支撑 `/follow_ups` |

两处设计值得单独讲：

**为什么 `human_handovers` 独立成表？** 因为"转人工"可以发生在任何时刻（可能还没建工单），如果挂在 `repair_tickets` 上就得允许空外键或改表结构。这是建模时"按生命周期建模而非按页面建模"的体现——一张表服务一个生命周期。

**为什么 `orders` 里的保修期不落库？** 落库的是"购买日期 + 保修年限"，`在保/超保` 是**查询时动态计算**的。这样规则（如"活动延保一年"）改了不用迁移数据。教学点：能用计算表达的状态就别物化。

### 5.2 工单状态机与忙档释放

```
               ┌──────────── cancelled（取消）
               ▼
pending ──→ assigned ──→ in_progress ──→ completed（完成）
   │            │              │
   └────────────┴──────────────┘
         （三态皆可取消，取消/完成即释放工程师忙档）
```

阅读时注意两点：

1. 状态流转有**白名单**（`ALLOWED_TRANSITIONS` 之类映射表），不允许任意跳转；工单被"完成/取消"时把绑定的 `engineer_schedules` 忙档释放——这两条是离线测试的重点对象（`tests/test_offline_services.py`）。
2. 工单号 `AX + YYYYMMDD + 当日序号` 是确定性生成的（纯函数 + 库查询），因此可离线断言，这是"演示闭环可测"的基础。

### 5.3 会话管理：`session_scope` 为什么这样写

`db/base/session_manager.py` 的核心是：

```python
@contextmanager
def session_scope(self):
    session = self.Session()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()
```

三个教学点：

1. **事务边界 = 上下文边界**：进入 `with` 开始事务，正常退出 commit，异常 rollback 后**继续抛出**——绝不吞异常（吞了调用方会拿到脏数据还不知情）。
2. `scoped_session` 让同一线程/协程内共享同一会话，配合 `session_scope` 避免"会话泄漏"。
3. 仓储方法拿 session 的方式：由调用方（Service 或路由）通过 `with router.xxx() as session` 之类把 `session_scope` 实例传入——所以 Repository 接口方法几乎都带 session 参数，测试时传一个临时库的 session 即可全离线验证。

**本章要点**：表的划分跟着"生命周期"走；能计算的状态不落库；状态机只走白名单；事务用上下文管理器包住提交与回滚。

---

## 6. Agent 内部机制精读

这是全项目最值得细读的部分。阅读顺序建议：`config/constants.py` → `state_manager.py` → `task_classifier.py` → `agent_router.py` → 任一子 Agent 的 processor。

### 6.1 会话状态机：为什么"一次只做一件事"

`config/constants.py`：

```python
class StateEnum(Enum):
    CLASSIFY = "classify"
    APPOINTMENT = "appointment"
    CONSULT = "consult"
    OTHER = "other"

class SharedState:
    def __init__(self):
        self.value = StateEnum.CLASSIFY
```

`StateManager` 负责一切流转，并定义白名单：

```python
allowed_transitions = {
    StateEnum.CLASSIFY:    [StateEnum.APPOINTMENT, StateEnum.CONSULT],
    StateEnum.APPOINTMENT: [StateEnum.CLASSIFY],
    StateEnum.CONSULT:     [StateEnum.CLASSIFY],
}
```

设计的动机（理解它，比背代码重要）：

- **为什么要有状态**？LLM 对话没有"上下文边界"。用户上一句在报修、下一句突然查保修，如果所有历史都堆给 LLM，容易串场、浪费 token。状态机把一次会话切成"一个主任务"，当前不是该任务的消息按"无关"处理，任务结束强制回 `CLASSIFY`。
- **为什么是白名单而不是任意转换**？因为 `APPOINTMENT → CONSULT` 这类"横跳"意味着任务没完成就换频道，业务上不允许（报修还没登记完就去回答保修问题，两边都做不好）。白名单把"流程编排"从 LLM 的自由发挥变成可审计的确定性逻辑。
- 注意 `StateEnum.OTHER` 存在但状态机实际只用到三态——它是分类器输出的合法值之一，用于"本轮不进入任何子流程"（见 6.2），不要把两套枚举混为一谈。

### 6.2 任务分类 Agent（主调度器）

**链路**：`TaskClassificationAgent.classify_task_stream` → `ClassificationProcessor.process_task_stream`。

1. **分类**：`task_classifier.py` 用 LLM 把用户输入分成四类，且输出必须落在 `VALID_CATEGORIES = {'appointment', 'query', 'complaint', 'other'}` 白名单内；**LLM 返回不在白名单的类别时按 `other`（兜底）处理**——白名单校验同时防幻觉与防格式漂移。
2. **分流**：`agent_router.py` 按类别路由：
   - `appointment` → 报修专员（同时把状态切到 `APPOINTMENT`）；
   - `query` → 售后顾问（状态 `CONSULT`）；
   - `complaint` → **调度层直接落库**转人工记录（`human_handovers`）并回执"30 分钟内回电"，不再下钻；
   - `other` → 兜底话术，列出系统能力。
3. **坏输入兜底**：子 Agent 内发现"无关请求"（例如报修专员收到"帮我点外卖"）→ `unrelated_handler.py` 给出一句引导（几条轮换文案）→ `unrelated_callback` 转回调度重新分类。
4. **递归上限**：分类→子流程→转回→再分类若超过轮次上限，直接兜底，防止两个 Agent 互相踢皮球死循环。

**本章教学点**：调度器是"入口闸门"，分类质量不必完美——因为 1) 输出被白名单约束；2) 子 Agent 有纠错回退；3) 有轮次上限保底。这套"宽松入口 + 严格出口 + 回退闭环"是 Agent 系统防失控的经典三件套。

### 6.3 报修专员 Agent（核心业务流程）

`appointment/` 四个文件分工清晰：

| 文件 | 职责 |
|---|---|
| `input_parser.py` | 把对话文本解析为结构化数据（LLM 抽取 JSON） |
| `engineer_finder.py` | 工程师匹配（档期/技能/区域） |
| `message_builder.py` | 把内部数据渲染成用户话术 |
| `appointment_processor.py` | 主流程状态机：缺什么补什么、何时结束 |

**抽取契约**（`appointment_processor.py`）：

```python
REQUIRED_FIELDS = ["product_type", "fault_desc", "address", "phone", "start_time"]
```

主流程（结合代码读）：

1. **抽取与补齐**：解析出 JSON（含 `confirmation / info_complete / unrelated / missing_info` 等控制字段）。缺失字段逐项追问（`handle_incomplete_info`），追问文案按缺失项动态生成——不是死模板。
2. **硬校验（纯函数先行）**：手机号正则 `PHONE_PATTERN = r"^1\d{10}$"`；期望上门时间解析失败或落在 9:00-18:00 之外则要求重说。**这些校验不经过 LLM**——LLM 只负责"听懂"，格式合法性交给正则，这是"确定性优先"母题的最佳案例。
3. **匹配工程师**（`engineer_finder.py`，三段策略）：
   - 用户指定了工程师 → 先查该工程师档期；
   - 档期冲突 → 按**技能向量相似度 + 区域软偏好**推荐替代工程师，发 `[SIGNAL]recommendation_pending` 信号，**征求用户确认后才派单**（不能让用户觉得被"擅自换人"）；
   - 未指定 → 按技能相似度排序，逐个查空闲档期，第一个可用即命中。
4. **建单落库**：生成 `AX+YYYYMMDD+序号` 工单号，写工程师忙档（`engineer_schedules` 对应时段绑定工单）。
5. **保修提示**：按手机号查 `orders`，同轮给出"这台空调在保/已超保"的提示，把报修完成与售后增值信息一次给到。

注意 `appointment_processor` 全程 `AsyncGenerator[str]`——每个产出都是一个 SSE 令牌片段（可能是 `[THOUGHT]` 也可能是 `[REPLY]` 的分段），这就是"流式 Agent"的实现：**LLM 生成一句、编排逻辑做一步，就把结果推给用户，而不是全部算完一次性回答**。

### 6.4 售后顾问 Agent（查询短路 + RAG）

`consultant/` 五个文件：`consultation_classifier`（意图细分）、`consultation_processor`（主流程）、`knowledge_retriever`（向量检索）、`response_generator`（回答生成）、`prompt_builder`（Prompt 集中管理）。

主流程两条路：

**A. 查询前置短路（零 LLM）**——在 `consultation_processor.py` 里用正则和关键词先行拦截：

- 订单保修类意图关键词：`ORDER_INTENT_KEYWORDS = ("保修", "在保", "过保", "超保", "质保", "出保", "保内", "保外", "订单", "还保")`；
- 工单进度类关键词：`TICKET_INTENT_KEYWORDS = ("工单", "进度", "报修单", "单号")` + 工单号形态校验（`AX…`、`1\d{10}` 手机号）；
- 命中即走 `OrderService / TicketService` 查库，用**模板**拼出答复。这类查询答案确定、格式固定，用 LLM 纯属浪费且可能幻觉——短路是性能与正确性双赢。

**B. 知识问答（RAG）**：其余问题走 `KnowledgeRetriever` 检索知识库（FAISS，`IndexFlatIP`，`top_k=3`）→ `PromptBuilder` 把检索文档拼成上下文 → LLM 生成回答。无 Key/检索失败时降级为**热线引导话术（400-820-9000）**，不把错误抛给用户。

**consultant 是 async 上下文管理器（`__aenter__/__aexit__`）**的原因值得注意：顾问可能同时服务分类 Agent 转来的"查一下"请求和独立咨询请求，每次会话进入时初始化 LLM 与检索器、退出时释放——用生命周期管理把"资源准备/清理"和业务代码分离。

无关请求（如"讲个笑话"）→ `set_unrelated_callback` 转回调度，与 6.2 的闭环衔接。

### 6.5 用户行为 Agent（后台的"记忆"）

这不是聊天 Agent，而是**学习 Agent**：每次报修/咨询时被调度器或服务层通知，把（手机号、品类、故障、时段、工程师）写入 `user_behaviors` 流水 → `preference_manager` 聚合出 `user_preferences` 偏好与置信度。

产出两条业务价值：

1. **回访提醒**：30 天无报修的客户 → 保养回访提醒；保修 30 天内到期（`WARRANTY_EXPIRY_WINDOW_DAYS=30`）→ 续保提醒。由调度器定期产出，落到 `user_recommendations`。
2. **个性化话术**：`/follow_ups` 页面用"该客户常用工程师 + 可约时段"生成带人味的回访话术（`generate_personalized_reminder`、`get_reminder_with_schedule` 等），查工程师档期用的是同一套 `engineer_schedules`——业务复用而非复制。

**本章要点**：4 个 Agent = 1 个入口闸门（调度）+ 2 个对客处理器（报修/咨询）+ 1 个后台学习器。它们共享状态机纪律、路由回退、确定性校验三套基础设施，但业务完全解耦。

---

## 7. LLM 与 Embedding 的抽象与降级

`config/model_provider.py` 是模型侧的单一事实源：

- `create_chat_model(temperature)` 与 `create_embedding_model()` 两个工厂函数；
- 厂商经 `MODEL_PROVIDER` / `EMBEDDING_PROVIDER` 环境变量选择（qwen/deepseek/zhipu/openai/azure，OpenAI 兼容接口），聊天与 Embedding 可**不同厂商**（DeepSeek 无通用 Embedding 的常见配置就是这么解的）；
- 全项目只有 Agents 层经它构造模型。

**降级链**（从高到低，逐级掉）是演示工程最该学的思路：

1. 真 LLM 正常 → 最高质量；
2. LLM 调用失败/无 Key → 各 Agent 的确定性兜底（分类器回 `other`、报修走不了就引导热线、顾问给热线话术）；
3. Embedding 失败 → 检索跳过向量索引（知识搜索返回空），工程师匹配退化为"未用 Embedding 的排序"（如关键词/区域优先）；
4. 任何一层失败都不让用户看到 traceback——用户只看到一句人话引导。

仓库曾附带零成本演示工具（`m_demo_llm_server.py` 规则式"假 LLM" + `m_demo_chat.py` 聊天壳），后随死代码清理删除；同一思路的正式产物仍在：`tests/eval/` 的 `ModelHub` 替身 LLM 即"按角色装配的脚本假 LLM"，离线评测/单测用它驱动真实 Agent 链路，无 Key 即可跑完整业务闭环（见 §11）。

**本章要点**：模型是插件不是地基——工厂函数 + 环境变量 + 逐级降级，让"无 Key 演示"成为一等公民而非残缺运行。

---

## 8. 流式协议（SSE）精讲

`web/routes.py` 的 `chat_stream_endpoint` 返回 `text/event-stream`。令牌协议：

| 令牌前缀 | 含义 | 前端渲染 |
|---|---|---|
| `[THOUGHT][角色]` | 思考过程（客服调度/报修专员/售后顾问） | 灰字小号 |
| `[REPLY][角色]` | 对用户说的话 | 气泡主文本 |
| `[SIGNAL]recommendation_pending` | 推荐工程师待确认信号 | 不渲染（内部状态） |
| `[ERROR]` | 异常信息 | 红色提示 |

三个实现细节值得琢磨：

1. **为什么不用标准 SSE 的 `data:` 结构而用行内前缀？** 因为 Agents 层产出的是"带角色的文本流"，而非结构化事件。前端用一个正则按行解析即可，接口简单、调试直观（浏览器 Network 面板肉眼可读）。这是"内部协议与实现成本匹配"的取舍。
2. **按字符 yield**：Agent 的 `run_stream`/`consult_stream` 是 `AsyncGenerator`，逐段产出；Web 层把它们实时转发。用户在 LLM 还没说完时就能看到开头——这是 AI 客服体验的关键（省去"转圈等待"）。
3. **`[SIGNAL]recommendation_pending` 是"程序信号"不是"人话"**：报修专员要征求确认时发出，前端不渲染，但流程层据此知道"对话停在待确认状态"，下一次用户输入会被当成对推荐的答复处理（`_handle_recommendation_response`）——流式里夹带控制信号，是"对话即协议"的一种形态。

**本章要点**：流式 = Agent 异步生成 + 令牌化 + 前端增量渲染；"对人说的话"与"程序信号"在同一流里分道而行。

---

## 9. 知识库与 RAG（本地 FAISS + 外部 MCP 双路）

`services/knowledge_service.py` 是知识库核心，方法族齐全（`search / add_document / update_document / delete_document / initialize / _build_vector_index` …），自带 12 条家电售后种子文档。

### 9.1 本地链路（默认）

1. 启动时 `initialize()`：建表、播种默认文档、为每条文档调 Embedding API 并把向量 JSON 存进自己的列；
2. `_build_vector_index()`：把 DB 里的向量载入内存 FAISS `IndexFlatIP`（点积相似度）；
3. `search(query, top_k=3, category=None)`：查询向量化 → FAISS 检索 → 返回 `{content, category, keywords, score, rank}` 结构；
4. 增删改：`delete_document` 默认**软删除**（保留行但标记 inactive），重建索引时剔除——软删除 + 异步重建是演示项目里"向量与 DB 一致性"的务实解；
5. Embedding 不可用时：索引构建跳过，search 返回空——与第 7 章降级链一致。

### 9.2 外部 RAG MCP 扩展（可选链路）

`services/mcp_rag_client.py` 演示了一个完整的外部能力接入范式：

- **协议**：官方 MCP SDK 的 stdio transport——`StdioServerParameters(command, args, cwd)` + `stdio_client` + `ClientSession`，本进程以子进程方式拉起外部 RAG 服务（chroma 向量 + BM25 + rerank 混合检索，Embedding 走本地 ollama）；
- **开关与配置**：环境变量 `RAG_MCP_ENABLED`（`is_rag_mcp_enabled()` 实时读取，可运行时切换），其余 `RAG_MCP_COMMAND/ARGS/CWD/COLLECTION` 均可覆盖默认值；
- **每次调用一个完整会话**：`_call_with_session` 用 `asyncio.Lock` 串行化 + `wait_for` 120s 超时。原因是 MCP 的 session enter/exit 必须发生在同一 task，无法跨请求常驻——这是踩过真坑后的正确姿势（首版常驻连接会报 "exit cancel scope in a different task"）；
- **解析容错**：`_parse_citations` 兼容两种返回形态（纯 JSON 块 / Markdown 里 ```json 围栏块），`_extract_json` 先整体解析失败再取围栏；每条 citation 被**规范化**成与本地 `KnowledgeService.search` 完全对齐的字段（`content/category/keywords/score/rank` + 溯源字段 `source/chunk_id/metadata`）——**对外部服务做"输出契约归一化"**，上层调用方无感；
- **双路回退**（在 `knowledge_service.search()` 里）：开关开 → 先问外部 RAG → 有结果直接返回；空结果或抛异常 → 记日志**自动回退本地 FAISS**。开关关 → 路径与最初完全一致，零行为变化。

这套"功能开关 + 输出归一化 + 失败回退"的扩展模式，可以套用到任何你想外接的能力（天气、物流查询、外部知识库……）。

**本章要点**：本地 RAG 是"DB 存文档 → 向量化 → 内存索引 → 检索拼上下文"的最小闭环；MCP 扩展展示的是**集成外部智能服务的工业级姿势**：配置化、容错解析、契约归一、失败回退、全程不影响旧路径。

---

## 10. 时间与业务规则的"单一事实源"

时间处理是这类项目的重灾区，本项目把坑提前填了：

- `config/time_config.py` 是唯一时间事实源：`BEIJING_TZ = UTC+8`，统一提供 `now()/today()/naive_now()` 等；
- 业务窗口 9:00–18:00 由 `get_business_hours()` 返回 `(9, 18)`——README 明说这是"唯一事实源"，排班网格、报修时间校验、忙闲判断都从这里取；
- **naive datetime 的约定**：存储与比较统一用"北京时间 naive"（`naive_now` / `parse_datetime`），避免 SQLite 与带时区对象混比出 off-by-8 小时的类 bug。读代码时遇到 `tzinfo` 处理不妨都对照 `time_config.py` 想一遍。

其余规则的事实源位置速查（读代码遇到数字别猜，找源头）：

| 规则 | 事实源 |
|---|---|
| 服务窗口 / 时区 | `config/time_config.py` |
| 会话状态枚举 | `config/constants.py` |
| 工单状态机白名单 | ticket 模型/service 的 `ALLOWED_TRANSITIONS` |
| 保修计算公式 | `services/order_service.py`（购买日 + 年限） |
| 回访/提醒窗口（30 天） | 用户行为相关常量（如 `WARRANTY_EXPIRY_WINDOW_DAYS`） |
| 工单号格式 | ticket service 的 `AX+YYYYMMDD+序号` 生成逻辑 |
| 热线号码 | 兜底话术常量 |

---

## 11. 测试体系：68 项为什么能全离线跑

`tests/` 下 6 个文件，68 项测试**全部离线**（不需要任何 API Key，不需要外部服务）。这是本项目最值得抄走的部分。怎么做到的：

| 难点 | 解法（在 `tests/conftest.py` 与各测试文件里） |
|---|---|
| 不能真调 LLM | 注入 Fake 模型/确定性桩（如假分类器、假对话模型），测试编排逻辑而非模型 |
| 不能污染开发库 | `temp_db_path` 夹具：每测试一个临时 SQLite 文件，用完即删 |
| 仓储层要真数据 | 测试直连临时库的仓储/Session，验证真实 SQL 行为 |
| MCP 不能真拉起子进程 | monkeypatch session 层（fake `call_tool` 返回构造的 `TextContent`），覆盖解析/异常两条路径 |
| 环境变量开关 | monkeypatch setenv/delenv，分别验证开关开/关行为 |

覆盖的典型断言对象（去测试文件里找对应用例）：

- 分类枚举与无关请求兜底、状态机白名单；
- 报修抽取契约（缺哪个字段、如何判定 complete）；
- 工单状态机全流转与非法流转拒绝、完成/取消释放忙档；
- 保修期边界（临界日、超保判定）；
- 档期冲突与替代推荐（`[SIGNAL]` 信号触发条件）；
- 偏好置信度增减、30 天回访判定边界；
- 外部 RAG 的解析/降级/开关。

运行方式：

```bash
pytest                    # 全部 68 项
pytest tests/test_offline_services.py -q   # 纯逻辑那一批
```

**教学点**：AI 项目的测试策略不是"测 LLM"（那是评测该干的），而是**把 LLM 当依赖注入掉，测它周围的确定性骨架**——状态机、契约校验、检索结果处理、回退路径。骨架稳了，换什么模型都稳。

---

## 12. 动手练习

按难度分级；每题给出"看哪里"和"怎么验收"。验收尽量用 `pytest` 或 curl 而不是肉眼看页面。

### 入门：读代码（回答以下问题）

1. 客户说"我想投诉"走哪条链路？`human_handovers` 记录在哪一层落库？（提示：调度层直接落，不经过子 Agent。验证：`pytest` 里搜 complaint/handover 用例。）
2. 报修时客户把手机号说成"138 0013 8000"（带空格），会被哪个正则、在哪一步拦下重问？（提示：`appointment_processor.PHONE_PATTERN` 与 `_is_valid_phone`。）
3. 一个"在保"判定在哪里完成？改订单的保修年限，现有订单的判定会立即变化吗？（提示：动态计算 vs 物化。）

### 进阶：改代码（改完跑 `pytest`，再按需补用例）

4. 把业务窗口从 9:00–18:00 改成 8:30–20:00，只改 `config/time_config.py` 的 `get_business_hours()`，确认所有用到的地方联动。（验收：新增一个对 19 点报修请求的测试。）
5. 给报修抽取加一个新必填字段（例如"期望联系电话"，其实即 `phone` 外的 `contact_note`？）——按 `REQUIRED_FIELDS` 与 `missing_info` 的既有模式扩展，观察追问话术是否自动覆盖新字段。
6. 新增一种"无关请求"轮换文案（`unrelated_handler.py`），为它补一个断言测试。

### 挑战：加功能（先想清楚设计，再动手）

7. **工单加急**：让客户说"很急"时工单带 `priority` 字段并在 `/tickets` 列表置顶。涉及：模型加列 → 抽取契约 → 仓储 → 页面。对照现有"状态流转"改动量评估分层给你省了多少事。
8. **工程师平均响应时长统计**：在 `engineer_schedules` 上统计"从 assigned 到 in_progress 的耗时"，在 `/engineers` 页面展示。涉及：Service 聚合 + 页面。考察你是否找到了正确的数据源。
9. **给售后顾问接上工程师闲忙**（README「后续规划」原题）：让客户问"今天下午能有人来吗"时顾问能查 `engineer_schedules` 给实时答复。设计难点：查询短路（纯函数）与 RAG 的先后顺序怎么排？

### 通读检查单

读完代码后，不看代码回答：`/chat/stream` 的一次完整调用涉及哪些文件（按顺序写出来）？每个文件只干一件事吗？哪一层是"可以被删掉而业务逻辑不损"的？——最后一问的答案不是"没有"，思考它为什么可以删、以及代价是什么。

---

## 13. 面试与汇报可提炼的亮点

项目叙事建议按"问题 → 约束 → 设计 → 验证"讲，下面每条都配了"代码里在哪"：

1. **五层单向依赖 + LLM 收口到 Agents 层**（目录结构、`config/model_provider.py` 唯一构造点）：回答"AI 代码如何与业务代码隔离"。
2. **任务分类调度 + 状态机白名单**（`StateManager.can_transition_to`、`VALID_CATEGORIES`）：回答"多 Agent 如何防上下文串场、如何防失控"——一次一个主任务 + 转回调度 + 轮次上限。
3. **确定性优先 / 纯函数短路**（保修与工单查询的正则短路、报修信息硬校验）：回答"如何控制成本与幻觉"——能查库模板化的不调 LLM。
4. **无 Key 全链路降级**（各兜底话术、检索空降级、热线引导）：回答"AI 功能的可用性设计"。
5. **流式体验**（SSE 令牌协议、`AsyncGenerator` 逐段产出）：回答"对话型产品的工程细节"。
6. **RAG 最小闭环 + 外部 MCP 双路回退**（`knowledge_service` + `mcp_rag_client`）：回答"知识库怎么演进、怎么外接能力"——开关化、输出契约归一化、失败回退零影响旧路径。
7. **可离线测试的 AI 骨架**（68 项 pytest、Fake 模型注入、临时库）：回答"AI 项目怎么测试"——测确定性骨架而非模型本身。
8. **业务建模细节**：转人工独立表、保修动态计算、完成/取消释放忙档——回答"演示项目里也有值得讲的建模决策"。

一个推荐的开场白：*"这是一个家电售后的多 Agent 演示系统，客户用自然语言完成报修到预约的闭环。我最有收获的设计不是 Agent 本身，而是为了让它可演示、可测试、可换模型，我被迫把 AI 代码放进一个和业务代码同样严格的分层里。"*——把重点放在工程约束上，比罗列功能更显深度。

---

## 附：与仓库其他文档的关系

| 文档 | 定位 |
|---|---|
| `README.md` | 项目总览：能力、架构、快速开始（先读这份） |
| `LEARNING_GUIDE.md`（本文） | 教学深读：机制、代码路径、练习 |
| `PROJECT_SUMMARY.md` | 单页总结（汇报/自述用） |
| `SPEC.md` | 规格/需求侧描述 |
| `tests/` | 可运行的"行为规格说明书"——想确认某个机制的真实行为，去测试里找，那里没有含糊其辞 |

> 学习捷径：把测试文件当"另一种文档"读。`test_offline_services.py` 的用例名基本就是业务规则清单。
