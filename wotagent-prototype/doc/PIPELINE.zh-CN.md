# 多 Agent 流水线

## 为什么用流水线而非单 Agent

单 ReAct Agent 的工作方式是给 LLM 工具并让它循环：*思考 → 调用工具 → 观察 → 再次思考 → ...*

IoT 场景下的问题：

1. **无显式计划** — LLM 的推理不透明，无法在执行前检查"它打算做什么"
2. **无聊天快捷方式** — 即使是简单的问候也会触发完整的工具调用循环
3. **无验证** — 调用工具后，Agent 可能检查结果也可能不检查
4. **难以审计** — 没有结构化的"计划了什么 vs 发生了什么"记录
5. **无环境感知** — 没有后台监控；LLM 每次必须主动查询

流水线通过拆分职责解决这些问题：

- **意图分类器**（新增）：工具调用 LLM（temperature=0）用于结构化意图分类
- **感知引擎**："发生了什么？" → 基于规则的环境监控
- **意图分类器**："意图是什么？" → 工具调用 LLM（temperature=0）
- **执行器**："去做。" → 工具调用
- **观察器**："成功了？" → 格式化自然语言响应

## AgentPipeline

```python
@dataclass
class AgentPipeline:
    executor: Any    # create_agent() 使用 EXECUTOR_PROMPT + 所有工具
    observer: Any    # create_agent() 使用 OBSERVER_PROMPT + 无工具
    responder: Any   # create_agent() 使用 RESPONDER_PROMPT + 无工具
    planner: Any     # (保留 — 当前由意图分类器替代)
```

由 `create_wot_pipeline()` 创建：

```python
from wotagent.core import create_wot_pipeline

pipeline = create_wot_pipeline(enable_thinking=True)
# pipeline.executor, pipeline.observer, pipeline.responder (主要)
# pipeline.planner (保留)
```

### 意图分类器 (Step 0)

意图在流水线运行**之前**被分类，使用一个单独的轻量级工具调用 LLM 调用（temperature=0，禁用思考）：

```python
intent_model = _build_llm(temperature=0, enable_thinking=False)
intent_model_with_tool = intent_model.bind(
    tools=[intent_schema],  # classify_intent 函数，枚举值: chat|query|control
    tool_choice={"type": "function", "function": {"name": "classify_intent"}},
)
intent_result = await intent_model_with_tool.ainvoke([
    ("system", intent_prompt + context_injection + continuation_hint),
    ("human", message),
])
# 解析 tool_call 参数 → {"intent": "control", "rationale": "..."}
```

这比关键词分类器更准确，因为它使用与流水线其他部分相同的 LLM，但没有额外的模型初始化成本（temperature=0，不思考）。

关键设计点：
- **快速路径**：temperature=0 产生确定性、单 token 选择的输出
- **结构化输出**：工具调用 API 返回 JSON，无需解析
- **廉价**：无 `reasoning_content`，无工具调用循环 — 一次模型调用

## 规则触发执行

对于感知引擎触发的规则，分类器步骤**完全跳过**：

```python
from wotagent.core import execute_plan_directly

final_text = await execute_plan_directly(
    pipeline, plan_json, plan,
    session_id="sess_xxx",
    user_role="operator",
    memory=memory,
)
# 仅运行 Executor → Observer（无需 LLM 进行意图解析）
```

## Agent 提示词模板

每个 Agent 角色有自己的系统提示词模板：

| Agent | 模板 | 关键变量 |
|-------|------|---------|
| 执行器 | `EXECUTOR_PROMPT_V1` | `{role}`, `{tools_description}`, `{plan_json}` |
| 观察器 | `OBSERVER_PROMPT_V1` | `{plan_json}`, `{execution_results}` |
| 响应器 | `RESPONDER_PROMPT_V1` | `{role}`, `{extra_instructions}` |
| 规划器 (保留) | `PLANNER_PROMPT_V1` | `{role}`, `{perception_context}`, `{tools_description}` |

提示词注册在 `prompts/templates.py` 中，通过 `dynamic_prompt` 中间件和每个角色的构建器（`_make_prompt_builder`）加载。

## 提示词构建器

```python
_make_prompt_builder("executor")  → 从 AgentContext 读取 role/session/plan_json
_make_prompt_builder("observer")  → 读取 plan_json + execution_results
_make_prompt_builder("responder") → 基本聊天提示词
_make_prompt_builder("planner")   → 保留（意图分类器替代了规划器）
```

每个构建器在每次 Agent 步骤从中间件 `request` 对象提取 `AgentContext`，确保提示词始终是最新的。`perception_context` 字段在调用时由 `PerceptionEngine.get_context()` 填充 — LLM 无需查询即能看到当前设备状态。

## 流式输出

`invoke_agent_stream()` 生成一个包含类型化数据块的异步生成器：

```
Phase 0 (意图分类器 — 对消费者透明):
  (内部工具调用 LLM 调用，不产生事件)
  └── yield {"type": "plan", "content": {"intent": "control", "steps": [...]}}

Phase 1a (响应器，如果是 chat 意图):
  yield {"type": "thinking", "content": "..."}   # DeepSeek 推理
  yield {"type": "token", "content": "你好！"}      # 响应 token

Phase 1b (执行器，如果是 control/query 意图):
  yield {"type": "wot.agent.action.started", "data": {...}}
  yield {"type": "wot.agent.action.completed", "data": {...}}

Phase 2 (观察器，如果是 control/query 意图):
  yield {"type": "token", "content": "客厅灯已调暗到30%"}

最终:
  yield {"type": "done", "response": "...", "session_id": "..."}
```

### 感知触发的流（通过 `execute_plan_directly`）

```
EventBus: wot.perception.rule_triggered
  │
  └── execute_plan_directly()
       │
       ├── emit wot.session.started
       │
       ├── executor.astream_events()
       │   ├── action.started
       │   ├── action.completed
       │   └── action.failed
       │
       ├── observer.ainvoke()
       │
       ├── emit wot.agent.response
       ├── emit wot.session.ended
       └── return final_text
```

没有 `{"type": "plan"}` 数据块 — 计划已由规则引擎预结构化。

## 记忆集成

记忆在 `invoke_agent_stream()` 中透明处理：

1. **调用前**：将 `memory.messages` 前置到输入
2. **调用后**：调用 `memory.add_user_message()`、`memory.add_ai_message()` 和 `memory.save()`

意图分类器仅接收**最后一条用户消息**加上上下文注入（方案A + 方案B）。执行器/响应器接收完整消息历史以获取上下文。

## 会话转录

当 `session_id` 提供给 `invoke_agent_stream()` 时，自动创建 `SessionTranscript` 并在整个流水线中记录事件：

```
invoke_agent_stream(message, session_id="sess_xxx")
  │
  ├── 创建 SessionTranscript(session_id)
  ├── transcript.record_start(model=..., role=...)
  │
  ├── Phase 0 (意图分类器)
  │   ├── transcript.record_user_message(message)
  │   └── transcript.record_plan(intent, rationale, steps)
  │
  ├── Phase 1/2 (执行器/响应器)
  │   ├── transcript.record_llm_input(agent, messages)   ← on_chat_model_start
  │   └── transcript.record_tool_call(tool, input, output) ← on_tool_end
  │
  └── 流水线完成后
      ├── transcript.record_assistant_response(final_text)
      ├── transcript.record_end()
      └── transcript.save()  → {session_id}.full.jsonl + .chat.jsonl
```

每个会话两个 JSONL 文件：
- `data/memory/{session_id}.full.jsonl` — 所有事件及时间戳
- `data/memory/{session_id}.chat.jsonl` — 仅用户/助手轮次

不可序列化的对象（LangChain `ToolMessage`、`AIMessage` 等）通过 `_sanitize()` 自动转换为字符串。

## 意图连续性

`state` 参数（`ConversationState`）提供跨轮意图追踪：

```python
state = ConversationState()
async for chunk in invoke_agent_stream(..., state=state):
    ...
# state.intent → "control" (从上一轮保留)
```

意图分类器内部的两种注入机制：

**方案A — 上下文注入**（`get_context_str()`）：
将最多 2 条最近的用户/助手消息对注入分类器提示词，使 "我在客厅呢" 这样的后续消息能在上下文中被理解。

**方案B — 延续提示**（`get_continuation_hint()`）：
如果上一个意图是非 chat 且 Agent 问了问题，注入：
```
⚠️ 提示：上一步的操作意图是「control」，助手刚问了用户一个问题，
现在用户在回答。如果用户在回答助手的问题，意图应延续上一步的
「control」，而不是chat。
```

**状态生命周期：**
- 分类后：`state.intent = intent if intent != "chat" else state.intent`
- 轮次完成后：`state.record_turn(user_msg, agent_msg)` 追踪：
  - Agent 是否问了问题（用于下一轮的方案B）
  - 最近 5 轮对话（用于方案A 上下文）
  - 当 Agent 回到空聊天时重置 `intent` 为 `None`

## 从流水线到 StateGraph

当前架构使用 Python 级别的编排（三次顺序 `astream_events` 调用）。这可以清晰地映射到 LangGraph `StateGraph`：

```python
from langgraph.graph import StateGraph
from typing import TypedDict, Annotated

class PipelineState(TypedDict):
    messages: list
    context: AgentContext
    plan: str
    execution_results: str
    final_response: str

builder = StateGraph(PipelineState)

# 节点 — 每个包裹一个 create_agent() 调用
builder.add_node("planner", planner_node)
builder.add_node("executor", executor_node)
builder.add_node("observer", observer_node)
builder.add_node("responder", responder_node)

# 条件路由
builder.add_conditional_edges(
    "planner",
    lambda s: "respond" if json.loads(s["plan"]).get("intent") == "chat" else "execute",
)
builder.add_edge("executor", "observer")
```

切换到 StateGraph 将带来：
- **内置状态管理**，而非手动上下文传递
- **检查点**，用于暂停/恢复长时间运行的计划
- **并行执行**，用于独立的计划步骤
- **可视化追踪**，通过 LangGraph Studio

当前实现的设计目标是使此迁移成为机械性的 — 无需逻辑更改，仅结构变动。
