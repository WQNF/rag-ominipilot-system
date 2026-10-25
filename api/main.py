import json
import os
import queue
import threading
import time
import uuid
from pathlib import Path
from typing import List, Dict, Optional
from dotenv import load_dotenv

# 加载环境变量
load_dotenv()

from fastapi import FastAPI
from fastapi.responses import StreamingResponse, FileResponse
from pydantic import BaseModel
from langchain_core.messages import HumanMessage
from langchain_core.callbacks import BaseCallbackHandler
from core.graph import graph  # 你的编译好的 LangGraph 图
from langchain_core.messages import AIMessage
from fastapi import Depends, HTTPException, status
from fastapi.security import OAuth2PasswordRequestForm
from api.dependencies.auth import (
    AdminUserCreate,
    AdminUserUpdate,
    PublicUser,
    RegisterRequest,
    User,
    authenticate_user,
    create_access_token,
    create_user,
    delete_user,
    get_current_user,
    list_users,
    require_admin,
    update_user,
    Token,
)
from prometheus_fastapi_instrumentator import Instrumentator
from fastapi.middleware.cors import CORSMiddleware

# 导入LLM配置更新函数
from core.llm import update_llm_config, get_llm_config

app = FastAPI()

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

# 存储用户配置的文件
USER_SETTINGS_FILE = Path(__file__).parent / "user_settings.json"

class Settings(BaseModel):
    provider: str
    model: str
    api_key: Optional[str] = None
    api_base: str
    temperature: float
    custom_model: str = ""
    custom_api_base: str = ""
    api_keys: dict[str, str] = {}
    retrieval_top_k: Optional[int] = 80
    retrieval_nprobe: Optional[int] = 16
    retrieval_threshold: Optional[float] = 0.3

@app.get("/settings")
async def get_settings():
    """获取当前设置"""
    try:
        # 从环境变量获取默认配置
        default_settings = {
            "provider": "deepseek",
            "model": os.getenv("LLM_MODEL", "deepseek-v4-flash"),
            "api_key": os.getenv("deepseek_API_KEY", ""),
            "api_base": os.getenv("deepseek_API_BASE", "https://api.deepseek.com"),
            "retrieval_top_k": int(os.getenv("RETRIEVAL_TOP_K", "80")),
            "retrieval_nprobe": int(os.getenv("RETRIEVAL_NPROBE", "16")),
            "retrieval_threshold": float(os.getenv("RETRIEVAL_THRESHOLD", "0.3")),
            "temperature": float(os.getenv("LLM_TEMPERATURE", "0.0")),
            "custom_model": "",
            "custom_api_base": "",
            "api_keys": {},
            "providers": {
                "deepseek": {
        "api_base": "https://api.deepseek.com",
        "models": ["deepseek-v4-flash", "deepseek-v4-pro"]
    },
                "doubao": {
        "api_base": "https://ark.cn-beijing.volces.com/api/v3",
        "models": ["doubao-seed-evolving", "doubao-seed-2-1-pro-260628", "doubao-seed-2-1-turbo-260628"]
    },
                "minimax": {
        "api_base": "https://api.minimaxi.com/anthropic",
        "models": ["MiniMax-M2.7", "MiniMax-M2.7-highspeed", "MiniMax-M3"]
    },
                "kimi": {
        "api_base": "https://api.moonshot.cn/v1",
        "models": ["kimi-k2.7-code-highspeed", "kimi-k2.7-code", "kimi-k2.6"]
    },
                "other": {
                    "api_base": "",
                    "models": ["custom"]
                }
            }
        }
        
        # 如果有用户保存的设置，覆盖默认配置
        if USER_SETTINGS_FILE.exists():
            with open(USER_SETTINGS_FILE, "r", encoding="utf-8") as f:
                user_settings = json.load(f)
                default_settings.update(user_settings)
        
        return default_settings
    except Exception as e:
        raise HTTPException(status_code=500, detail=str(e))

@app.post("/settings")
async def save_settings(settings: Settings):
    """保存设置并更新LLM配置"""
    try:
        # 准备保存的设置数据
        save_data = settings.dict()
        
        # 保存到文件
        with open(USER_SETTINGS_FILE, "w", encoding="utf-8") as f:
            json.dump(save_data, f, ensure_ascii=False, indent=2)
        
        # 更新LLM配置
        llm_config = {
            "api_base": save_data["api_base"],
            "model": save_data["model"],
            "temperature": save_data["temperature"],
        }
        
        # 添加检索参数配置
        if save_data.get("retrieval_top_k") is not None:
            llm_config["retrieval_top_k"] = save_data["retrieval_top_k"]
        if save_data.get("retrieval_nprobe") is not None:
            llm_config["retrieval_nprobe"] = save_data["retrieval_nprobe"]
        if save_data.get("retrieval_threshold") is not None:
            llm_config["retrieval_threshold"] = save_data["retrieval_threshold"]
        
        # 如果是deepseek模型且没有提供api_key，使用环境变量中的默认值
        if save_data["provider"] == "deepseek" and not save_data.get("api_key"):
            llm_config["api_key"] = os.getenv("deepseek_API_KEY", "")
        else:
            llm_config["api_key"] = save_data["api_key"]
        
        update_llm_config(llm_config)
        
        return {"message": "设置已保存并生效"}
    except Exception as e:
        # 打印详细错误信息以便调试
        print(f"保存设置失败: {str(e)}")
        raise HTTPException(status_code=500, detail=str(e))

# 初始化监控，会自动添加 /metrics 端点
instrumentator = Instrumentator()
instrumentator.instrument(app).expose(app)

from typing import List, Dict, Optional

class ChatRequest(BaseModel):
    messages: List[Dict[str, str]]

class ChatResponse(BaseModel):
    reply: str

@app.post("/chat", response_model=ChatResponse)
async def chat_endpoint(req: ChatRequest):
    # 构造初始状态
    original_query = req.messages[0]["content"]
    initial_state = {
        "messages": [HumanMessage(content=original_query)],
        "original_query": original_query,
        "next_agent": "planner",
        "task_completed": False,
        "visited_agents": [],
        "step_count": 0,
        "plan": [],
        "user_id": "default_user",     # 新增
        "department": "general",       # 新增
    }
    result = graph.invoke(initial_state)
    # 获取最后一条消息的文本
    reply = result["messages"][-1].content
    return ChatResponse(reply=reply)

# 登录端点（获取 token）
@app.post("/token", response_model=Token)
async def login(form_data: OAuth2PasswordRequestForm = Depends()):
    user = authenticate_user(form_data.username, form_data.password)
    if not user:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="用户名或密码错误",
            headers={"WWW-Authenticate": "Bearer"},
        )
    access_token = create_access_token(
        data={"sub": user.username, "department": user.department, "role": user.role}
    )
    return {
        "access_token": access_token,
        "token_type": "bearer",
        "username": user.username,
        "role": user.role,
        "department": user.department,
        "full_name": user.full_name,
    }


@app.post("/register", response_model=PublicUser, status_code=status.HTTP_201_CREATED)
async def register_user(req: RegisterRequest):
    """普通用户注册。注册用户默认是 user 角色。"""
    return create_user(
        username=req.username,
        password=req.password,
        confirm_password=req.confirm_password,
        full_name=req.full_name,
        email=req.email,
        department=req.department,
        role="user",
        is_active=True,
    )


@app.get("/users/me", response_model=User)
async def get_me(current_user: User = Depends(get_current_user)):
    return current_user


@app.get("/admin/users", response_model=list[PublicUser])
async def admin_list_users(_: User = Depends(require_admin)):
    return list_users()


@app.post("/admin/users", response_model=PublicUser, status_code=status.HTTP_201_CREATED)
async def admin_create_user(req: AdminUserCreate, _: User = Depends(require_admin)):
    return create_user(
        username=req.username,
        password=req.password,
        confirm_password=req.confirm_password,
        full_name=req.full_name,
        email=req.email,
        department=req.department,
        role=req.role,
        is_active=req.is_active,
    )


@app.put("/admin/users/{username}", response_model=PublicUser)
async def admin_update_user(username: str, req: AdminUserUpdate, current_user: User = Depends(require_admin)):
    if username == current_user.username:
        if req.role is not None and req.role != "admin":
            raise HTTPException(status_code=400, detail="不能将当前管理员降级为普通用户")
        if req.is_active is False:
            raise HTTPException(status_code=400, detail="不能禁用当前登录的管理员账户")
    return update_user(username, req)


@app.delete("/admin/users/{username}")
async def admin_delete_user(username: str, current_user: User = Depends(require_admin)):
    delete_user(username, current_user.username)
    return {"status": "success", "message": "用户已删除"}


@app.post("/chat/stream")
def chat_stream(req: ChatRequest, current_user: User = Depends(get_current_user)):
    from core.stream import init_stream, shutdown_stream, get_log_queue
    import threading

    def event_generator():
        has_message = False
        original_query = req.messages[0]["content"]
        input_state = {
            "messages": [HumanMessage(content=original_query)],
            "original_query": original_query,
            "next_agent": "planner",
            "task_completed": False,
            "visited_agents": [],
            "step_count": 0,
            "plan": [],
            "user_id": current_user.username,
            "department": current_user.department,
            "role": current_user.role,
        }

        init_stream()
        log_queue = get_log_queue()
        result_queue = queue.Queue()

        # 计时
        total_start = time.time()
        node_timings = {}
        current_node = None
        last_end_time = total_start

        def _record_node_end(node_name):
            nonlocal last_end_time
            if node_name:
                if node_name not in node_timings:
                    node_timings[node_name] = [last_end_time, None]
                if node_timings[node_name][1] is None:
                    node_timings[node_name][1] = time.time()
                    last_end_time = time.time()

        def _record_node_start(node_name):
            nonlocal current_node
            if current_node and current_node != node_name:
                _record_node_end(current_node)
            if node_name not in node_timings:
                node_timings[node_name] = [time.time(), None]
            current_node = node_name

        def run_graph():
            try:
                for chunk in graph.stream(
                    input_state, stream_mode=["updates", "messages"]
                ):
                    result_queue.put(chunk)
                result_queue.put(None)
            except Exception as e:
                result_queue.put(e)

        thread = threading.Thread(target=run_graph, daemon=True)
        thread.start()

        graph_done = False
        try:
            while not graph_done:
                while True:
                    try:
                        log_msg = log_queue.get_nowait()
                        escaped = json.dumps({'type': 'thinking_token', 'content': log_msg})
                        yield f"data: {escaped}\n\n"
                    except queue.Empty:
                        break

                try:
                    chunk = result_queue.get(timeout=0.1)
                except queue.Empty:
                    continue

                if chunk is None:
                    graph_done = True
                    break
                if isinstance(chunk, Exception):
                    import traceback
                    traceback.print_exc()
                    escaped = json.dumps({'type': 'error', 'message': str(chunk)})
                    yield f"data: {escaped}\n\n"
                    has_message = True
                    graph_done = True
                    break

                mode, data = chunk[0], chunk[1]

                if mode == "messages":
                    msg_chunk, metadata = data
                    node = metadata.get("langgraph_node", "")
                    content = msg_chunk.content if hasattr(msg_chunk, 'content') else str(msg_chunk)
                    if node and node != current_node:
                        _record_node_start(node)
                    if content and node in ("planner", "rag_searcher", "dispatcher", "supervisor", "__start__"):
                        escaped = json.dumps({'type': 'thinking_token', 'content': content})
                        yield f"data: {escaped}\n\n"

                elif mode == "updates":
                    for node_name, update in data.items():
                        _record_node_end(node_name)
                        if "sources" in update:
                            escaped = json.dumps({'type': 'sources', 'sources': update['sources']})
                            yield f"data: {escaped}\n\n"
                        if node_name == "summarize" and "messages" in update:
                            last_msg = update["messages"][-1]
                            answer = last_msg.content if hasattr(last_msg, 'content') else str(last_msg)
                            escaped = json.dumps({'type': 'answer', 'content': answer})
                            yield f"data: {escaped}\n\n"
                            has_message = True

            while True:
                try:
                    log_msg = log_queue.get_nowait()
                    escaped = json.dumps({'type': 'thinking_token', 'content': log_msg})
                    yield f"data: {escaped}\n\n"
                except queue.Empty:
                    break

            # 计时信息
            total_time = round(time.time() - total_start, 2)
            _record_node_end(current_node)
            timing_detail = {"total": total_time, "nodes": {}}
            for node_name, times in node_timings.items():
                if times[1] is not None:
                    timing_detail["nodes"][node_name] = round(times[1] - times[0], 2)
            escaped = json.dumps({'type': 'timing', 'timing': timing_detail})
            yield f"data: {escaped}\n\n"

        finally:
            if not has_message:
                escaped = json.dumps({'type': 'message', 'content': '⚠️ 系统未能生成有效回答，请检查后端日志。'})
                yield f"data: {escaped}\n\n"
            escaped = json.dumps({'type': 'done'})
            yield f"data: {escaped}\n\n"
            shutdown_stream()
            thread.join(timeout=5)

    return StreamingResponse(event_generator(), media_type="text/event-stream")


# ========== 文件服务（PDF 在线查看 + 页面跳转） ==========
DATA_DIR = Path(__file__).resolve().parent.parent / "data"

@app.get("/files/{file_path:path}")
async def serve_file(file_path: str):
    """仅提供公共知识库文件，支持浏览器在线打开和 #page=N 跳转。"""
    full_path = DATA_DIR / file_path
    full_path = full_path.resolve()
    if not full_path.exists():
        raise HTTPException(status_code=404, detail="文件不存在")

    public_roots = [
        (DATA_DIR / "pdf").resolve(),
        (DATA_DIR / "word").resolve(),
        (DATA_DIR / "excel").resolve(),
    ]
    try:
        is_public_file = any(full_path.is_relative_to(root) for root in public_roots)
    except AttributeError:
        is_public_file = any(
            os.path.commonpath([str(full_path), str(root)]) == str(root)
            for root in public_roots
        )
    if not is_public_file:
        raise HTTPException(status_code=403, detail="禁止访问")
    suffix = full_path.suffix.lower()
    media_types = {
        '.pdf': 'application/pdf',
        '.doc': 'application/msword',
        '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
        '.xls': 'application/vnd.ms-excel',
        '.xlsx': 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
    }
    media_type = media_types.get(suffix, 'application/octet-stream')
    return FileResponse(full_path, media_type=media_type, headers={"Content-Disposition": "inline"})


# ========== 知识库 API（给 React 前端用） ==========
from fastapi import UploadFile, File
from tools.init_milvus import build_knowledge_base, build_pdf_knowledge_base, build_excel_knowledge_base
from tools.user_knowledge import (
    ingest_user_file,
    list_user_documents,
    get_user_document,
    delete_user_document,
)

@app.get("/knowledge/docs")
async def list_documents(current_user: User = Depends(get_current_user)):
    """返回公共知识库文件列表，所有已登录用户均可查看。"""
    result = {"pdf": [], "word": [], "excel": []}

    for subdir, key, suffixes in [
        ("pdf", "pdf", (".pdf",)),
        ("excel", "excel", (".xls", ".xlsx")),
    ]:
        dir_path = DATA_DIR / subdir
        if not dir_path.exists():
            continue
        for file_path in sorted(dir_path.iterdir()):
            if file_path.is_file() and file_path.suffix.lower() in suffixes:
                result[key].append({
                    "name": file_path.name,
                    "type": key,
                    "scope": "public",
                    "path": file_path.relative_to(DATA_DIR).as_posix(),
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime,
                })

    word_dir = DATA_DIR / "word"
    if word_dir.exists():
        for file_path in sorted(word_dir.rglob("*")):
            if file_path.is_file() and file_path.suffix.lower() in (".doc", ".docx"):
                result["word"].append({
                    "name": file_path.name,
                    "type": "word",
                    "scope": "public",
                    "path": file_path.relative_to(DATA_DIR).as_posix(),
                    "size": file_path.stat().st_size,
                    "modified": file_path.stat().st_mtime,
                })
    return result


@app.post("/knowledge/upload")
async def upload_files(files: list[UploadFile] = File(...), current_user: User = Depends(get_current_user)):
    """上传文件到公共目录并导入公共知识库，所有已登录用户均可操作。"""
    type_map = {
        ".pdf": "pdf", ".docx": "word/docx", ".doc": "word/doc",
        ".xlsx": "excel", ".xls": "excel",
    }
    saved = {"pdf": [], "word": [], "excel": []}

    for f in files:
        safe_name = Path(f.filename or "").name
        ext = Path(safe_name).suffix.lower()
        subdir = type_map.get(ext)
        if not subdir:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {f.filename}")

        target_dir = DATA_DIR / subdir
        target_dir.mkdir(parents=True, exist_ok=True)
        file_path = target_dir / safe_name
        content = await f.read()
        with open(file_path, "wb") as fw:
            fw.write(content)

        if subdir.startswith("word"):
            saved["word"].append(str(file_path))
        else:
            saved[subdir].append(str(file_path))

    details = {"pdf": 0, "word": 0, "excel": 0}
    builders = [
        ("word", build_knowledge_base, {}),
        ("pdf", build_pdf_knowledge_base, {"skip_confirm": True}),
        ("excel", build_excel_knowledge_base, {"skip_confirm": True}),
    ]
    for file_type, builder, options in builders:
        if not saved[file_type]:
            continue
        result = builder(file_list=saved[file_type], **options)
        if result.get("status") != "success":
            raise HTTPException(
                status_code=500,
                detail=result.get("message", f"{file_type} 公共知识库导入失败"),
            )
        details[file_type] = result.get("chunks_count", 0)

    total_chunks = sum(details.values())
    return {"status": "success", "scope": "public", "total_chunks": total_chunks, "details": details}


@app.get("/knowledge/personal/docs")
async def list_personal_documents(current_user: User = Depends(get_current_user)):
    """返回当前用户的个人知识库文件列表。"""
    grouped = list_user_documents(current_user.username)
    for docs in grouped.values():
        for doc in docs:
            doc["scope"] = "personal"
            doc["path"] = f"personal/{doc['id']}"
    return grouped


@app.post("/knowledge/personal/upload")
async def upload_personal_files(
    files: list[UploadFile] = File(...),
    current_user: User = Depends(get_current_user),
):
    """上传到当前用户个人知识库；同名文件只替换该用户自己的旧版本。"""
    type_map = {
        ".pdf": "pdf", ".docx": "word", ".doc": "word",
        ".xlsx": "excel", ".xls": "excel",
    }
    details = {"pdf": 0, "word": 0, "excel": 0}
    documents = []
    incoming_dir = DATA_DIR / "user_knowledge" / "_incoming" / current_user.username
    incoming_dir.mkdir(parents=True, exist_ok=True)

    for f in files:
        safe_name = Path(f.filename or "").name
        ext = Path(safe_name).suffix.lower()
        file_type = type_map.get(ext)
        if not file_type:
            raise HTTPException(status_code=400, detail=f"不支持的文件类型: {f.filename}")

        temp_path = incoming_dir / f"{uuid.uuid4().hex}_{safe_name}"
        content = await f.read()
        with open(temp_path, "wb") as fw:
            fw.write(content)
        try:
            doc = ingest_user_file(current_user.username, temp_path, safe_name)
            doc["scope"] = "personal"
            doc["path"] = f"personal/{doc['id']}"
            details[file_type] += doc["chunk_count"]
            documents.append(doc)
        finally:
            if temp_path.exists():
                temp_path.unlink()

    return {
        "status": "success",
        "scope": "personal",
        "total_chunks": sum(details.values()),
        "details": details,
        "documents": documents,
    }


@app.delete("/knowledge/personal/docs/{document_id}")
async def delete_personal_document(document_id: str, current_user: User = Depends(get_current_user)):
    """删除当前用户个人知识库中的一个文件，同时删除对应向量。"""
    delete_user_document(current_user.username, document_id)
    return {"status": "success", "message": "文件已删除"}


@app.get("/knowledge/personal/docs/{document_id}/file")
async def serve_personal_file(document_id: str, current_user: User = Depends(get_current_user)):
    """仅允许文件所属用户读取个人知识库原文件。"""
    doc = get_user_document(current_user.username, document_id)
    if not doc:
        raise HTTPException(status_code=404, detail="文件不存在")
    file_path = Path(doc["path"]).resolve()
    personal_root = (DATA_DIR / "user_knowledge").resolve()
    try:
        is_personal_file = file_path.is_relative_to(personal_root)
    except AttributeError:
        is_personal_file = os.path.commonpath([str(file_path), str(personal_root)]) == str(personal_root)
    if not is_personal_file or not file_path.is_file():
        raise HTTPException(status_code=404, detail="文件不存在")
    return FileResponse(file_path, filename=doc["name"])

# ========== 知识库统计 API ==========
from pymilvus import Collection, connections, utility

@app.get("/knowledge/stats")
async def knowledge_stats():
    """返回知识库统计：文档数量、最后更新时间"""
    try:
        connections.connect(host=os.getenv("MILVUS_HOST", "localhost"), port=int(os.getenv("MILVUS_PORT", "19530")))
        if utility.has_collection("knowledge_base"):
            col = Collection("knowledge_base")
            col.load()
            num = col.num_entities
        else:
            num = 0
        
        # 最后更新时间：取 data 目录下最新文件的修改时间
        data_dir = DATA_DIR
        latest_mtime = 0
        if data_dir.exists():
            for f in data_dir.rglob("*"):
                if f.is_file():
                    mtime = f.stat().st_mtime
                    if mtime > latest_mtime:
                        latest_mtime = mtime
        
        from datetime import datetime
        update_time = datetime.fromtimestamp(latest_mtime).strftime("%Y-%m-%d %H:%M") if latest_mtime > 0 else "无"
        
        return {
            "count": num,
            "update_time": update_time,
            "status": "connected" if num >= 0 else "disconnected"
        }
    except Exception as e:
        return {"count": 0, "update_time": "无法连接", "error": str(e)}
    
    # ========== 快速测试面板 API ==========
import asyncio
import re
from datetime import datetime
from collections import defaultdict

TEST_DIR = DATA_DIR / "test_dataset"
test_stop_flag = False


class NodeTimingCallback(BaseCallbackHandler):
    def __init__(self):
        self.timings = {}
        self._stack = []

    def on_chain_start(self, serialized, inputs, *, run_id, parent_run_id=None, **kwargs):
        name = kwargs.get("name", "")
        if not name and serialized:
            name = serialized.get("name", "")
        if name and name != "LangGraph":
            self._stack.append((name, time.time()))

    def on_chain_end(self, outputs, *, run_id, **kwargs):
        if self._stack:
            name, start = self._stack.pop()
            self.timings[name] = self.timings.get(name, 0) + round(time.time() - start, 2)


class NodeTimingCallback(BaseCallbackHandler):
    def __init__(self):
        self.timings = {}
        self._starts = {}

    def on_chain_start(self, serialized, inputs, **kwargs):
        name = (serialized or {}).get("name", "") or kwargs.get("name", "")
        if name:
            self._starts[name] = time.time()

    def on_chain_end(self, outputs, **kwargs):
        name = (outputs or {}).get("name", "") if isinstance(outputs, dict) else ""
        # try to match with running name from kwargs
        run_id = kwargs.get("run_id")
        if run_id:
            for n, s in list(self._starts.items()):
                if n not in self.timings:
                    self.timings[n] = round(time.time() - s, 2)
                    del self._starts[n]
                    break

    def get_timings(self):
        return dict(self.timings)

@app.get("/test/datasets")
async def list_datasets():
    """列出可用的测试数据集"""
    datasets = []
    if TEST_DIR.exists():
        for f in sorted(TEST_DIR.iterdir()):
            if f.suffix == ".py":
                with open(f, "r", encoding="utf-8") as fh:
                    try:
                        questions = eval(fh.read())
                        count = len(questions) if isinstance(questions, list) else 0
                    except:
                        count = 0
                datasets.append({
                    "name": f.name,
                    "count": count,
                    "size": f.stat().st_size,
                })
    return datasets


def extract_answer_letter(reply: str) -> str:
    """从回复中提取答案字母"""
    if "无正确选项" in reply:
        return "X"
    patterns = [
        r'\*\*[^》]*?答案\*\*\s*[:：]\s*\*?([A-D])\*?',
        r'答案[:：]\s*\*?([A-D])\*?',
        r'[正确]确答案[是为：]\s*\*?([A-D])\*?',
        r'最终答案[:：]\s*\*?([A-D])\*?',
        r'选择\s*([A-D])\s*[。，]',
        r'选\s*([A-D])\s*$',
    ]
    for pattern in patterns:
        match = re.search(pattern, reply, re.MULTILINE)
        if match:
            return match.group(1)
    return "?"


@app.post("/test/stop")
async def stop_test():
    global test_stop_flag
    test_stop_flag = True
    return {"status": "stopping"}


@app.post("/test/run")
async def run_test(req: dict):
    """
    SSE 流式运行测试
    请求体: {"dataset": "all.py"}
    """
    dataset_name = req.get("dataset", "all.py")
    start_index = req.get("start_index", 0)
    file_path = TEST_DIR / dataset_name
    
    if not file_path.exists():
        return {"error": f"测试集 {dataset_name} 不存在"}
    
    with open(file_path, "r", encoding="utf-8") as f:
        try:
            questions = eval(f.read())
        except Exception as e:
            return {"error": f"解析测试集失败: {str(e)}"}
    
    if not isinstance(questions, list) or len(questions) == 0:
        return {"error": "测试集为空"}
    
    async def event_stream():
        total = len(questions)
        correct = 0
        wrong = 0
        errors = 0
        total_time = 0.0
        wrong_details = []
        difficulty_stats = defaultdict(lambda: {"total": 0, "correct": 0})
        
        yield f"data: {json.dumps({'type': 'start', 'total': total, 'dataset': dataset_name}, ensure_ascii=False)}\n\n"
        
        global test_stop_flag
        test_stop_flag = False
        
        for i, q in enumerate(questions):
            if i < start_index:
                continue
            if test_stop_flag:
                yield f"data: {json.dumps({'type': 'stopped', 'total': total, 'completed': i}, ensure_ascii=False)}\n\n"
                return
            qid = q.get("id", f"Q{i+1}")
            difficulty = q.get("difficulty", "unknown")
            expected = q.get("answer", "")
            source_doc = q.get("source_document", "")
            
            # 构建 prompt
            q_text = q.get("question", "")
            options = q.get("options", {})
            parts = [q_text]
            for letter in ["A", "B", "C", "D"]:
                if letter in options:
                    parts.append(f"{letter.lower()}{options[letter]}")
            prompt = "".join(parts)
            
            # 调用 graph
            import time
            try:
                initial_state = {
                    "messages": [HumanMessage(content=prompt)],
                    "original_query": prompt,
                    "next_agent": "planner",
                    "task_completed": False,
                    "visited_agents": [],
                    "step_count": 0,
                    "plan": [],
                    "user_id": "eval_user",
                    "department": "general",
                }
                t0 = time.time()
                timing_cb = NodeTimingCallback()
                result = await asyncio.to_thread(graph.invoke, initial_state, {"callbacks": [timing_cb]})
                elapsed = time.time() - t0
                reply = result["messages"][-1].content
                
                answer = extract_answer_letter(reply)
                is_correct = (answer == expected)
                
                if is_correct:
                    correct += 1
                else:
                    wrong += 1
                    wrong_details.append({
                        "id": qid,
                        "question": q_text[:100],
                        "expected": expected,
                        "got": answer,
                        "difficulty": difficulty,
                        "source": source_doc,
                        "reply_preview": reply[:200],
                    })
                
                total_time += elapsed
                difficulty_stats[difficulty]["total"] += 1
                if is_correct:
                    difficulty_stats[difficulty]["correct"] += 1
                
                yield f"data: {json.dumps({'type': 'result', 'index': i+1, 'total': total, 'qid': qid, 'correct': is_correct, 'answer': answer, 'expected': expected, 'elapsed': round(elapsed, 1), 'difficulty': difficulty, 'question': q_text, 'options': options, 'timing': timing_cb.timings}, ensure_ascii=False)}\n\n"
            except Exception as e:
                errors += 1
                yield f"data: {json.dumps({'type': 'result', 'index': i+1, 'total': total, 'qid': qid, 'correct': False, 'answer': 'ERROR', 'expected': expected, 'elapsed': 0, 'difficulty': difficulty, 'error': str(e)}, ensure_ascii=False)}\n\n"
        
        accuracy = round(correct / (total - errors) * 100, 1) if (total - errors) > 0 else 0
        avg_time = round(total_time / (total - errors), 1) if (total - errors) > 0 else 0
        
        yield f"data: {json.dumps({'type': 'done', 'total': total, 'correct': correct, 'wrong': wrong, 'errors': errors, 'accuracy': accuracy, 'avg_time': avg_time, 'total_time': round(total_time, 1), 'wrong_details': wrong_details, 'difficulty_stats': dict(difficulty_stats)}, ensure_ascii=False)}\n\n"
    
    return StreamingResponse(event_stream(), media_type="text/event-stream")


# ========== 系统设置 API ==========
from pydantic import BaseModel as PydanticBaseModel
import sys
import os
sys.path.insert(0, os.path.dirname(__file__))
from settings_manager import load_settings, save_settings, PROVIDER_PRESETS


class SettingsModel(PydanticBaseModel):
    provider: str
    model: str
    api_key: str
    api_base: str
    temperature: float
    custom_model: str = ""
    custom_api_base: str = ""


@app.get("/settings")
async def get_settings():
    """获取当前设置"""
    settings = load_settings()
    return {
        **settings,
        "providers": PROVIDER_PRESETS,
    }


@app.post("/settings")
async def update_settings(data: SettingsModel):
    """保存设置"""
    new_settings = {
        "provider": data.provider,
        "model": data.model,
        "api_key": data.api_key,
        "api_base": data.api_base,
        "temperature": data.temperature,
        "custom_model": data.custom_model,
        "custom_api_base": data.custom_api_base,
    }
    save_settings(new_settings)
    return {"status": "ok", "message": "设置已保存，重启后生效"}