# 安居家电售后智能客服系统设计规格说明书（SPEC）

| 项目 | 内容 |
| --- | --- |
| 文档版本 | v1.1（家电售后版） |
| 编写日期 | 2026-09-03 |
| 适用版本 | 当前 master 分支 |
| 系统名称 | 安居家电售后智能客服 + 上门报修预约 Agent |
| 英文代号 | Home Appliance After-Sales Service Agent（工程内部以 engineer 命名工程师域） |

---

## 1. 文档目的

本文档描述系统的模块设计、核心流程、数据模型与工程约定，供二次开发与讲解使用。README 面向使用者，PROJECT_SUMMARY 面向汇报，本文档面向**技术细节**。

## 2. 项目概述

### 2.1 项目目标

构建家电品牌的**售后智能客服 + 上门维修调度**演示系统：客户聊天即可完成报修登记、工程师上门预约、保修查询、工单进度追踪与投诉转人工；运营侧提供工单管理、排班、知识库与回访页面。核心工程目标：在"多 Agent 编排 + RAG + 状态机"架构之上，把**高确定性业务（保修计算、派单冲突、状态流转）全部收敛为可离线测试的纯逻辑**。

### 2.2 核心能力清单

1. 报修登记与上门预约（结构化抽取、逐项追问、工程师匹配、建单派单、保修提示）
2. 订单保修查询（按手机号，纯查库模板答复）
3. 报修工单进度查询（按单号 AX…，纯查库模板答复）
4. 投诉/转人工登记（独立落库 + 回电承诺回执）
5. 售后知识 RAG 问答（政策/收费/故障自查，FAISS 检索）
6. 后台运营：工单管理、工程师与排班、知识库管理、售后回访

### 2.3 技术栈

| 类别 | 选型 |
| --- | --- |
| 语言 / Web 框架 | Python 3.10+ / FastAPI + Uvicorn |
| 模板 | Jinja2 + 原生 JS（EventSource SSE 流式渲染） |
| LLM 层 | LangChain ChatOpenAI 兼容工厂，`config/model_provider.py` 统一出链 |
| 向量检索 | FAISS：`IndexFlatIP`（知识语义检索）、`IndexFlatL2`（工程师技能近似） |
| ORM | SQLAlchemy 2.0 声明式 + Repository 仓储 + 手工 DatabaseRouter（每 Service 独立 SessionFactory 注入 db_path） |
| 数据库 | SQLite 单文件 `data/smart_appointment.db` |
| 测试 | pytest + pytest-asyncio（59 项离线用例，零外部依赖） |

---

## 3. 系统总体架构

### 3.1 分层架构

```
Web 层    web/：routes.py（页面 + SSE 聊天端点）、templates/、static/
API 层    api/：engineer / ticket / knowledge / follow_ups / task / consultation / appointment
Agents 层 agents/：task_classification_agent / appointment_agent / consultant_agent / user_behavior_agent
Services 层 services/：engineer / ticket / order / handover / knowledge / text_embedding / recommendation / user_behavior
DB 层    db/：models.py、db_router.py、base/（session_manager、interfaces）、repositories/
```

依赖方向：`Web → API → Agents → Services → DB`；禁止反向与跳级调用。Agents 层是唯一允许调用 LLM 的层（模型访问封装在各自的 *Classifier / Parser / Generator 组件中）。

### 3.2 调用规则

- 页面数据直取：`Web → API → Service → Repository`（如工单管理页）；
- 对话编排：`Web(/chat/stream) → Agent 控制器 → 组件(LLM 或 Service) → Repository`；
- Services 在模块内延迟 import（函数级），规避 Agent↔Service 循环依赖；
- Service 构造签名统一 `(db_path='sqlite:///data/smart_appointment.db')`，测试以临时库注入。

### 3.3 整体架构流程图

```
                      /chat/stream (SSE)
                             │
              [TaskClassificationAgent] 任务分类（LLM）
                分类枚举：appointment / query / complaint / other
                             │
        ┌────────────┬───────┴──────────┬──────────────┐
        ▼            ▼                  ▼              ▼
   [报修专员]   [售后顾问]          [投诉登记]      [其他兜底]
   APPOINTMENT   CONSULT         调度层直落库      抱歉话术
        │            │
   InputParser   查询短路(纯函数查库)
   JSON 契约      │  │  命中→模板答复
        │         RAG(FAISS→LLM)
   EngineerFinder                     [用户行为 Agent]
   (档期/技能/区域)                   行为记录→偏好置信度
        │
   TicketService 建单 AX… + 派单写忙档 + 保修提示
        │
   后台：/tickets 流转 · /engineers 排班 · /follow_ups 回访 · /knowledge 知识库
```

### 3.4 模块依赖要点

- `agents/task_classification/classification_processor.py` 持有**子任务转回再分类的轮次上限**（>3 强制重置），防客服调度 ↔ 子 Agent 递归；
- 售后顾问在 RAG 前做**查询意图短路**（consult_stream 顶部 `try_lookup`），防"查单进度"在二次分类中被误判转回；
- 报修专员 `appointment_history` 为会话级内存状态（模块单例），字段只增不覆盖，补齐信息不丢旧值。

---

## 4. 模块详细设计

### 4.1 Web 层（`web/`、`app.py`）

`web/routes.py` 提供页面路由与两个聊天端点：

| 端点 | 说明 |
| --- | --- |
| `GET /` | 聊天主页 |
| `POST /chat/stream` | **主聊天端点**：SSE 流式，逐 token 输出 |
| `POST /chat` | 兼容端点（同步一次性返回） |
| `GET /knowledge` `/engineers` `/engineer_schedules` `/follow_ups` `/tickets` | 后台页面 |

聊天端点内部：校验请求 → 定位全局 TaskAgent 单例 → `process_task_stream` → 以 `text/event-stream` 逐块输出。聊天状态为**进程级全局单会话**（模块单例持有状态机与各 Agent 历史）。

`app.py` 的 `initialize_system()` 启动时依次执行，**每个模块独立 try/except**（无 Embedding Key 等外部依赖缺失只告警不阻断）：

1. 知识库：从 DB 加载 12 条默认知识，缺向量则尝试生成并构建 FAISS 索引；失败跳过（记录日志）
2. 工程师：空表则播种 8 名默认工程师
3. 演示订单：空表则播种 5 条订单（王芳 13800138000 ×3：空调在保/冰箱在保/洗衣机超保；李强 13900000000 ×2）
4. 回访提醒调度器（后台定时任务，写 user_recommendations）

### 4.2 API 层（`api/`）

| Router | Prefix | 端点 |
| --- | --- | --- |
| task | `/api/task` | `POST /classify` |
| consultation | `/api/consultation` | `POST /ask` |
| appointment | `/api/appointment` | `POST /create` |
| knowledge | `/api/knowledge` | `GET/` 列表、`POST/` 新增、`POST /search`、`GET/PUT/DELETE /{id}` |
| engineer | `/api/engineers` | `GET/` 全部、`GET /schedules/today`（先于动态段声明）、`GET /{id}`、`GET /{id}/schedule` |
| ticket | `/api/tickets` | `GET ""` 列表(按 status/phone 过滤)、`POST ""` 创建、`GET /handovers`（先于 `/{ticket_id}`）、`GET /{id}`、`POST /{id}/assign`、`POST /{id}/status` |
| follow_ups | `/api/follow_ups` | `GET /stats`、`POST /analysis`、`POST /reminder` |

契约要点：工单创建 `TicketCreate{user_phone, product_type, fault_desc, address, start_time, end_time?, engineer_id?}`；创建时带 engineer_id 则立即尝试派单，冲突保留 pending 并返回 400 说明；状态流转走服务层白名单，非法流转 400；派单返回 `OperationResult{message, data}`。

### 4.3 Agents 层（`agents/`）—— 系统核心

#### 4.3.1 TaskClassificationAgent（客服调度）

- `TaskClassifier`：LLM 分类，枚举 `appointment/query/complaint/other`，prompt 内置每类两个家电例句；输出归一化（大小写/空白）；LLM 异常 → `other` 兜底；
- `StateManager`：三态 `CLASSIFY/APPOINTMENT/CONSULT`，`should_classify()` 决定本轮走分类还是续接；
- `AgentRouter`：`route_to_appointment` / `route_to_consultation` / `route_to_complaint` / `route_by_state`；`route_to_complaint` 单轮完成：`HandoverService.create` 落库 + 回执话术（含回电号码）+ 状态重置 CLASSIFY；**不新增状态枚举**；
- `ClassificationProcessor.process_task_stream`：分类轮次计数 `_classify_rounds`（>3 重置为 CLASSIFY 并走 other 兜底），路由正常结束清零。

#### 4.3.2 AppointmentAgent（报修专员）

- `InputParser`：prompt 模板 + LLM 流式输出**纯 JSON** 契约（见下），`parse_data` JSONDecodeError → 全"未知" + `missing_info=["所有信息"]` 降级字典；
  - 契约字段：`product_type / fault_desc / address / phone / start_time / engineer_name / confirmation / info_complete / unrelated / missing_info`
  - 必需字段（info_complete=true 需五项）：品类、故障、地址、11 位手机号、上门时间（YYYY-MM-DD HH:MM）；engineer_name 可选
- `AppointmentProcessor`：
  - `update_history_from_data`：等待确认态先走 `_handle_recommendation_response`（positive `是/好/可以/行/确定…`，negative `不/不要/不行/别…`）；普通态只更新"有值且校验通过"的字段；
  - `_is_valid_start_time`：可解析 + **9:00–18:00 窗口**（`TimeConfig.get_business_hours()` 为唯一事实源）+ 不早于当前时间；
  - `handle_complete_appointment`：declined → 拒绝文案；confirmed → 按推荐工程师建单；awaiting → 提示明确回复"是/不"；否则 `find_engineer_with_thought` 匹配；推荐态发 `[SIGNAL]recommendation_pending`；
  - `_process_successful_repair`：`TicketService.create_ticket`（pending）→ `assign_ticket`（写忙档 + assigned）→ 行为记录 → `_build_warranty_note` 按手机号+品类查订单拼在保提示 → `create_appointment_success_message` 确定性成功文案（无 LLM）；
- `EngineerFinder`：见 3.3 匹配策略；`parse_repair_time` 默认维修时长 120 分钟；`extract_region` 由地址关键词（海淀/朝阳/…）做区域软偏好；`_ranked_engineers` 用 `services/text_embedding.find_best_match_indices`（IndexFlatL2，失败保持原顺序）；
- `MessageBuilder`：全部确定性文案（追问表、成功/失败/换人/拒绝/无关/解析失败），追问时给出 9:00-18:00 槽位示例。

#### 4.3.3 ConsultantAgent（售后顾问）

- `ConsultationProcessor`：
  - `_try_lookup_answer`（纯函数短路，先于一切 LLM）：`AX\d{10,14}` 单号（容忍纯数字 11-14 位 + 工单/进度/单号意图词）→ `_build_ticket_status_answer`（状态标签表 pending/assigned/in_progress/completed/cancelled + 工程师 + 上门时间 + 关闭时间）；11 位手机号 + 保修/在保/过保/质保/订单等关键词 → `_build_warranty_answer`（订单逐条在保/超保 + 截止日 + 剩余天数）；
  - 未命中 → KnowledgeRetriever FAISS 检索 top-3 → ResponseGenerator LLM 流式生成；
  - `ConsultantAgent.consult_stream` 顶部 `try_lookup` 短路后直接返回并重置状态——**这是防顾问二次分类误判转回的根**；
- `handle_unrelated_request`：置状态回 CLASSIFY 并交回调（unrelated_callback → 主调度重分类）。

#### 4.3.4 UserBehaviorAgent（用户行为/回访）

- 报修成功时以**手机号**为 user_id 记录行为 `{product_type, fault_desc, address, start_time, end_time, engineer_id, ticket_no}`；
- 偏好类型枚举：`engineer_id / time_period / product_type / fault_type`，置信度累加；pattern_analyzer 统计常用品类/故障/时段/工程师；
- 回访判定：`should_send_reminder`（累计报修≥阈值 或 距上次报修 ≥ 30 天，天数用**北京时间**计算）；
- `_query_engineer_available_times`：查常用工程师今天/明天窗口（9:00-18:00）空闲整点槽（今天从下一整点起），最多 3 个，供 LLM 生成回访话术（mock/降级时返回固定话术）。

### 4.4 Services 层（`services/`）

| 服务 | 职责要点 |
| --- | --- |
| engineer_service | 8 名默认工程师种子；`is_engineer_available`（busy 时段重叠判定）、`get_engineer_by_name` 等 |
| ticket_service | 建单（单号 `AX+%Y%m%d+两当日序`）；**状态机白名单** `ALLOWED_TRANSITIONS`；派单（校验档期→写 busy 排班绑定 ticket_id→assigned）；`change_engineer`（释放重派）；完成/取消 → 记 closed_at + **释放忙档** |
| order_service | 演示订单种子；`get_warranty_info(phone)`：保修结束 = purchase_date + 365×warranty_years，与北京时间 `naive_now` 比较得 in_warranty/days_left |
| handover_service | 转人工记录 create/list（可带 ticket 关联） |
| knowledge_service | 默认 12 条家电售后知识（保修政策/收费/分品类排查/退换货/9-18 服务时间/延保/安装/发票/保养/安全）；FAISS IndexFlatIP 检索 + 软删除后重建索引 |
| recommendation_service | 调度任务骨架：保修 30 天内到期订单提醒、完成维修满意度回访（写 user_recommendations） |
| text_embedding | `get_embedding`（缓存 DB+文件）；`find_best_match_indices(query, candidates)` IndexFlatL2 返回排序下标 |

### 4.5 DB 层（`db/`）

- `session_manager.SessionManager`：构造即建表（`create_all`）+ 基础会话工厂；**删库文件 = 零迁移重置**；
- `DatabaseRouter`：按 Services 注入同一 db_path，暴露 `engineers / tickets / orders / handovers / knowledge / user_behavior` 等仓储；
- 仓储接口集中在 `base/interfaces.py`（BaseEngineerRepository / BaseTicketRepository / BaseOrderRepository / BaseHandoverRepository / …），测试可注入替身；
- Repository 层负责 SQLAlchemy 查询拼装、软删除过滤、`confidence_score` 自增等。

### 4.6 Config 层（`config/`）

| 模块 | 职责 |
| --- | --- |
| settings.py | 读取环境变量（provider/api_key/base_url/model） |
| model_provider.py | `create_chat_model(temperature)` / `create_embedding_model()` 工厂；env 前缀 `LLM_*` / `EMBEDDING_*` |
| constants.py | `StateEnum`：CLASSIFY / APPOINTMENT / CONSULT |
| time_config.py | **唯一时间事实源**：`naive_now()`（北京时间 naive）、`get_business_hours()`（9,18）、`parse_datetime`（**返回 naive**，与库内存储一致）、`format_datetime` |

### 4.7 外部服务与扩展

- 已删除原天气工具与 `create_openai_tools_agent`：订单/工单/保修查询均为纯函数，无 LLM 工具调用残留；
- 模型侧仅 OpenAI 兼容 HTTP 依赖；可扩展点为调度器任务（recommendation_service）与转人工外呼对接。

---

## 5. 核心流程设计

### 5.1 系统启动初始化流程

见 4.1。关键：`initialize_system` 各步骤独立容错 + 幂等（库中已有工程师/订单/知识则跳过播种）。

### 5.2 报修预约流程（详细时序）

```
消息进入 APPOINTMENT 态：
1. InputParser：LLM 抽取 JSON 契约（该 LLM 调用结果不向用户流式输出）
2. parse_data 容错降级
3. update_history_from_data：
   a. awaiting_confirmation → 解析 confirmation（是→confirmed_engineer 置位；不→declined 置位）
   b. 普通：逐字段校验写入（phone 正则 ^1\d{10}$；时间需在 9-18 且未过期）
   c. 返回 has_all_required（五项必需）
4. unrelated=true 且非等待确认 → 转 CLASSIFY 交主调度（历史不清空）
5. finished=true → handle_complete_appointment：
   - declined → 拒绝文案（移除推荐临时字段）
   - confirmed → _process_successful_repair(推荐工程师)
   - awaiting_confirmation → 提示明确回复
   - 否则 EngineerFinder：
       指定工程师：查档期 → 有空直接返回；没空 → 技能相似 + 区域偏好找替代
                    → requires_confirmation 包装 + [SIGNAL]recommendation_pending
       未指定：区域软偏好 → 技能排序 → 找第一个空闲
   - 成功路径：create_ticket → assign_ticket → 行为记录 → 保修提示 → 确定性成功文案
   - 无候选工程师 → 失败文案
   成功且无待确认 → 重置会话（history 清空、状态回 CLASSIFY）
6. finished=false → handle_incomplete_info：按 REQUIRED_FIELDS 逐项重建缺失清单 → 追问文案
```

### 5.3 咨询流程（RAG 数据流）

```
1. consult_stream：try_lookup(user_input) 命中 → 模板答复（零 LLM）→ 状态复位
2. ConsultationClassifier.is_consultation_related：否 → 无关回调转回主调度
3. KnowledgeRetriever.search_knowledge(user_input, top_k=3)：
   向量化 → IndexFlatIP 检索 → 过滤软删除 → 附加元数据
   （无 Embedding Key：文档无向量 → 跳过检索返回空）
4. ResponseGenerator.generate_response_stream：知识上下文 + LLM 流式生成
5. 行为记录（异步、失败仅告警）
6. 咨询结束 → 状态复位 CLASSIFY
```

### 5.4 对话令牌协议（前端解析约定）

`/chat/stream` 纯文本流，前端按行解析：

| 令牌 | 语义 | 前端处理 |
| --- | --- | --- |
| `[THOUGHT][角色] …` | 过程思考（客服调度/报修专员/售后顾问） | 灰色小字 |
| `[REPLY][角色]\n…` | 回复正文 | 气泡（成功绿/追问蓝/错误红按关键词） |
| `[SIGNAL]recommendation_pending` | 换人推荐待确认 | 静默（不渲染） |
| `[ERROR]…` | 异常 | 红色告警 |

前端关键词渲染：含 `报修单 / 已为您安排 / 上门` → 成功绿；含 `抱歉 / 失败 / 暂无` → 错误样式；追问（无上述关键词的 REPLY）→ 蓝色强调；消息内 `\n` 保留换行。

---

## 6. 异常处理与兜底机制汇总

| 环节 | 兜底 |
| --- | --- |
| 任务分类 LLM 异常/超时 | 归类 `other`，输出能力清单话术 |
| 分类→子任务转回超过 3 轮 | 强制重置状态 + `other` 兜底话术 |
| 信息抽取 JSON 解析失败 | 全"未知"降级字典（missing_info=所有信息），流程转追问不崩 |
| 售后顾问二次分类把查询误判为无关 | consult_stream 顶部查询短路优先于分类（先查库） |
| 时间非法（过期/非营业窗口） | 拒绝写入并追问，提示 9:00-18:00 |
| 指定工程师无档期 | 相似工程师推荐（requires_confirmation 流程） |
| 无候选工程师 | 确定性失败文案（提示改时间/热线） |
| Embedding 服务不可用 | 跳过向量索引构建；检索返回空 → LLM 兜底话术 |
| 派单冲突（后台操作） | 400 + 说明冲突、工单保留 pending 可改派 |
| 非法工单状态流转 | 服务层白名单拒绝 |
| 行为记录失败 | 仅日志告警，不影响主流程 |

---

## 7. 数据模型（9 张表，`db/models.py`）

```
engineers (id, name unique, skills Text, service_region)
  │1─N
  ▼
engineer_schedules (id, engineer_id FK, start_time, end_time, status[busy/free], ticket_id FK nullable)

repair_tickets (id, ticket_no unique "AX+YYYYMMDD+NN", user_name, user_phone,
                product_type, fault_desc, address, start_time, end_time,
                status: pending→assigned→in_progress→completed|cancelled,
                engineer_id FK nullable, created_at, updated_at, closed_at)

orders (id, user_phone, user_name, product_type, brand_model,
        purchase_date Date, warranty_years)        -- 保修期动态计算

human_handovers (id, user_name, user_phone, ticket_id FK nullable, issue_summary, created_at)

knowledge_documents (id, content, category, keywords, embedding BLOB, deleted)

user_behaviors (id, user_id=手机号, action_type[repair/consultation], action_data JSON, engineer_id, session_id, created_at)
user_preferences (id, user_id, preference_type[engineer_id/time_period/product_type/fault_type], preference_value, confidence_score, last_updated)
user_recommendations (id, user_id, type, content, status, created_at)
```

状态机：

```
pending ──assigned──> assigned ──in_progress──> in_progress ──completed──> completed
   │                    │                           │
   └────cancelled───────┴─────────cancelled──────────┘   （前三态可取消；完成/取消终态）
完成/取消：写 closed_at 并 release_schedule_by_ticket（工程师忙档即释放）
```

## 8. 已知注意事项（基于源码观察）

- **时间口径**：全系统使用北京时间 naive datetime（`TimeConfig.naive_now()`）；`parse_datetime` 必须返回 naive，否则与库内比较抛 TypeError（历史 bug，勿改回 aware）；
- **路由顺序敏感**：`/api/engineers/schedules/today`、`/api/tickets/handovers` 必须声明在动态段 `/{id}` 之前；
- **对话为全局单会话**：AppointmentAgent/ConsultantAgent 状态与历史随 TaskAgent 单例驻留进程，重启即重置；演示依赖此行为；
- **软删除**：知识库条目删除为标记 deleted，检索与索引重建均过滤；
- **Windows + 中文**：源码含中文文案，运行与测试请设 `PYTHONIOENCODING=utf-8`；避免在代码中使用弯引号（U+201C/201D），历史 SyntaxError 教训；
- **curl 直发中文**：Windows shell 常以 GBK 编码 POST 体导致 UnicodeDecodeError，调试中文接口建议用 Python urllib/httpx 客户端或 `--data-binary` + UTF-8 文件。

## 9. 测试设计（`tests/`，59 项全离线）

| 文件 | 覆盖 |
| --- | --- |
| test_task_classification_agent.py | 枚举完整性、prompt 家电化断言、分类归一化、非法类别（pay/statistics）→ other 兜底 |
| test_appointment_agent.py | 信息抽取契约解析、历史合并行为 |
| test_consultant_agent.py | 工单号/保修查询模板答复（含纯数字单号容错） |
| test_offline_services.py | 工单全生命周期合法/非法流转、档期冲突与换派、完成/取消释放档期、保修边界（364/366 天）、演示订单幂等播种、工程师种子覆盖、转人工记录 |
| test_user_behavior_agent.py | 行为记录与偏好置信度、常用偏好识别、30 天回访判定（UTC/本地修正后）、话术无旧版残留 |

`conftest.py` 提供 FakeChatModel（同步可 `prompt|llm` 组合）与 `temp_db_path`/`tmp_engine` 夹具（sqlite 内存临时库），保证零 Key 可跑。

## 10. 扩展方向

1. 会话持久化与多客户隔离（user_id 从手机号提升为会话维度）
2. 超保付费报价与支付流程接入；配件库存查询
3. 真实外呼/短信对接（转人工与预约提醒位已留）
4. 满意度评价闭环写入行为画像
5. Docker 化与云数据库、日志监控标准化
