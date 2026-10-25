from typing import TypedDict, Annotated, List, Optional
from langgraph.graph.message import add_messages
from langchain_core.messages import BaseMessage
import operator

class multiagentstate(TypedDict):
    messages: Annotated[List[BaseMessage], add_messages]
    visited_agents: Annotated[List[str], operator.add]
    step_count: Annotated[int, lambda x, y: max(x, y)]
    next_agent: str
    task_completed: bool
    sql_query: str
    sql_result: str
    context_docs: list
    plan: List[dict]          # 变成字典列表
    sub_results: dict         # 收集子任务 id -> 回答文本，可选
    user_id: Optional[str]
    department: Optional[str]
    target_documents: Optional[list]   # 新增字段，供 rag_agent 使用
    rewritten_query: Optional[str]  # 可选，供后续使用
    final_context_docs: Optional[list]  # 新增字段，供 rag_agent 使用
    sources: Optional[list]
    retrieval_insufficient: bool        # 是否检索不足
    missing_docs: List[str]            # 缺失的文档名列表
    parsed_question: Optional[dict]    # planner 解析的问题结构 {type, stem, options}
    multi_fact: Optional[bool]          # 是否为多事实选择题
    option_claim_map: Optional[dict]    # 选项→子事实映射 {A: {claim_1, claim_2}}