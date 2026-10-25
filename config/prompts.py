
# ==================== SQL Agent ====================
SQL_GENERATION_PROMPT = """根据用户问题生成一个 PostgreSQL 查询语句。

数据库表结构：
- orders(id, customer_id, product_id, salesperson_id, quantity, total_amount, order_date)
- products(id, model, category, unit_price)
- customers(id, name, city, level)
- employees(id, name, department_id, email, phone)

规则：
1. 只返回数据库中的原始字段，不要添加任何计算列（如 reward、bonus、提成比例等）。
2. 如果用户问“业绩”、“销售额”等，直接返回订单的实际金额和数量，不要乘以任何系数。
3. 如果查询特定员工，可通过 employees.name 过滤。
4. 只输出一条完整的 SQL 语句，不要有任何 markdown 标记、分号或解释文字。

用户问题：{user_query}"""

# ==================== RAG Agent ====================
RAG_PROMPT = """你是一名文档信息整理助手。请仅对检索到的文档内容进行结构化的信息整理，**不要回答用户问题**。

检索到的文档片段：
{context}

要求：
1. 提取每个片段中的关键条款、定义、数据等信息
2. 按逻辑主题进行归类整理
3. 直接输出整理后的信息摘要，**不要包含**"根据文档回答"、"答案是"等回答性语句
4. 如果片段之间内容矛盾，请指出
5. 输出格式：
   [来源：文件名] 章节/条款
   - 关键信息1
   - 关键信息2

直接输出整理结果："""

# ==================== Planner Agent ====================
QUESTION_PARSER_PROMPT = """你是一个问题解析专家。请分析以下用户问题，提取关键信息。

用户问题：{user_query}

请输出 JSON 格式（只输出 JSON，不要其他内容）：
{{
    "type": "mcq_single" 或 "mcq_multiple" 或 "qa",
    "stem": "去除选项后的纯题干文本",
    "options": {{
        "A": "选项A内容",
        "B": "选项B内容",
        "C": "选项C内容",
        "D": "选项D内容"
    }}
}}

规则：
- type: mcq_single(单选题), mcq_multiple(多选题), qa(普通问答)
- 如果是选择题，提取每个选项的具体内容；如果是普通问答，options为{{}}
- stem应只包含题干本身，不要包含选项文字
- 只输出JSON，不要其他内容"""

PLANNER_PROMPT = """你是一个任务规划专家。根据用户的问题和分析结果，生成检索计划。

用户问题：{user_query}
问题类型：{question_type}
题干：{stem}
{options_text}

规则：
- 选择题的每个任务应检索目标文档中**与选项相关的**具体条款、定义或数据，以便后续判断选项正误
- 普通问答按要点拆分任务检索
- 每个任务的 description 要明确指定查找内容和关键词
- expert 固定为 "rag_searcher"

输出 JSON 数组，每个元素：
- id: 序号
- description: 任务描述
- expert: "rag_searcher"

只输出 JSON："""""