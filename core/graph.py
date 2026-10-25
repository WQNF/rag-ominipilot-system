# core/graph.py (关键修改部分示例)
from langgraph.graph import StateGraph, START, END
from core.state import multiagentstate
from agents.planner import planner_node
from agents.dispatcher import dispatch_route    # 导入路由函数
from agents.rag import rag_agent
#from agents.data_analyst import data_analyst_agent
#from agents.supervisor import researcher_agent
from agents.summarize import summarize_agent

builder = StateGraph(multiagentstate)

# 添加节点（注意：不再需要添加名为 "dispatcher" 的节点）
builder.add_node("planner", planner_node)
builder.add_node("rag_searcher", rag_agent)
#builder.add_node("data_analyst", data_analyst_agent)
#builder.add_node("researcher", researcher_agent)
builder.add_node("summarize", summarize_agent)

# 定义流程边
builder.add_edge(START, "planner")

# 关键：planner 指向 dispatch_route 这个条件边
# LangGraph 会自动处理返回的 Send 列表，实现并行
builder.add_conditional_edges(
    "planner",            # 起点是 planner 节点
    dispatch_route,       # 路由函数
    {                     # 映射表：定义 Send 的目标节点和最终的 summarize
        "rag_searcher": "rag_searcher",
        #"data_analyst": "data_analyst",
        #"researcher": "researcher",
        "summarize": "summarize"
    }
)

# 所有专家节点执行完成后，需要汇聚到 summarize 节点
builder.add_edge("rag_searcher", "summarize")
#builder.add_edge("data_analyst", "summarize")
#builder.add_edge("researcher", "summarize")

# 总结后结束
builder.add_edge("summarize", END)

graph = builder.compile()