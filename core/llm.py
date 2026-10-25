import os
from dotenv import load_dotenv
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage

# 加载环境变量
load_dotenv()

# 全局变量存储当前配置
current_llm_config = {
    "api_key": os.getenv("deepseek_API_KEY", ""),
    "api_base": os.getenv("deepseek_API_BASE", "https://api.deepseek.com"),
    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.0")),
}

# 存储原始环境变量配置，用于恢复默认
DEFAULT_LLM_CONFIG = {
    "api_key": os.getenv("deepseek_API_KEY", ""),
    "api_base": os.getenv("deepseek_API_BASE", "https://api.deepseek.com"),
    "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
    "temperature": float(os.getenv("LLM_TEMPERATURE", "0.0")),
}

# 创建全局LangChain兼容的LLM实例（只使用invoke方法）
llm = ChatOpenAI(
    api_key=current_llm_config["api_key"],
    base_url=current_llm_config["api_base"],
    model_name=current_llm_config["model"],
    temperature=current_llm_config["temperature"],
    request_timeout=int(os.getenv("LLM_REQUEST_TIMEOUT", "120")),
    max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
)

# 兼容旧代码的包装函数
def chat_completions_create(**kwargs):
    """兼容旧代码的chat.completions.create调用"""
    messages = kwargs.get("messages", [])
    prompt = "\n".join([msg["content"] for msg in messages])
    response = llm.invoke(prompt)
    return type('obj', (object,), {
        'choices': [type('choice', (object,), {
            'message': type('msg', (object,), {
                'content': response.content
            })()
        })()]
    })()

# 兼容旧代码的client对象
client = type('obj', (object,), {
    'chat': type('chat', (object,), {
        'completions': type('completions', (object,), {
            'create': chat_completions_create
        })()
    })()
})()

def update_llm_config(new_config: dict) -> None:
    """更新LLM配置"""
    global current_llm_config, llm, client
    
    # 更新全局配置
    current_llm_config.update(new_config)
    
    # 如果API Key为空，使用环境变量中的默认值
    if not current_llm_config["api_key"]:
        current_llm_config["api_key"] = DEFAULT_LLM_CONFIG["api_key"]
    
    # 重新创建LangChain兼容的LLM实例
    llm = ChatOpenAI(
        api_key=current_llm_config["api_key"],
        base_url=current_llm_config["api_base"],
        model_name=current_llm_config["model"],
        temperature=current_llm_config["temperature"],
        request_timeout=int(os.getenv("LLM_REQUEST_TIMEOUT", "120")),
        max_retries=int(os.getenv("LLM_MAX_RETRIES", "2")),
    )
    
    # 更新兼容旧代码的client对象
    def new_chat_completions_create(**kwargs):
        """兼容旧代码的chat.completions.create调用"""
        messages = kwargs.get("messages", [])
        prompt = "\n".join([msg["content"] for msg in messages])
        response = llm.invoke(prompt)
        return type('obj', (object,), {
            'choices': [type('choice', (object,), {
                'message': type('msg', (object,), {
                    'content': response.content
                })()
            })()]
        })()
    
    client = type('obj', (object,), {
        'chat': type('chat', (object,), {
            'completions': type('completions', (object,), {
                'create': new_chat_completions_create
            })()
        })()
    })()
    
    print(f"LLM配置已更新: {current_llm_config['model']}")

def get_llm_config() -> dict:
    """获取当前LLM配置"""
    return current_llm_config.copy()

# 初始化时加载.env配置
load_dotenv()