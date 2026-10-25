import re
from langchain_core.messages import HumanMessage, AIMessage
from core.state import multiagentstate
from core.llm import llm
from tools.retriever import get_retriever
from tools.embedder import embed_query
from tools.reranker import call_siliconflow_rerank
from tools.bm25_retriever import get_bm25_retriever
from tools.user_knowledge import search_user_knowledge
from config.prompts import RAG_PROMPT
from core.metrics import record_llm_usage
from langsmith import traceable

def reciprocal_rank_fusion(results_list, k=60):
    fusion_scores = {}
    for results in results_list:
        for rank, (doc_id, _) in enumerate(results):
            fusion_scores[doc_id] = fusion_scores.get(doc_id, 0) + 1 / (k + rank + 1)
    sorted_docs = sorted(fusion_scores.items(), key=lambda x: x[1], reverse=True)
    return [doc_id for doc_id, _ in sorted_docs]

def extract_metadata_filters(query: str) -> str:
    filters = []
    # 文件名
    file_match = re.search(r'《([^》]+)》', query)
    if file_match:
        file_name = file_match.group(1)
        filters.append(f'file_name like "%{file_name}%"')
    # 年份
    year_match = re.search(r'(\d{4})年', query)
    if year_match:
        year = year_match.group(1)
        filters.append(f'publish_date >= "{year}-01-01" and publish_date <= "{year}-12-31"')
    # 发文机关
    authority_match = re.search(r'(中国人民银行|国家金融监督管理总局)', query)
    if authority_match:
        authority = authority_match.group(1)
        filters.append(f'authority == "{authority}"')
    # 指标名称
    indicator_match = re.search(r'(普惠型小微企业贷款余额|核心一级资本充足率|不良贷款率|资本充足率)', query)
    if indicator_match:
        indicator = indicator_match.group(1)
        filters.append(f'indicator == "{indicator}"')
    # 时间维度
    period_match = re.search(r'(\d{4})年\s*[第]?(\d+)季度', query)
    if period_match:
        year, quarter = period_match.groups()
        period_str = f"{year}Q{quarter}"
        filters.append(f'time_dimension == "{period_str}"')
    # 条款号
    clause_match = re.search(r'第([一二三四五六七八九十百千万0-9]+)条', query)
    if clause_match:
        clause_num = clause_match.group(0)
        filters.append(f'section == "{clause_num}"')
    
    if filters:
        return " and ".join(filters)
    return None

def build_filter_from_target_docs(target_docs: list) -> str:
    """
    根据 planner 识别的目标文档名列表构建 Milvus 过滤表达式
    例如: file_name like "%doc1%" or file_name like "%doc2%"
    """
    if not target_docs:
        return None
    conditions = []
    for doc in target_docs:
        conditions.append(f'file_name like "%{doc}%"')
    return " or ".join(conditions)

def rag_agent(state: multiagentstate) -> dict:
    print("\n" + "="*60)
    print("  [RAG Agent] 步骤 3/4 — 知识库检索")
    print("="*60)
    
    user_msg = state.get("original_query", "")
    if not user_msg and state.get("messages"):
        last_message = state["messages"][-1]
        if isinstance(last_message, HumanMessage):
            user_msg = last_message.content
        else:
            user_msg = str(last_message)
    if not user_msg:
        user_msg = "请提供帮助"

    # 归一化检索查询：用 parsed_question.stem（纯题干），消除选项拼接格式对 embedding/BM25 的影响
    parsed_question = state.get("parsed_question", {})
    search_query = parsed_question.get("stem", "") or user_msg
    
    target_docs = list(state.get("target_documents", []) or [])
    explicit_refs = re.findall(r'@\[([^\]]+)\]', user_msg)
    for ref in explicit_refs:
        if ref not in target_docs:
            target_docs.append(ref)
    user_id = state.get("user_id")
    
    # ========== 有 target_docs 时：主力检索 target 文档 + 全局辅助检索 ==========
    if target_docs:
        print(f"[RAG] Target 文档 ({len(target_docs)} 个):")
        for i, d in enumerate(target_docs):
            print(f"  └─ [{i+1}] {d}")
        
        retriever = get_retriever()
        bm25_retriever = get_bm25_retriever()
        query_embedding = embed_query(search_query)
        
        # 从 Planner 获取子任务描述，用于多角度 BM25 检索补全
        plan_tasks = state.get("plan", [])
        task_queries = []
        for t in plan_tasks:
            if isinstance(t, dict) and t.get("description"):
                desc = t["description"]
                if desc not in task_queries:
                    task_queries.append(desc)
        if task_queries:
            print(f"[RAG] 从 Planner 获取 {len(task_queries)} 个子任务关键词，扩展 BM25 检索")
         # 多事实模式：放宽重排序结果的返回数量
        multi_fact = state.get("multi_fact", False)
        # 从配置中获取top_k设置，默认20/24
        from core.llm import get_llm_config
        config = get_llm_config()
        default_top_n = 24 if multi_fact else 20
        rerank_top_n = config.get("retrieval_top_k", default_top_n)
        print(f"[RAG] 当前配置：retrieval_top_k={config.get('retrieval_top_k')}, retrieval_nprobe={config.get('retrieval_nprobe')}, retrieval_threshold={config.get('retrieval_threshold')}")
        if multi_fact:
            print(f"[RAG] 多事实模式：重排序 top_n = {rerank_top_n}")

            
        # ======== 主力：逐文档 BM25 + 向量多路召回（带 file_name 过滤） ========
        target_candidates = []
        for doc_name in target_docs:
            expr = f'file_name like "%{doc_name}%"'
            
            # 向量检索：Excel 用 nprobe=128 保证召回，Word/PDF 用 nprobe=10 精炼防噪声
            is_excel = doc_name.lower().endswith(('.xlsx', '.xls'))
            vec_nprobe = 128 if is_excel else 64
            # 从配置中获取top_k设置，默认80
            from core.llm import get_llm_config
            config = get_llm_config()
            vec_top_k = config.get("retrieval_top_k", 80)
            vec_nprobe_config = config.get("retrieval_nprobe", vec_nprobe)
            print(f"[RAG] 向量检索配置：top_k={vec_top_k}, nprobe={vec_nprobe_config}")
            vec_docs = retriever.retrieve(query_embedding, top_k=vec_top_k, score_threshold=-1.0, expr=expr, nprobe=vec_nprobe_config)
            
            # BM25 检索（过滤到该文档）- 原始问题 + 子任务关键词做额外检索
            from core.llm import get_llm_config
            config = get_llm_config()
            bm25_top_k = config.get("retrieval_top_k", 80)
            print(f"[RAG] BM25检索配置：top_k={bm25_top_k}")
            bm25_raw = bm25_retriever.search(query=search_query, top_k=bm25_top_k, filter_expr=expr)
            for task_q in task_queries:
                extra = bm25_retriever.search(query=task_q, top_k=bm25_top_k//2, filter_expr=expr)
                bm25_raw.extend(extra)

            user_docs = search_user_knowledge(
                user_id,
                query_embedding,
                top_k=max(10, vec_top_k // 2),
                file_names=[doc_name],
            )
            
            # 构建 doc_map（doc_id 天然去重：同 id 只保留第一个）
            doc_map = {}
            for idx, doc in enumerate(vec_docs):
                doc_map[f"target_vec_{doc_name}_{idx}"] = doc.page_content
            for doc_id, content, _ in bm25_raw:
                if doc_id not in doc_map:
                    doc_map[doc_id] = content
            for idx, (doc, _) in enumerate(user_docs):
                doc_map[f"user_vec_{doc_name}_{idx}"] = doc.page_content
            
            # 直接合并向量和 BM25 结果，不融合，统一交由 reranker 排序
            all_cands = list(doc_map.values())
            target_candidates.extend(all_cands)
            print(f"  [RAG] 文档「{doc_name[:30]}...」→ 召回 {len(vec_docs)} 全局向量 + {len(bm25_raw)} BM25 + {len(user_docs)} 个人向量 → 合并 {len(all_cands)} 个候选")
        
        # ======== 所有 target 文档合并去重 + 重排序取 12 个（主力检索）========
        dedup_seen = set()
        unique_target = []
        for c in target_candidates:
            key = c[:200]
            if key not in dedup_seen:
                dedup_seen.add(key)
                unique_target.append(c)
        # ↓ 插入这行
        print(f"  [RAG] 去重后保留 {len(unique_target)} 个唯一片段（合并 {len(target_candidates)} 个候选，去重 {len(target_candidates) - len(unique_target)} 个重复）")
        
        if len(unique_target) > rerank_top_n:
            # reranker 用完整问题（含选项），比纯题干更有区分度
            rerank_query = user_msg if user_msg and len(user_msg) > len(search_query) else search_query
            reranked = call_siliconflow_rerank(query=rerank_query, documents=unique_target, top_n=rerank_top_n)
            if reranked:
                indices = [item['index'] for item in reranked]
                target_final = [unique_target[idx] for idx in indices if idx < len(unique_target)]
                score_map = {unique_target[item['index']]: item.get('relevance_score', 0.0) for item in reranked if item['index'] < len(unique_target)}
            else:
                target_final = unique_target[:rerank_top_n]
                score_map = {}        
        else:
            target_final = unique_target
            score_map = {}
        
            print(f"  [RAG] 重排序 {len(unique_target)} 个候选 → 取 top {len(target_final)}")
        
        # ======== 如果不足 rerank_top_n 个，全局补充 ========
        if len(target_final) < rerank_top_n:
            supplement_needed = rerank_top_n - len(target_final)
        
        # ======== 如果不足 20 个，全局补充（仅填充，不独立 rerank）========
        if len(target_final) < rerank_top_n:
            supplement_needed = rerank_top_n - len(target_final)
            print(f"[RAG] ② 全局检索（补充 {supplement_needed} 个）...")
            # 向量
            # 从配置中获取top_k设置，默认80
            from core.llm import get_llm_config
            config = get_llm_config()
            global_top_k = config.get("retrieval_top_k", 80)
            global_vec = retriever.retrieve(query_embedding, top_k=global_top_k, score_threshold=-1.0)
            global_vec_results = [(f"global_vec_{idx}", doc.metadata.get('score', 0.0)) for idx, doc in enumerate(global_vec)]
            user_vec = search_user_knowledge(user_id, query_embedding, top_k=global_top_k)
            user_vec_results = [(f"user_global_vec_{idx}", score) for idx, (_, score) in enumerate(user_vec)]
            # BM25
            global_bm25_raw = bm25_retriever.search(query=search_query, top_k=global_top_k)
            global_bm25_results = [(doc_id, score) for doc_id, _, score in global_bm25_raw]
            # doc_map
            g_doc_map = {}
            for idx, doc in enumerate(global_vec):
                g_doc_map[f"global_vec_{idx}"] = doc.page_content
            for idx, (doc, _) in enumerate(user_vec):
                g_doc_map[f"user_global_vec_{idx}"] = doc.page_content
            for doc_id, content, _ in global_bm25_raw:
                if doc_id not in g_doc_map:
                    g_doc_map[doc_id] = content
            # 融合
            global_fused = reciprocal_rank_fusion([global_vec_results, global_bm25_results, user_vec_results], k=60)
            all_global = [g_doc_map[doc_id] for doc_id in global_fused[:30] if doc_id in g_doc_map]
            # 去重
            seen_global = set()
            for c in target_final:
                seen_global.add(c[:200])
            supplement = [d for d in all_global if d[:200] not in seen_global][:supplement_needed]
            print(f"  [RAG] 全局补充 {len(supplement)} 个片段（去重后）")
            final_docs = target_final + supplement
        final_docs = target_final

        if target_final:
            print(f"\n[RAG] 最终用于生成的片段: target {len(target_final)} 个（全部来自目标文档）")
            for i, doc in enumerate(target_final):
                print(f"  [{i+1}] {doc[:120]}...")
        else:
            print(f"\n[RAG] ⚠ Target 文档检索无结果（file_name 元数据可能缺失），返回空")
            final_docs = []
        
        sources = []
        for text in final_docs:
            src_match = re.search(r'\[Source:\s*([^\]]+)\]', text)
            file_name = src_match.group(1) if src_match else "未知文件"
            chap_match = re.search(r'Chapter:\s*([^|\n]+)', text)
            clause_match = re.search(r'Clause:\s*([^|\n]+)', text)
            section = clause_match.group(1).strip() if clause_match else (chap_match.group(1).strip() if chap_match else "")
            page_match = re.search(r'Page:\s*(\d+)', text)
            page = int(page_match.group(1)) if page_match else 0
            score_val = round(score_map.get(text, 0.0), 4)
            if score_val > 0:
                print(f"  [RAG] 片段 {len(sources)+1} 相关性分数: {score_val}")
            sources.append({
                "file_name": file_name,
                "section": section,
                "page": page,
                "preview": text[:200],
                "full_text": text,
                "score": score_val
            })
        
        print("\n" + "="*60)
        print(f"  [RAG Agent] 完成 → 共检索 {len(target_docs)} 个目标文档，使用 {len(final_docs)} 个片段")
        print("="*60)
        return {
            "context_docs": final_docs,
            "final_context_docs": final_docs,
            "sources": sources,
        }
    
    # ========== 无 target_docs 时：全局多路召回 + 重排序（无元数据过滤） ==========
    else:
        print("[RAG] 未指定目标文档，进行全库混合检索...")
        
        retriever = get_retriever()
        bm25_retriever = get_bm25_retriever()
        query_embedding = embed_query(search_query)
        
        # ---- 1. 向量检索（全局，无过滤） ----
        print("[RAG] ① 向量检索（全局）...")
        vector_docs = retriever.retrieve(
            query_embedding,
            top_k=100,
            score_threshold=-1.0
        )
        vector_results = [(f"vec_{idx}", doc.metadata.get('score', 0.0)) for idx, doc in enumerate(vector_docs)]
        user_vector_docs = search_user_knowledge(user_id, query_embedding, top_k=100)
        user_vector_results = [(f"user_vec_{idx}", score) for idx, (_, score) in enumerate(user_vector_docs)]
        doc_map = {}
        for idx, doc in enumerate(vector_docs):
            doc_id = f"vec_{idx}"
            doc_map[doc_id] = doc.page_content
        for idx, (doc, _) in enumerate(user_vector_docs):
            doc_id = f"user_vec_{idx}"
            doc_map[doc_id] = doc.page_content
        
        # ---- 2. BM25 检索（全局，无过滤） ----
        print("[RAG] ② BM25 检索（全局）...")
        bm25_raw = bm25_retriever.search(
            query=search_query,
            top_k=100
        )
        bm25_results = [(doc_id, score) for doc_id, _, score in bm25_raw]
        for doc_id, content, _ in bm25_raw:
            if doc_id not in doc_map:
                doc_map[doc_id] = content
        
        # ---- 3. 融合 ----
        print("[RAG] ③ 融合 BM25 + 向量结果...")
        fused_doc_ids = reciprocal_rank_fusion([vector_results, bm25_results, user_vector_results], k=60)
        candidate_docs = [doc_map[doc_id] for doc_id in fused_doc_ids[:30] if doc_id in doc_map]
        
        if not candidate_docs:
            print("[RAG] ⚠ 无相关文档，返回空结果")
            print("\n" + "="*60)
            print("  [RAG Agent] 完成（无检索结果）")
            print("="*60)
            return {
                "context_docs": [],
                "final_context_docs": [],
                "sources": []
            }
        
        # ---- 4. 重排序 ----
        print(f"[RAG] ④ 重排序（{len(candidate_docs)} 个候选）...")
        reranked = call_siliconflow_rerank(
            query=search_query,
            documents=candidate_docs,
            top_n=20
        )
        if reranked:
            indices = [item['index'] for item in reranked]
            final_docs = [candidate_docs[idx] for idx in indices if idx < len(candidate_docs)]
            score_map = {candidate_docs[item['index']]: item.get('relevance_score', 0.0) for item in reranked if item['index'] < len(candidate_docs)}
        else:
            final_docs = candidate_docs[:20]
            score_map = {}
        print(f"[RAG] 最终片段数: {len(final_docs)}")
        
        print("\n[RAG] → 调用 LLM 生成答案...")
        context = "\n\n".join(final_docs)
        prompt = RAG_PROMPT.format(context=context, user_query=user_msg)
        response = llm.invoke(prompt)
        answer = response.content.strip()
        record_llm_usage(response, agent="rag")
        
        sources = []
        for text in final_docs:
            src_match = re.search(r'\[Source:\s*([^\]]+)\]', text)
            file_name = src_match.group(1) if src_match else "未知文件"
            chap_match = re.search(r'Chapter:\s*([^|\n]+)', text)
            clause_match = re.search(r'Clause:\s*([^|\n]+)', text)
            section = clause_match.group(1).strip() if clause_match else (chap_match.group(1).strip() if chap_match else "")
            page_match = re.search(r'Page:\s*(\d+)', text)
            page = int(page_match.group(1)) if page_match else 0
            score_val = round(score_map.get(text, 0.0), 4)
            if score_val > 0:
                print(f"  [RAG] 片段 {len(sources)+1} 相关性分数: {score_val}")
            sources.append({
                "file_name": file_name,
                "section": section,
                "page": page,
                "preview": text[:200],
                "full_text": text,
                "score": score_val
            })
        
        print("\n" + "="*60)
        print(f"  [RAG Agent] 完成 → 全库检索，使用 {len(final_docs)} 个片段")
        print("="*60)
        return {
            "context_docs": candidate_docs,
            "final_context_docs": final_docs,
            "sources": sources,
        }
