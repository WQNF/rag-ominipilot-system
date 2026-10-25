import os
import time
from typing import List
from langchain_openai import OpenAIEmbeddings
from dotenv import load_dotenv

load_dotenv()

# 初始化嵌入模型（使用硅基流动 API）
# 初始化嵌入模型（使用硅基流动 API）
embedding_model = OpenAIEmbeddings(
    model=os.getenv("EMBEDDING_MODEL", "Qwen/Qwen3-Embedding-0.6B"),
    api_key=os.getenv("SILICON_FLOW_API_KEY", ""),
    base_url=os.getenv("SILICON_FLOW_API_BASE", "https://api.siliconflow.cn/v1")
)

def embed_texts(texts: List[str], batch_size: int = 50, delay: float = 0.2, max_retries: int = 3) -> List[List[float]]:
    """
    分批嵌入文本，避免 API 速率限制。
    
    Args:
        texts: 文本列表
        batch_size: 每批文本数量（根据平均 token 长度调整，建议 30-50）
        delay: 每批之间的延迟（秒）
        max_retries: 单批最大重试次数
    
    Returns:
        嵌入向量列表
    """
    all_embeddings = []
    total = len(texts)
    
    for start in range(0, total, batch_size):
        end = min(start + batch_size, total)
        batch = texts[start:end]
        
        for attempt in range(max_retries):
            try:
                batch_embeddings = embedding_model.embed_documents(batch)
                all_embeddings.extend(batch_embeddings)
                print(f"嵌入进度: {end}/{total} (批次 {start//batch_size + 1})")
                break  # 成功则跳出重试循环
            except Exception as e:
                error_msg = str(e).lower()
                if "rate_limit" in error_msg or "429" in error_msg or "tpm" in error_msg:
                    wait_time = (attempt + 1) * 2  # 指数退避: 2, 4, 6秒
                    print(f"速率限制，等待 {wait_time} 秒后重试... (错误: {e})")
                    time.sleep(wait_time)
                else:
                    # 其他错误，短暂延迟后重试
                    print(f"嵌入失败: {e}，等待 {delay} 秒后重试...")
                    time.sleep(delay)
        else:
            # 所有重试都失败，抛出异常
            raise RuntimeError(f"批次 {start//batch_size + 1} (索引 {start}-{end}) 嵌入失败，已重试 {max_retries} 次")
        
        # 每批后固定延迟，避免触发 TPM 限制
        time.sleep(delay)
    
    return all_embeddings

def embed_query(text: str) -> List[float]:
    """将查询文本转换为 embedding 向量（单条，无需分批）"""
    # 单条查询无需复杂重试，直接调用
    return embedding_model.embed_query(text)