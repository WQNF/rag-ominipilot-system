# tools/reranker.py
import os
import requests
from typing import List, Dict, Any

def call_siliconflow_rerank(
    query: str,
    documents: List[str],
    model: str = "Qwen/Qwen3-Reranker-0.6B",
    top_n: int = 6
) -> List[Dict[str, Any]]:
    """
    调用SiliconFlow重排序API，并返回结果列表。
    每个元素包含: index (原始文档索引), relevance_score (得分), document (文本)
    """
    api_key = os.getenv("SILICON_FLOW_API_KEY", "")
    if not api_key:
        raise ValueError("未找到 API Key，请在 .env 文件中设置 SILICON_FLOW_API_KEY 环境变量")
    
    url = "https://api.siliconflow.cn/v1/rerank"
    headers = {
        "Authorization": f"Bearer {api_key}",
        "Content-Type": "application/json"
    }
    payload = {
        "model": model,
        "query": query,
        "documents": documents,
        "top_n": top_n,
        "return_documents": True
    }
    
    print(f"\n[Reranker] 正在调用 {model} 进行重排序...")
    try:
        response = requests.post(url, json=payload, headers=headers, timeout=60)
        response.raise_for_status()
        result = response.json()
        if "results" in result:
            print(f"[Reranker] 成功，返回 {len(result['results'])} 个结果")
            return result['results']
        else:
            print("[Reranker] 响应中没有 results 字段")
            return []
    except Exception as e:
        print(f"[Reranker] 调用失败: {e}")
        return []