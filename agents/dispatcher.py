# agents/dispatcher.py
from langgraph.types import Send

def dispatch_route(state: dict):
    plan = state.get("plan", [])
    if not plan:
        return ["summarize"]
    # 简单起见，仍然将所有专家并行发出（即便有依赖，后续 Summarize 会按依赖整合）
    experts = list(set(task.get("expert", "rag_searcher") for task in plan))   # 去重，缺 expert 默认 rag_searcher
    sends = []
    for expert in experts:
        sends.append(Send(expert, state))
    return sends