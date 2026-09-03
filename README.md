# 安居家电售后智能客服 + 上门报修预约 Agent

一个演示级「企业售后智能客服」多 Agent 系统：客户通过自然语言完成**家电报修登记 → 工程师上门预约 → 工单状态跟踪 → 保修查询 → 投诉转人工**的完整售后闭环；客服运营侧配套**报修工单管理、工程师排班、知识库管理、售后回访**等后台页面。

## 核心能力

面向客户（聊天窗口）：

- **报修登记与上门预约**：自动识别品类/故障/地址/手机号/期望上门时间，逐项追问补齐；按品类+故障技能与区域软偏好匹配工程师；指定工程师没空时推荐相似工程师并征求确认
- **保修查询**：按手机号查询名下订单，返回每台家电在保/超保状态与保修截止日
- **工单进度查询**：报修单号（AX…）随时查状态、工程师与上门时间
- **投诉转人工**：登记诉求并承诺 30 分钟内回电，落库为转人工记录
- **售后知识问答**：保修政策、收费标准、故障自查等通过知识库 RAG 检索回答
- 服务窗口统一 **9:00–18:00**，默认上门维修窗口 2 小时

面向运营（后台页面）：

- `/tickets` 报修工单管理：状态筛选、派单（校验工程师档期）、开工/完成/取消，转人工记录
- `/engineers` 工程师管理：品类技能、服务区域、今日忙闲
- `/engineer_schedules` 今日排班网格（9:00-18:00）
- `/knowledge` 知识库管理：条目增删改查与语义搜索
- `/follow_ups` 售后回访：按客户手机号生成保养/保修到期/满意度回访话术，附工程师可约时段

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
- 持有状态机（CLASSIFY/APPOINTMENT/CONSULT）与路由器，负责把消息分发给对应子 Agent
- 投诉类在**调度层**直接落库转人工记录并回执，无需进入子流程
- 子 Agent 将请求判为无关时转回调度重新分类；设有递归轮次上限防止死循环

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

## 技术栈

| 类别 | 选型 |
|---|---|
| 语言/框架 | Python 3.10+ / FastAPI + Uvicorn |
| Web 页面 | Jinja2 模板 + 原生 JS（SSE 流式渲染） |
| LLM | LangChain + OpenAI 兼容接口（可换 qwen/deepseek/zhipu/openai/azure） |
| 向量检索 | FAISS（IndexFlatIP 语义检索 / IndexFlatL2 相似匹配） |
| 存储 | SQLite + SQLAlchemy 2.0（声明式 ORM + 仓储模式） |
| 测试 | pytest（离线可跑，不依赖 API Key） |

## 项目结构

```
├── app.py                 # FastAPI 入口：路由注册 + 启动初始化
├── web/                   # Web 层：routes.py + templates + static
│   ├── routes.py          #   页面路由与 /chat/stream SSE 流式聊天
│   └── templates/         #   index / engineers / engineer_schedules / tickets / knowledge_management / follow_ups
├── api/                   # API 层：engineer / ticket / knowledge / follow_ups / task / consultation / appointment
├── agents/                # Agents 层
│   ├── task_classification_agent.py   # 任务分类 Agent（客服调度）
│   ├── task_classification/           #   classifier / state_manager / agent_router / unrelated_handler / processor
│   ├── appointment_agent.py           # 报修专员 Agent
│   ├── appointment/                   #   input_parser / engineer_finder / message_builder / processor
│   ├── consultant_agent.py            # 售后顾问 Agent
│   ├── consultant/                    #   knowledge_retriever / consultation_classifier / response_generator / prompt_builder
│   └── user_behavior_agent.py + user_behavior/   # 用户行为与回访
├── services/              # Services 层：engineer / ticket / order / handover / knowledge / text_embedding / recommendation / user_behavior
├── db/                    # DB 层：models.py / db_router.py / repositories / base（session_manager、interfaces）
├── config/                # 模型提供方、常量、时区与营业时间
├── tests/                 # 59 项离线测试
└── data/                  # SQLite 库与向量索引（运行时生成，已 gitignore）
```

## 数据模型（9 张表）

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
pytest                    # 59 项全部离线运行，不依赖 LLM/Embedding Key
pytest tests/test_offline_services.py -q   # 工单生命周期/保修边界/档期冲突等纯逻辑
```

覆盖：分类枚举与兜底、信息抽取契约、工单状态机白名单、档期冲突与释放、保修期边界、偏好置信度、回访判定（30 天）等。

## 主要页面

| 路由 | 页面 |
|---|---|
| `/` | 智能客服聊天（SSE 流式） |
| `/tickets` | 报修工单管理（派单/状态流转/转人工记录） |
| `/engineers` | 工程师管理（技能/区域/今日忙闲） |
| `/engineer_schedules` | 今日排班网格（9:00-18:00） |
| `/knowledge` | 知识库管理（增删改查 + 搜索） |
| `/follow_ups` | 售后回访（档案分析 + 话术生成） |

## 已知边界（演示范围外）

- **单会话**：聊天状态为全局单进程会话，不做登录/多用户隔离；工单与订单按手机号归属客户
- **不接支付**：超保维修费用仅话术提示，不产生真实交易
- **转人工为登记制**：回执承诺 30 分钟回电，无真实外呼
- 业务窗口 9:00-18:00 统一在 `config/time_config.py` 配置（唯一事实源）

## 后续规划

- 会话级状态持久化与多客户隔离；SSE 心跳与断线重连
- 售后顾问对工程师闲忙态的实时感知与「顺路派单」建议
- 满意度评价闭环：完成后邀请打分，评价进入行为画像
- 超保付费维修报价流程与配件库存查询
