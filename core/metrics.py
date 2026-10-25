# core/metrics.py
from prometheus_client import Counter

llm_calls = Counter(
    "omnipilot_llm_calls_total",
    "Total number of LLM invocations",
    ["model", "agent"]
)

llm_tokens = Counter(
    "omnipilot_llm_tokens_total",
    "Total number of tokens consumed",
    ["model", "type"]  # type = input / output / total
)

def record_llm_usage(response, agent: str):
    """从 AIMessage 的响应元数据中提取 token 信息并上报 Prometheus"""
    try:
        model = response.response_metadata.get("model_name", "unknown")
        # 🔑 调用次数无条件递增
        llm_calls.labels(model=model, agent=agent).inc()

        token_usage = response.response_metadata.get("token_usage", {})
        if token_usage:
            llm_tokens.labels(model=model, type="input").inc(token_usage.get("prompt_tokens", 0))
            llm_tokens.labels(model=model, type="output").inc(token_usage.get("completion_tokens", 0))
    except Exception as e:
        print(f"[Metrics] 记录失败: {e}")