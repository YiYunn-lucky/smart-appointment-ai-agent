# 安居家电售后智能客服 + 上门报修预约 Agent — 技术规格（SPEC）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v2.0（按新大纲重构，与代码逐条核对） |
| 编写日期 | 2026-09-03 |
| 适用版本 | 当前 master（M0–M7 家电化改造完成后） |
| 系统名称 | 安居家电售后智能客服 + 上门报修预约 Agent |
| 阅读建议 | README 面向使用者，PROJECT_SUMMARY 面向汇报，本文档面向**开发与讲解**，所有锚点均可在源码中对应 |

---

## 1. 系统定位与目标

### 1.1 定位

面向家电品牌（demo 品牌「安居家电」）的**售后智能客服 + 上门维修调度**演示系统。客户用自然语言即可完成一条售后闭环：**报修登记 → 工程师上门预约 → 建单派单 → 工单进度跟踪 → 保修查询 → 投诉转人工**；运营侧配套**工单管理、工程师排班、知识库管理、售后回访**后台页面。品牌覆盖六品类：空调 / 冰箱 / 洗衣机 / 热水器 / 净水器 / 烟灶；服务窗口统一 **9:00–18:00**，默认上门维修窗口 2 小时。

### 1.2 工程目标

1. **多 Agent 编排 + 状态机 + RAG**：任务分类 → 分发专业 Agent → 结构化抽取 → 追问补齐 → 匹配派单；
2. **确定性收敛**：保修计算、档期冲突、状态流转、单号生成全部为可离线测试的纯函数，**不依赖 LLM 也可演示核心链路**；
3. **可运营闭环**：后台页面真实驱动工单状态与知识库，非单点 Demo。

### 1.3 范围外（README 已声明）

登录体系（会话按 `session_id` 隔离、客户身份以消息内手机号绑定为准，工单/订单按**手机号**归属客户）、真实支付、真实外呼/短信。

### 1.4 技术栈

| 类别 | 选型 |
| --- | --- |
| 语言 / Web | Python 3.10+ / FastAPI + Uvicorn / Jinja2 + 原生 JS（EventSource SSE） |
| LLM | LangChain，OpenAI 兼容工厂（`config/model_provider.py`：聊天与 Embedding 可分厂商；聊天模型 main/fast 双通道分级，见 §3.1 模型分级） |
| 向量 | FAISS：`IndexFlatIP`（知识语义检索）、`IndexFlatL2`（工程师技能相似排序） |
| 存储 | SQLite 单文件 + SQLAlchemy 2.0（声明式 ORM + Repository 仓储 + 手工注入 db_path） |
| 测试 | pytest + pytest-asyncio，59 项全离线（零外部 API） |

---

## 2. 名词表与领域模型

| 名词 | 含义 | 对应代码/存储 |
| --- | --- | --- |
| 客服调度 | 任务分类 Agent（主调度器）：意图识别、工具化规划与路由、状态机、投诉登记、无关兜底 | `agents/task_classification_agent.py` + `agents/supervisor/`（工具注册表） |
| 报修专员 | 预约 Agent：报修信息抽取、追问、工程师匹配、建单派单 | `agents/appointment_agent.py` |
| 售后顾问 | 咨询 Agent：订单保修/工单进度查库短路 + RAG 知识问答 | `agents/consultant_agent.py` |
| 用户行为 Agent | 行为记录、偏好置信度、回访判定与话术 | `agents/user_behavior_agent.py` |
| AutoDream 沉淀 | 离线把老客户（≥5 会话且跨度 ≥24h）行为增量回放为画像：置信度累计、旧偏好减半降权、profile 记忆去重轮换 | `services/dream_service.py` + `dream_policy.py`（checkpoint：`dream_checkpoints`） |
| 报修工单 | 一次上门维修的单据；单号 `AX+YYYYMMDD+当日2位序号`（`AX\d{12}`） | `repair_tickets` |
| 工程师 | 上门维修员：姓名、品类技能文本（供向量匹配）、服务区域（城区） | `engineers` |
| 忙档 / 排班 | 工程师时间占用；`status` ∈ busy / free，busy 绑工单 | `engineer_schedules` |
| 派单 | 校验档期 → 写 busy 排班 → 工单置 assigned | `ticket_service.assign_ticket` |
| 保修期 | 购买日 + 保修年限动态计算（365×年），非存储值 | `orders` + 查询时计算 |
| 转人工 | 投诉登记，独立落库，承诺 30 分钟回电（登记制，无真实外呼） | `human_handovers` |
| 回访 | 保养/保修到期/满意度三类提醒任务，可含工程师可约时段 | `user_recommendations` |
| 客户 | 以**手机号**（`user_id`）标识；演示客户王芳 `13800138000`、李强 `13900000000` | 各表 `user_id/user_phone` |
| 区域软偏好 | 地址命中工程师服务区域城区者优先匹配，无命中回退全量 | `engineer_finder._region_candidates` |

领域对象关系（数据层，详见 §7）：

```
工程师 1──N 排班(忙档，busy 绑工单) ─┐
工程师 1──N 工单(engineer_id，可空/未派单)   → 工单 1──N 转人工(ticket_id 可空：投诉可在无工单时发生)
客户(手机号) 1──N 订单 orders
客户(手机号) 1──N 行为流水 → 聚合出偏好 / 回访推荐
```

---

## 3. 五层架构与模块索引

### 3.1 依赖方向与调用规则

```
Web 层     web/                 页面与 SSE 聊天端点
   ↓ 页面数据直取
API 层     api/                 REST 薄层（参数校验与转发）
   ↓
Agents 层  agents/              AI 编排（唯一允许调用 LLM 的层）
   ↓
Services 层 services/           业务逻辑（纯函数、种子数据）
   ↓
DB 层      db/                  ORM 模型 / 仓储 / 会话
```

- `Web → API → Agents/Services → Repository → ORM`；**禁止反向、禁止跳级**；
- Service 构造签名统一 `(db_path=...)`，内部建 `DatabaseRouter`；测试注入临时库；
- Agents 不直接 import 数据库 Session，一律经 Service/仓储注入；
- Services 层模块内**函数级延迟 import**，规避 Agent↔Service 循环依赖；
- Agents 组件的 LLM 模型在各自组件内（Classifier/Parser/Generator）通过 `config.model_provider` 工厂构建，API Key 缺失时组件自动走降级路径（见 §9）。

**模型分级（M14）**：聊天模型 `create_chat_model(temperature, tier)` 双通道——`tier=main`（生成质量优先：话术/RAG 回答/回访/画像润色）与 `tier=fast`（本地小模型/轻量模型，承接高频结构化短调用）。装配规则：**主管任务分类、咨询相关性判定（YES/NO）、报修槽位抽取** → fast 通道；**报修确认话术与替代推荐文案、RAG 回答生成** → main 通道。控制器各自持有 `llm`（main）与 `structured_llm`（fast）两实例并按上表注入叶子组件；fast 通道未配置（`LLM_FAST_MODEL`/`AZURE_FAST_DEPLOYMENT` 缺省）时 `resolve_channel` 透明回退 main 配置——行为等价、零配置差异，仅在配了小模型时体现成本与延迟收益。

### 3.2 模块索引

**入口 `app.py`**：`create_app()` 注册 7 个 API Router（`api/__init__.py: api_routers`）+ Web Router + 2 个异常处理器（`BusinessException → api_exception_handler`、`Exception → general_exception_handler`）；`startup_event → initialize_system()` 四步独立容错初始化（§9）。

**Agents 层（核心）**

| 文件 | 关键符号 | 职责 |
| --- | --- | --- |
| `task_classification_agent.py` | `TaskClassificationAgent` | 总控：初始化 LLM、StateManager、各子 Agent |
| `task_classification/task_classifier.py` | `TaskClassifier.VALID_CATEGORIES` | LLM 分类，枚举 `appointment/query/complaint/other`，异常→other |
| `task_classification/state_manager.py` | `StateManager` | 三态（+OTHER）持有与合法迁移、reset/force_reset |
| `task_classification/agent_router.py` | `AgentRouter` | route_to_appointment/consultation/**complaint**/route_by_state、转人工落库回执 |
| `task_classification/classification_processor.py` | `ClassificationProcessor` | 主管编排：分类 → `select_tool` 选工具（plan_log 复盘）→ `_invoke_tool` 执行；`_classify_rounds` 上限 >3 强制重置；同步/流式双通道 |
| `task_classification/unrelated_handler.py` | `UnrelatedHandler` | 子 Agent 判无关 → 转回主调度重分类（带轮次上限） |
| `supervisor/tool_registry.py` | `SupervisorToolRegistry` / `ToolSpec` | 主管工具注册表：类别↔工具一一映射、未知类别兜底、清单注入分类 prompt、risk_tier/model_tier 元数据 |
| `appointment_agent.py` | `AppointmentAgent` | 报修流程控制；挂载会话上下文后槽位/窗口由 ctx 提供（无上下文时保持模块内历史，向后兼容） |
| `appointment/input_parser.py` | `InputParser` | LLM 流式输出**纯 JSON 契约**（§5.2），JSONDecodeError 降级字典 |
| `appointment/appointment_processor.py` | `AppointmentProcessor` | 历史合并、确认流处理、完整/不完整分派、成功建单流程 |
| `appointment/engineer_finder.py` | `EngineerFinder` | 匹配：指定工程师档期 / 相似替代推荐 / 技能+区域排序 |
| `appointment/message_builder.py` | `MessageBuilder` | 全确定性话术（追问表/成功/失败/推荐/拒绝/无关/解析失败） |
| `consultant_agent.py` | `ConsultantAgent` | 咨询流控；`consult_stream` 顶部查询短路优先于分类 |
| `consultant/consultation_processor.py` | `ConsultationProcessor` | `try_lookup` 纯函数查库模板（§5.4）；RAG 流程；无关转交 |
| `consultant/consultation_classifier.py` | `ConsultationClassifier` | 咨询域判定（YES/NO） |
| `consultant/knowledge_retriever.py` | `KnowledgeRetriever` | FAISS IndexFlatIP 检索封装 |
| `consultant/response_generator.py` | `ResponseGenerator` | 知识上下文 + LLM 流式生成（透传可选 `background`） |
| `consultant/prompt_builder.py` | prompt | 客服话术与兜底（400 热线/工单号引导）；`build_consultation_prompt(…, background="")` 注入客户背景 |
| `session/session_context.py` | `SessionContext` | 单会话状态束：客户绑定/预约槽位/窗口轮次/滚动摘要；to/from_row 序列化 |
| `session/session_window.py` | `should_roll / roll_oldest / fallback_summary / extract_reply_text` | 短期窗口纯函数：10 轮容量、60% 水位滚动、摘要降级、令牌流回复提取 |
| `session/agent_session_registry.py` | `AgentSessionRegistry / SessionRuntime` | 每会话惰性建 Agent 图 + LRU(8) + per-session 锁 + 快照写穿与按行还原 |
| `user_behavior_agent.py` | `UserBehaviorAgent` | 行为记录、档案分析、回访消息（含可约时段） |
| `user_behavior/behavior_recorder.py` | `BehaviorRecorder` | 行为入库（repair/consultation）、统计、清理 |
| `user_behavior/preference_manager.py` | `PreferenceManager` | 四类偏好更新（`engineer_id/time_period/product_type/fault_type`） |
| `user_behavior/pattern_analyzer.py` | `PatternAnalyzer` | 常用品类/故障/时段/工程师统计、30 天回访判定 |

**Services 层**

| 服务 | 职责要点 |
| --- | --- |
| `ticket_service.py` | 建单（AX 单号）、`ALLOWED_TRANSITIONS` 状态机、派单/换人（档期校验+忙档绑定）、完成/取消释放忙档 |
| `order_service.py` | 演示订单种子（5 条）、`get_warranty_info(phone)` 动态保修判定 |
| `handover_service.py` | 转人工记录 create/list（可关联工单） |
| `engineer_service.py` | 8 名默认工程师种子、查询、`is_engineer_available`、区域/技能取数 |
| `knowledge_service.py` | 默认 12 条知识、CRUD、FAISS 索引构建/重建、检索（top_k、分类过滤） |
| `recommendation_service.py` | 后台定时任务：保修 30 天内到期提醒 + 维修完成满意度回访，写 `user_recommendations` |
| `dream_service.py` | AutoDream 沉淀：资格扫描 → 增量回放 → 置信度/降权 → profile 记忆 → checkpoint 落库；守护线程定时 + `run_immediate_check` 手动触发（风格同 recommendation） |
| `dream_policy.py` | AutoDream 纯策略函数：`activity_stats/is_eligible/replay_events/aggregate_profile/preference_upsert_deltas/stale_downweight_rows/build_profile_text`（离线可测） |
| `user_behavior_service.py` | 行为/偏好/推荐的仓库转发与统计 |
| `chat_session_service.py` | 会话快照读/写/绑定：`ChatSessionService(db_path)`（upsert 全量覆盖、load、bind_user、list_sessions） |
| `memory_service.py` | 长期记忆读写与召回：`add_memory`（同用户同内容去重）、`recall(query, top_k=5)`（0.6 语义+0.3 时效+0.1 重要度，embedding 不可用自动降级）、`extract_phone`、`maybe_bind_session`、`upsert_profile_memory`（画像记忆：内容一致或余弦 ≥0.92 视为重复跳过，否则软删旧画像后写入，至多一条活跃 profile） |
| `text_embedding.py` | `embed_input`、`find_best_match_indices(query, candidates)`（IndexFlatL2 排序下标）；工程师向量缓存 `data/engineer_embeddings.pkl` |

**DB 层**

| 文件 | 内容 |
| --- | --- |
| `models.py` | 12 张表（§7） |
| `db_router.py` | `DatabaseRouter`（属性 `engineers/knowledge/user_behavior/tickets/orders/handovers/chat_sessions/user_memories/dream_checkpoints`，内部 `session_manager`）；另含 Engineer/Knowledge/UserBehaviorDBRouter 兼容类 |
| `repositories/` | engineer / ticket / order / handover / knowledge / user_behavior / chat_session / user_memory / dream_checkpoint 九个仓储 |
| `base/interfaces.py` | 10 个抽象基类：BaseEngineer / BaseSchedule / BaseRepairTicket / BaseOrder / BaseHumanHandover / BaseKnowledge / BaseUserBehavior / BaseChatSession / BaseUserMemory / BaseDreamCheckpointRepository |
| `base/session_manager.py` | `SessionManager(db_path)` 构造即 `create_all` + 会话工厂；**删库文件 = 零迁移重置** |

**Config 层**：`constants.py`（`StateEnum` + `SharedState`）、`database.py`（`DatabaseConfig`，env `DATABASE_URL/DB_ECHO/...`）、`model_provider.py`（`create_chat_model(temperature, tier)/create_embedding_model`，env `LLM_*`/`EMBEDDING_*`，支持 openai/qwen/deepseek/zhipu/azure/openai-compatible；**模型分级（M14）**：`tier ∈ main/fast`——fast 通道读 `LLM_FAST_MODEL`/`MODEL_FAST_PROVIDER`（Azure 为 `AZURE_FAST_DEPLOYMENT`），未配置透明回退 main 配置；`resolve_channel/channel_label` 纯函数供解析与日志）、`settings.py`、`time_config.py`（§6.6 唯一时间事实源）。

**Web 层**：`web/routes.py` + `templates/`（index / tickets / engineers / engineer_schedules / knowledge_management / follow_ups）+ `static/styles.css`。`api/chat_handler.py` 经 `AgentSessionRegistry` 按 `session_id` 取/建会话运行时（每会话独立 Agent 图 + `SessionContext`），处理期持 per-session 锁，结束时写穿 `chat_sessions`（详见 §4.1）；前端用 `localStorage` 固定 `session_id`。

---

## 4. 对话状态机与令牌协议

### 4.1 状态机

`StateEnum`（config/constants.py）：`CLASSIFY / APPOINTMENT / CONSULT / OTHER`（OTHER 为枚举兜底值）。

```
                     ┌──────────────────────┐
        (每轮)        │      CLASSIFY         │
  ───────────────▶   │   LLM 意图分类         │
                     └──────────┬───────────┘
              ┌─────────────────┼──────────────────┐
      appointment          query/咨询类          complaint / other
              ▼                 ▼                  │
     ┌────────────┐   ┌────────────┐               │
     │APPOINTMENT │   │  CONSULT   │               │
     │ 报修专员     │   │ 售后顾问     │               │
     └────────────┘   └────────────┘               │
              └─────────┴───────────┘               ▼
                  任务结束/无关转交         单轮处理：投诉落库+回执；
                  → reset_to_classify     other：能力清单兜底话术
```

- `StateManager.should_classify()`：当前 CLASSIFY（或 None）→ 本轮走分类；APPOINTMENT/CONSULT → 直接续接子 Agent 多轮对话，不重复分类；
- 合法迁移 `can_transition_to`：CLASSIFY → {APPOINTMENT, CONSULT}；APPOINTMENT/CONSULT → {CLASSIFY}；`force_reset()` 供递归兜底；
- **转回重分类上限**：`ClassificationProcessor._classify_rounds`，子 Agent 判无关交回主调度时 +1，>3 则清零并强制 reset 后走 other 兜底（防递归死循环）；
- **投诉**在调度层单轮完成（§5.6），**不新增状态枚举**；
- **会话化（按 session_id 隔离）**：`api/chat_handler.ProcessUserInput_stream` 经 `AgentSessionRegistry.get(session_id)` 取/建 `SessionRuntime`（每会话独立 Agent 图 + `SessionContext`，进程内 LRU 上限 8）；同会话并发请求以 per-session `asyncio.Lock` 串行；每轮结束把（状态机值/客户绑定/槽位/窗口/摘要）写穿 `chat_sessions` 行 → **页面刷新、LRU 淘汰、进程重启均按行还原**，多会话互不串场；
- **客户绑定**：消息中出现 11 位手机号（或续接上轮槽位内手机号）即绑定 `ctx.user_id`，绑定客户的本轮消息先做长期记忆召回（Top-5）注入子 Agent 背景；报修成功/咨询完成沉淀新记忆。

### 4.2 令牌协议（SSE 纯文本流，前端按行解析）

| 令牌 | 语义 | 前端处理 |
| --- | --- | --- |
| `[THOUGHT][客服调度/报修专员/售后顾问] …` | 过程思考（可解释性） | 灰色小字 |
| `[REPLY][角色]\n…` | 对客户说的正文 | 气泡 |
| `[SIGNAL]recommendation_pending` | 相似工程师推荐待客户确认 | 静默（不渲染，仅驱动状态） |
| `[ERROR]…` | 链路异常 | 红色告警 |

前端气泡着色（index.html 关键词判定，与 `message_builder.py` 成功文案同源约定）：正文含 `报修单 / 已为您安排 / 上门` → 成功绿；含 `抱歉 / 失败 / 暂无` → 错误样式；两者皆无（追问）→ 强调蓝；`[ERROR]` 前缀独立红色渲染。

---

## 5. Agent 契约

### 5.1 任务分类契约

- **输出枚举**（`VALID_CATEGORIES`）：`appointment`（报修/预约上门）、`query`（售后咨询+订单保修/工单进度查询）、`complaint`（投诉/转人工）、`other`（无关兜底）；
- Prompt 内嵌每个类别的家电例句（“空调不制冷，帮我预约个师傅上门看看”→appointment 等 5 例），LLM 输出归一化（小写/去空白）；
- **主管 ReAct 工具化（M14）**：子 Agent / 确定性流程登记为主管工具（`agents/supervisor/tool_registry.py`：`repair_booking` 报修登记 / `aftersale_consult` 售后咨询 / `human_handover` 转人工 / `fallback_reply` 兜底），每个 `ToolSpec` 携带 `category`（与分类枚举一一对应）、`handler`（处理方）、`risk_tier`（read/confirm/write，M15 权限门禁白名单源头）、`model_tier`（结构化入口 fast）；工具清单注入分类 prompt——**分类即工具选择**；`ClassificationProcessor.select_tool` 落选并写 `plan_log`（category→tool_id 规划复盘，供审计），`_invoke_tool` 按 handler 分发（处理方缺失自动落到兜底工具）；
- **观察 → 再规划**：子 Agent 判无关转回（`handle_unrelated_async`）即主管重新规划；单条消息 `_classify_rounds > 3` 强制重置并兜底（防递归）；
- LLM 异常/超时/无 Key → 返回 `other`，调度层给“抱歉 + 能力清单”（`AgentRouter.handle_unsupported_task` / `get_available_services`）兜底。

### 5.2 报修信息抽取契约（InputParser）

LLM 输出**纯 JSON**（禁止 markdown 代码块），字段契约（JSON Schema 形式）：

```jsonc
{
  "product_type":  "string  // 家电品类，如 空调/冰箱/洗衣机/热水器/净水器/烟灶；无法判断='未知'",
  "fault_desc":    "string  // 故障现象，如 不制冷/漏水/异响；没有='未知'",
  "address":       "string  // 上门地址（保留城区+小区门牌口述）；没有='未知'",
  "phone":         "string  // 11 位手机号；没有='未知'",
  "start_time":    "string  // 期望上门起始，标准格式 YYYY-MM-DD HH:MM；只说时间没日期默认今天；完全没有='未知'",
  "engineer_name": "string  // 用户指定工程师姓名；否则='未知'（未指定由系统推荐）",
  "confirmation":  "string  // 回应推荐确认问题的原文（是/好/可以/不/不要…）；否则='未知'",
  "info_complete": "boolean // 仅当 product_type/fault_desc/address/phone/start_time 五项均非'未知'为 true；engineer_name 不影响",
  "unrelated":     "boolean // 与报修预约无关（聊天/天气…）为 true；推荐确认回复不得标记 unrelated",
  "missing_info":  "string[] // info_complete=false 时列出缺失键，如 [\"start_time\",\"phone\"]"
}
```

- 必需字段常量 `REQUIRED_FIELDS = [product_type, fault_desc, address, phone, start_time]`（appointment_processor.py）；
- **确定性校验**（不信任 LLM）：phone 必须 `^1\d{10}$`；start_time 可解析且须在 9:00–18:00 窗口且不早于当前（`_is_valid_start_time`）——校验不过按缺失处理并追问；
- **历史合并规则**（`update_history_from_data`）：会话内字段**只增不覆盖**，补齐信息不丢旧值；等待确认态优先走 `_handle_recommendation_response`（positive 词集“是/好/可以/行/确定…”，negative 词集“不/不要/不行/别…”）；
- **容错降级**：JSONDecodeError → 全 `'未知'` + `info_complete=False` + `missing_info=["所有信息"]` 字典，流程转追问不崩；
- 追问字典 `missing_info_prompts`（按 REQUIRED_FIELDS 逐项话术，含品类列举、地址城区引导与 9:00–18:00 槽位提示）

### 5.3 工程师匹配契约（EngineerFinder）

| 路径 | 逻辑 | 出口 |
| --- | --- | --- |
| 客户指定工程师 | 查该工程师档期（busy 重叠判定） | 有空 → 直接匹配；没空 → 走替代推荐 |
| 替代推荐 | 排除目标后按「区域软偏好（地址命中城区优先）→ skills 向量相似排序（IndexFlatL2，embedding 不可用保持原序）→ 逐个查空闲」 | 找到 → `{engineer, requires_confirmation: True}` + `[SIGNAL]recommendation_pending`；区域无候选回退全量再查一次 |
| 客户未指定 | 匹配文本 = product_type + fault_desc；先区域候选后技能排序，再查空闲 | 找到 → 直接匹配；全量无空闲 → 失败文案（改时间/热线） |

- 确认流：推荐后等待客户明确回复（positive → 按推荐派单；negative → `create_recommendation_declined_message` 尊重原选择，确定性文案）；回复前**不 reset 会话**；
- 时间默认 `DEFAULT_SERVICE_MINUTES = 120` 分钟（`parse_repair_time`，写排班 end_time）；
- **成功建单路径**（`AppointmentProcessor._process_successful_repair`，全确定性零 LLM）：`TicketService.create_ticket`(pending) → `assign_ticket`(busy+assigned) → 行为记录 → 按手机号+品类查订单拼**保修提示**（在保：免上门费/维修费；超保：付费维修口径）→ `MessageBuilder.create_appointment_success_message`（含报修单号 AX…/工程师/上门时段）；任务结束 reset 会话。

### 5.4 售后顾问契约（ConsultationProcessor）

**① 查询短路（先于一切 LLM，纯函数查库模板）**

| 触发 | 判定 | 输出 |
| --- | --- | --- |
| 工单进度 | `AX\d{10,14}`（容忍 11–14 位纯数字 + 含 工单/进度/报修单/单号 意图词） | 状态标签（pending=待派单/assigned=已派单…）+ 工程师 + 上门时间 + 关闭时间 |
| 保修查询 | `1\d{10}` 手机号 + （保修/在保/过保/超保/质保/出保/保内/保外/订单…）关键词 | 名下订单逐条：在保（保修至…）/ 超保（已超出，保修至…），含购买日与剩余天数 |

`try_lookup` 为公开探测（consult_stream 顶部短路返回并复位状态——**防顾问二次分类误判转回的关键**）；无 Key 时查询仍可用。

**② RAG 路径**（未命中短路时）：`ConsultationClassifier` 判非咨询 → 无关转回调重分类；是咨询 → `KnowledgeRetriever.search`（top_k=3，向量化 → IndexFlatIP → 候选 ×2 → category 过滤；文档无向量返回空）→ `ResponseGenerator` 知识上下文 + LLM 流式；结束后行为记录（失败仅告警）并复位 CLASSIFY。

### 5.5 用户行为 / 回访契约

- **行为记录**：`action_type ∈ {repair, consultation}`；报修行为 `action_data` 含 product_type/fault_desc/address/start_time 等，`engineer_id` 独立列；
- **偏好四类**：`engineer_id`（存工程师 ID 字符串）、`time_period`（9-18 内分 上午/下午）、`product_type`、`fault_type`；`confidence_score` 出现一次 +1（仓库层自增），`last_updated` 刷新；
- **回访判定**（`PatternAnalyzer.should_send_return_reminder`）：`total_repairs ≥ 1` 且 `距上次报修（北京时间 naive 天数差）≥ 30`；
- **可约时段**（`_query_engineer_available_times`）：查常用工程师今天/明天 9:00–18:00 内空闲整点槽（今天从下一整点起），最多 3 个，输出 `AvailableSlot{date, time, formatted}`；
- **话术生成**：LLM prompt 注入偏好摘要 + 可约时段，产出保养建议/保修到期/维修后回访三类话术；LLM 失败降级为确定性模板消息（§9）。

### 5.6 投诉转人工契约（调度层单轮）

输入：投诉/转人工意图（调度分类为 complaint）→ `AgentRouter.route_to_complaint`：抽取诉求摘要（去意图词）→ `HandoverService.create(user_name?, user_phone?, issue_summary, ticket_id?=None)` 落库 → 回执话术「已为您登记转人工处理，售后专员将在 30 分钟内回电 {手机号}」→ 状态重置 CLASSIFY。投诉可能发生在无工单时，故 `human_handovers.ticket_id` 可空。

### 5.7 AutoDream 沉淀契约（DreamService，M13）

- **资格**（`dream_policy.is_eligible`）：`session_count ≥ 5 且 span_hours ≥ 24`；会话数 = 行为事件去重 session_id + 无有效会话ID行为（`default_session`/空）按隐式会话计 + 已绑定 `chat_sessions` 行 session_id 的并集；span = 全部活动时间戳（行为 created_at ∪ 会话 created_at）首末跨度（相对差，纯比较）；
- **增量回放**（`replay_events`）：只取 `id > checkpoint.last_event_id` 的事件按时间升序处理——同批事件重复运行天然空集，**幂等**；checkpoint 在 `dream_checkpoints` 每人一行（`run_count`/`total_events_processed` 仅在 processed > 0 时累计）；
- **任务锁**：进程内 per-user `threading.Lock` + 行级锁（`try_acquire_lock`：is_running=0 可抢；`running_started_at` 超 30 分钟视为崩溃残留自动接管；`release_lock` 归还）；
- **置信度更新**：新事件逐条 `update_user_preference`（engineer_id ← 行为 engineer_id、product_type/fault_type ← action_data 的 product_type/fault_desc、time_period ← start_time 分上午/下午），同值 +1；
- **冲突降权**（`stale_downweight_rows`）：某维度本周期新值**只出现单一值**时，同维度已存的其他值行整体 `confidence = max(1, conf // 2)`——被新值持续压制的旧偏好自动淡出（多值分散使用不降权）；
- **画像记忆**（`aggregate_profile` → LLM 润色 → `MemoryService.upsert_profile_memory`）：聚合该客户**全量**行为成画像文本；LLM 不可用走 `build_profile_text` 确定性模板；内容一致或语义余弦 ≥0.92 视为重复跳过，实质变化才软删旧画像并写新条（memory_type=profile、重要度 0.7、每人至多一条活跃）；
- **调度**：`start_scheduler` 守护线程每 60 分钟 `scan_and_consolidate`（扫描 `user_behaviors` 全客户，资格不足者零副作用跳过）；app.py 启动事件加载；`run_immediate_check()` 手动触发；LLM 润色通道可整体关闭（`profile_text_llm=None`，离线测试/无 Key 环境自动走模板）。

---

## 6. 确定性逻辑规范

> 本节全部为纯函数/纯查库逻辑，离线可测（`tests/test_offline_services.py` 覆盖）。

### 6.1 工单号生成

`AX + %Y%m%d + 当日序号(2 位补零)`；当日序号 = 当日以该前缀开头的工单计数 +1（`ticket_repository.generate_ticket_no`，同日内递增、跨日归零）。格式校验 `AX\d{12}`。

### 6.2 工单状态机（`ALLOWED_TRANSITIONS`）

| 当前 \ 目标 | assigned | in_progress | completed | cancelled |
| --- | --- | --- | --- | --- |
| pending | ✔ | ✘ | ✘ | ✔ |
| assigned | —（走换人接口） | ✔ | ✘ | ✔ |
| in_progress | ✘ | — | ✔ | ✔ |
| completed / cancelled | ✘ | ✘ | ✘ | ✘（终态） |

- `update_status`：非法状态值或非法流转返回 None（API 层转 400）；流转合法则更新；
- 到达 `completed / cancelled`：写 `closed_at = TimeConfig.naive_now()` 并 `release_schedule_by_ticket(ticket_id)`（**释放工程师忙档**——完成与取消都要释放，历史 bug 已修，见 git cf46f09）；
- `assign_ticket` 仅允许 pending/assigned；`change_engineer` = 释放原忙档 → 置回 pending → 重新派单。

### 6.3 派单与档期冲突

- `is_engineer_available(engineer_id, start, end)`：工程师在 [start, end) 不存在 **busy 时段重叠**即可用（排班行含 free/busy 两类，冲突仅看 busy）；
- 派单成功：写 `engineer_schedules` busy 行（ticket_id 绑定）→ 工单置 assigned；工单结束（6.2）释放该 ticket 对应的 busy 行，工程师可承接新单；
- 派单失败（档期冲突）：工单保持原状态可改派，API 返回 400 + 冲突说明。

### 6.4 保修期计算（动态，无存储状态）

```
warranty_end = purchase_date + timedelta(days=365 × warranty_years)
in_warranty  = warranty_end > TimeConfig.naive_now()
days_left    = (warranty_end - now).days   # 超保时为 0
```

边界测试锚点：购买 364 天（1 年保）→ 在保；366 天 → 超保。注意 `365 × 年` 非日历对齐。

### 6.5 服务窗口与默认时长

- 唯一事实源 `TimeConfig.get_business_hours() → (9, 18)`；业务窗口、槽位提示、排班网格、回访时段枚举一律引用它，禁止硬编码 12/22 等旧值；
- 默认上门维修窗口 120 分钟（`DEFAULT_SERVICE_MINUTES`）。

### 6.6 时间口径（历史坑，勿改）

- 全系统业务时间 = **北京时间 naive datetime**：`TimeConfig.naive_now()`（北京时区去掉 tzinfo，与库内 naive 存储可比）；`TimeConfig.now()` 返回 aware（格式化输出用）；
- `parse_datetime` **必须返回 naive**——曾改为 aware 导致与库内 naive 比较抛 TypeError（历史 bug，勿改回）；
- 模型列默认值 `datetime.utcnow`（UTC naive）仅作审计时间戳，业务比较一律走 `naive_now`。

### 6.7 知识库检索与软删除

- 软删除字段 `is_active`（0=删除）：检索、列表、索引重建全部过滤 `is_active=1`；
- FAISS 索引：`IndexFlatIP` 内积；检索 `top_k`（默认 3，API search 默认 5）时先取 `top_k × 2` 候选再做 category 过滤补齐；知识增/改/删后重建索引；
- 工程师技能匹配用 `IndexFlatL2` 最近邻：`find_best_match_indices(query_text, candidates)` 返回按相似度降序下标；embedding 不可用（无 Key）时**保持候选原顺序**（不崩、不截断）。

### 6.8 偏好置信度

`update_user_preference`：存在同 (user_id, preference_type, preference_value) 行则 confidence_score+1 并刷新 last_updated，否则新建（置信度 = 出现次数）。

**写入方**：① 用户行为 Agent 实时行为（一次 +1）；② AutoDream 增量回放（M13，`DreamService`）对 checkpoint 之后的新事件逐条累计，并在单一新值窗口下对同维度旧值执行 `max(1, conf // 2)` 冲突降权（§5.7）——同一列两种写入路径共用仓库层，口径一致。

---

## 7. 数据模型与种子数据

### 7.1 表结构（12 张，db/models.py 逐一对应）

```
engineers ──1:N── engineer_schedules(busy 行 ticket_id ──)──► repair_tickets
engineers ──1:N── repair_tickets.engineer_id(可空=未派单) ──1:N── human_handovers.ticket_id(可空)
orders（独立，按 user_phone 查询）
knowledge_documents（独立，FAISS 索引内存构建）
user_behaviors ──聚合──► user_preferences / user_recommendations（均按 user_id=手机号）
chat_sessions（会话快照，按 session_id 单行覆盖写）
user_memories（按 user_id=手机号 的长期记忆流水，软删除）
dream_checkpoints（按 user_id 单行：AutoDream 回放断点 + 任务锁）
```

| 表 | 字段 | 说明 |
| --- | --- | --- |
| `engineers` | id, name(unique), skills(Text 品类专长), service_region | 无 gender/strength（M2 移除） |
| `engineer_schedules` | id, engineer_id(FK), start_time, end_time, status('busy'/'free'), ticket_id(可空，busy 时绑定) | 冲突判定 = busy 行与窗口重叠 |
| `repair_tickets` | id, ticket_no(unique), user_name(可空), user_phone, product_type, fault_desc(Text), address(Text), start_time, end_time, status, engineer_id(FK 可空), created_at, updated_at, closed_at(可空) | 状态机见 6.2 |
| `orders` | id, user_phone(index), user_name(可空), product_type, brand_model, purchase_date(DateTime), warranty_years(default 3), created_at | 保修动态计算（6.4） |
| `human_handovers` | id, user_name(可空), user_phone, ticket_id(FK 可空), issue_summary(Text), created_at | 投诉可发生在无工单时 |
| `knowledge_documents` | id, content(Text), category, keywords(JSON 列表), embedding(JSON 向量), created_at, updated_at, **is_active**(软删标记, default 1) | 检索/重建仅看 is_active=1 |
| `user_behaviors` | id, user_id(默认 'guest'；实际为手机号), action_type('repair'/'consultation'), action_data(JSON), engineer_id(FK 可空), session_id(可空), created_at | 行为流水 |
| `user_preferences` | id, user_id, preference_type('engineer_id'/'time_period'/'product_type'/'fault_type'), preference_value, confidence_score(default 1), last_updated | 置信度累加（6.8） |
| `user_recommendations` | id, user_id, recommendation_type('warranty_expiry_reminder'/'satisfaction_followup'/'maintenance_advice'), content(Text), engineer_id(FK 可空), is_sent(default 0), created_at, sent_at(可空) | 调度器/回访页产出 |
| `chat_sessions` | id, session_id(unique,index), user_id(index,可空), state_value, appointment_slots(JSON), message_window(JSON), summary_text(Text), created_at, updated_at | 会话快照单行覆盖写：槽位/窗口/摘要全量 JSON；`user_id` 空 = 未识别客户 |
| `user_memories` | id, user_id(index), content(Text), memory_type('repair'/'consult'/'preference'/'profile'), importance(Float,默认0.5), embedding(JSON 向量,可空), source_session_id(可空), created_at, updated_at, is_active(默认1) | 长期记忆：repair 0.8 / preference 0.6 / consult 0.5 / profile 0.7；软删除；同用户同内容去重；profile 每人至多一条活跃（M13 轮换） |
| `dream_checkpoints` | id, user_id(unique,index), last_event_id(default 0), run_count, total_events_processed, is_running(默认0), running_started_at(可空), last_run_at(可空), last_status(可空), last_error(Text,可空), created_at, updated_at | AutoDream 每人一行：回放断点（幂等）+ 任务锁（超 30 分钟接管） |

### 7.2 种子数据（启动自动播种，幂等：表非空即跳过；删 `data/` 即重置）

**8 名默认工程师**（`EngineerService.default_engineers`，覆盖 6 品类 × 城区，部分品类冗余以支撑替代推荐）：

| 姓名 | 专长品类 | 区域 |
| --- | --- | --- |
| 张建国 | 空调（制冷/加氟/安装保养）+ 烟灶 | 海淀区 |
| 李卫东 | 冰箱（不制冷/压缩机/门封条） | 朝阳区 |
| 王海涛 | 洗衣机（滚筒/波轮/异响漏水） | 海淀区 |
| 刘志强 | 热水器（燃/电、打不着火漏水） | 朝阳区 |
| 陈国华 | 净水器（滤芯/漏水不出水） | 丰台区 |
| 赵文斌 | 中央空调柜机挂机/移机加氟 | 丰台区 |
| 孙建军 | 烟灶（排烟/点火） | 西城区 |
| 周永康 | 冰箱洗衣机综合（制冷系统/电机排水） | 西城区 |

**5 条演示订单**（`OrderService.DEMO_ORDERS`）：王芳 `13800138000`：空调 2024-06-15 购 6 年保（在保）、冰箱 2025-01-10 购 3 年保（在保）、洗衣机 2021-03-20 购 3 年保（已超保）——覆盖在保/超保双话术；李强 `13900000000`：热水器 2025-08-02 购 3 年保、烟灶 2022-05-12 购 3 年保。

**12 条默认知识**（`KnowledgeService.default_knowledge`，10 个 category）：品牌服务 ×1、保修政策 ×1、收费标准 ×1、故障排查 ×3、退换货 ×1、预约政策 ×1（9:00–18:00）、延保服务 ×1、安装服务 ×1、保养服务 ×1、安全提示 ×1。

---

## 8. API 与 Web 路由全表

### 8.1 Web 层（web/routes.py，页面走 Jinja2；对话走 SSE）

| 方法/路径 | 说明 |
| --- | --- |
| `GET /` | 智能客服聊天主页 |
| `POST /chat/stream` | **主聊天端点**：text/event-stream 逐 token 输出（§4.2）；请求体 `ChatRequest{message, session_id?}`；`session_id` 缺省时后端生成并通过响应头 `X-Session-Id` 下发（前端 localStorage 固定复用）；内部 = `ProcessUserInput_stream(message, session_id)`（§4.1） |
| `POST /chat` | 兼容端点（同 /chat/stream 语义，`session_id` 可空同上） |
| `GET /tickets` | 报修工单管理页 |
| `GET /engineers` | 工程师管理页 |
| `GET /engineer_schedules` | 今日排班网格（9:00–18:00） |
| `GET /knowledge` | 知识库管理页 |
| `GET /follow_ups` | 售后回访页 |

### 8.2 API 层（注册于 app.py：7 个 Router，见 api/__init__.py）

| Router / Prefix | 端点 | 契约要点 |
| --- | --- | --- |
| task `/api/task` | `POST /classify` | 复用聊天链路多 Agent 编排；请求 `{text}` → `DataResponse`（**兼容遗留**，主链路走 /chat/stream） |
| consultation `/api/consultation` | `POST /ask` | `{question}` → `{answer, question}`（兼容遗留） |
| appointment `/api/appointment` | `POST /create` | 兼容遗留（请求模型仍为旧预约语义词，见 8.3） |
| knowledge `/api/knowledge` | `GET ""` 列表；`POST ""` 新增；`GET/PUT/DELETE /{knowledge_id}`；`POST /search` | 增删改 `{content, category, keywords}`；search `{query, top_k=5, category?}` → `{status, query, results, total_found}`；删除软删（is_active=0）后重建索引 |
| engineer `/api/engineers` | `GET ""` 全部；`GET /schedules/today`；`GET /{engineer_id}`；`GET /{engineer_id}/schedule` | EngineerResponse{id,name,skills,service_region}；ScheduleResponse{id,engineer_id,start_time,end_time,status,ticket_id?}；**静态段 `/schedules/today` 必须先于动态段 `/{engineer_id}` 声明**（已满足） |
| ticket `/api/tickets` | `GET ""`（按 status/phone 过滤）；`POST ""` 创建；`GET /handovers`；`GET /{ticket_id}`；`POST /{ticket_id}/assign`；`POST /{ticket_id}/status` | 创建 TicketCreate{user_name?, user_phone, product_type, fault_desc, address, start_time, end_time?, engineer_id?}（带 engineer_id 立即尝试派单，冲突 400 保留 pending）；assign/status 返回 OperationResult{status, message, data?}，非法流转 400；**`/handovers` 必须先于 `/{ticket_id}` 声明**（已满足） |
| follow_ups `/api/follow_ups` | `GET /stats`；`POST /analysis`；`POST /reminder` | analysis `{phone}` → 档案摘要（偏好/工单统计/应回访）；reminder `{phone}` → `ReminderResponse{phone, message, engineer_available_times:[AvailableSlot{date,time,formatted}]}`（engineer 与槽位结构同 user_behavior_agent 输出，历史 bug 已修）；stats → 运营计数 |

### 8.3 兼容遗留端点说明（如实披露）

`api/core/response_models.py` 中 `AppointmentRequest{user_id, service_type, preferred_time, notes}`、`AppointmentResponse`、`ConsultationRequest/Response`、`TaskClassificationRequest/Response` 等仍保留**改造前的预约语义词**（service_type/preferred_time…）。主聊天链路不经过它们（走 `/chat/stream`），仅 `/api/task|consultation|appointment` 三个演示入口使用。如需彻底对齐可后续替换为报修语义模型。

---

## 9. 降级与容错矩阵

| 环节 | 兜底行为 | 位置 |
| --- | --- | --- |
| 启动初始化（无 Embedding Key 等） | 4 步各自独立 try/except，失败仅告警不阻断启动 | `app.py: initialize_system` |
| 知识库初始化失败 | 跳过索引构建；检索返回空 → 顾问给兜底话术 | knowledge_service.initialize / 检索 |
| 任务分类 LLM 异常/超时/无 Key | 归类 `other` + 能力清单话术 | task_classifier.classify_task |
| 分类 ↔ 子 Agent 转回 >3 轮 | `_classify_rounds` 清零 + force_reset + other 兜底 | classification_processor |
| 报修抽取 JSON 解析失败 | 全“未知”降级字典 + 逐项追问 | input_parser.parse_data |
| 时间非法（过期/不在 9-18） | 拒绝写入该字段并追问，提示服务窗口 | appointment_processor `_is_valid_start_time` |
| 指定工程师档期冲突 | 技能相似 + 区域软偏好推荐替代 + 请求确认 | engineer_finder.find_similar_available_engineer |
| 无任何空闲工程师 | 确定性失败文案（建议改时间/拨打热线） | message_builder failure |
| 推荐回复无法解析 | 不 reset，提示明确回复“是/不” | appointment_processor awaiting |
| Embedding 不可用 | 工程师候选保持原顺序；知识无向量跳过检索；记忆语义分置空 → 按归一化 (0.3×时效+0.1×重要度)/0.4 召回 | text_embedding / knowledge_service / memory_scoring |
| 查询类意图（保修/进度）误判 | 纯函数短路先于一切 LLM 与二次分类 | consultation_processor.try_lookup |
| 行为记录失败 | 仅日志告警，不影响主流程 | recorder / processor |
| 回访话术 LLM 失败 | 确定性模板话术（含可约时段） | user_behavior_agent fallback |
| AutoDream 画像润色 LLM 失败/无 Key | `dream_policy.build_profile_text` 确定性模板（数据较少给提示文案） | dream_service profile_text_llm |
| AutoDream 任务锁冲突 | 返回 `locked` 跳过该客户本轮；崩溃残留（>30 分钟）自动接管 | dream_checkpoint_repository |
| AutoDream 行级并发 | 进程内 per-user 线程锁串行，同用户不并行沉淀 | dream_service._thread_locks |
| 主管选了不可用工具 | 处理方缺失（Agent 未挂载）自动落到兜底工具，不崩不静默 | classification_processor._invoke_tool |
| 分类结果非白名单 | 注册表 `by_category` 兜底 `fallback_reply` + 规划复盘记录原始类别 | supervisor.tool_registry |
| fast 通道未配置/不可用 | `resolve_channel` 透明回退 main 同配置模型（行为等价），仅装配差异 | model_provider.resolve_channel |
| 派单冲突（后台操作） | 400 + 冲突说明，工单保留 pending 可改派 | api/ticket.py |
| 非法状态流转 | 服务层白名单拒绝 → API 400 | ticket_service.update_status |
| 滚动摘要 LLM 失败 | 截断拼接 + 「（早期对话截断）」标记，窗口正常滚动 | session_window.fallback_summary |
| 会话快照写穿失败 | 仅日志告警，已流出的回复不受影响；下轮成功后整份覆盖 | chat_handler / registry.persist |
| 前端 SSE 异常令牌 | `[ERROR]` 红色渲染，不吞不静默 | index.html |

---

## 10. 测试策略与工程约定

### 10.1 测试设计（172 项全离线，零 API Key 依赖）

| 文件 | 覆盖 |
| --- | --- |
| `test_task_classification_agent.py` | 分类枚举完整性、prompt 家电化断言、非法类别（pay/statistics）→ other 兜底、归一化 |
| `test_appointment_agent.py` | 信息抽取 JSON 契约解析、历史合并行为 |
| `test_consultant_agent.py` | 工单号/保修查询模板答复（含纯数字单号容错） |
| `test_offline_services.py` | 工单全生命周期（合法/非法流转、完成与取消均释放忙档）、档期冲突与换派、保修 364/366 天边界、演示订单幂等播种、工程师种子覆盖（8 人/6 品类）、转人工记录 |
| `test_user_behavior_agent.py` | 行为记录与偏好置信度、常用偏好识别、30 天回访判定、话术无旧版残留 |
| `test_memory_recall.py` | 召回打分纯函数：0.6/0.3/0.1 权重、30 天时效衰减、无语义分归一化、Top-K 排序 |
| `test_session_window.py` | 窗口纯函数：60% 水位触发滚动、`extract_reply_text` 令牌段提取、摘要降级截断 |
| `test_session_isolation.py` | 两会话行/ctx 互不串场、状态机值按会话还原、LRU 淘汰后按行重建、AppointmentAgent 上下文模式 |
| `test_memory_binding.py` | 手机号提取/会话绑定、默认重要度与去重、软删除、无语义分召回降级（embedding 一律离线禁止） |
| `test_chat_handler_session.py` | 入口链路：绑定/匿名、多轮窗口写穿、重启还原续谈、交错会话隔离、召回注入 ctx.recalled |
| `test_dream_policy.py` | AutoDream 纯函数：会话/跨度资格边界（≥5 且 ≥24h）、增量回放幂等、画像聚合排序、偏好增量、冲突降权、模板文案 |
| `test_dream_service.py` | AutoDream 集成：资格不足跳过、全量沉淀（置信度/画像记忆/checkpoint）、二次空跑不重复、增量续跑、漂移降权、LLM 通道与兜底、任务锁接管、批量扫描 |
| `test_tool_registry.py` | 主管工具注册表：四工具元数据（类别覆盖/风险分级/模型分级）、类别↔工具映射与未知兜底、清单注入分类 prompt、假路由器验证"观察→选工具→执行"分发与规划复盘、处理方缺失兜底 |
| `test_model_tier.py` | 模型分级：fast 未配置透明回退 main、模型/提供商/Azure 部署覆盖、非法 tier 拒绝、按 tier 构造、控制器装配（分类/判定/抽取接 fast、话术/RAG 生成接 main） |

`conftest.py` 夹具：`FakeChatModel`（可 `prompt | llm` 组合的同步替身）、`temp_db_path`（独立临时库）、`tmp_engine`。会话类测试以 `monkeypatch` 将 `chat_handler` 单例指向临时库，并把运行时 Agent 图的分类流替换为假流（不触网）。

### 10.2 常用命令

```bash
pytest                    # 全量 172 项离线
pytest tests/test_offline_services.py -q   # 确定性纯逻辑（状态机/保修/档期）
pytest tests/test_dream_policy.py tests/test_dream_service.py -q  # AutoDream 策略/沉淀链路
pytest tests/test_tool_registry.py tests/test_model_tier.py -q  # 主管工具化/模型分级
python services/dream_service.py     # 服务自测块：起调度器（演示入口，Ctrl+C 退出）
python -m uvicorn app:app --host 127.0.0.1 --port 8001   # 启动（无 Key 亦可演示核心链路）
```

### 10.3 工程约定与历史教训

- **Windows + 中文**：源码含中文话术，运行/测试设 `PYTHONIOENCODING=utf-8`；`.py` 文件避免弯引号（U+201C/201D，历史 SyntaxError 教训）；Shell 调试中文 POST 建议 Python urllib/httpx（Windows shell curl 常按 GBK 发送导致 UnicodeDecodeError）；
- **路由顺序敏感**：`/api/engineers/schedules/today`、`/api/tickets/handovers` 必须声明在动态段之前（当前已满足，新增路由注意）；
- **时间口径**：一律 `TimeConfig.naive_now()`；`parse_datetime` 返回 naive（§6.6）；
- **删库即重置**：`data/smart_appointment.db` + 索引文件删除后重启即重建全部种子（旧库备份为 `.db.bak`）；
- **会话**：按 `session_id` 隔离（每会话独立 Agent 图 + 写穿 `chat_sessions`），无登录体系、身份以手机号绑定为准；工单/订单按手机号归属（M12 起，旧"全局单进程会话"描述已废弃）。

---

*本文档锚点均已对照源码核实（M14 主管工具化 + 模型分级后版本）；技术讲解请配合 README（使用）与 PROJECT_SUMMARY（汇报）阅读。*
