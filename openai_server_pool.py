"""
OpenAI Compatible API Server with Account Pool
使用账号池轮询处理请求
"""
import asyncio
import json
import time
import uuid
import os
os.environ['NO_PROXY'] = '*'

from typing import AsyncGenerator, Optional, List, Dict, Any
from contextlib import asynccontextmanager
import logging

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

from account_pool import get_account_pool, Account, CONFIG

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)


class ChatMessage(BaseModel):
    role: str
    content: str


class ChatCompletionRequest(BaseModel):
    model: str = "claude-opus-4-6"
    messages: List[ChatMessage]
    temperature: Optional[float] = 0.7
    max_tokens: Optional[int] = None
    stream: bool = False


class ModelInfo(BaseModel):
    id: str
    object: str = "model"
    created: int = Field(default_factory=lambda: int(time.time()))
    owned_by: str = "emergent"


class ModelsResponse(BaseModel):
    object: str = "list"
    data: List[ModelInfo]


# 全局账号池
account_pool = None


def create_conv(account: Account, prompt: str, model: str) -> str:
    """创建对话"""
    ref_id = str(uuid.uuid4())
    body = {
        "client_ref_id": ref_id,
        "payload": {
            "processor_type": "env_only",
            "is_cloud": True,
            "env_image": "us-central1-docker.pkg.dev/emergent-default/emergent-container-hub/fastapi_react_mongo_shadcn_base_image_cloud_arm:release-26092025-2",
            "branch": "",
            "repository": "",
            "enable_visual_edit": True,
            "prompt_name": "auto_prompt_selector",
            "prompt_version": "latest",
            "work_space_dir": "",
            "task": prompt,
            "model_name": model,
            "model_manually_selected": True,
            "per_instance_cost_limit": 25,
            "agentic_skills": [],
            "plugin_version": "release-10092025-1",
            "base64_image_list": [],
            "human_timestamp": int(time.time() * 1000),
            "asset_upload_enabled": True,
            "is_pro_user": False,
            "testMode": False,
            "thinking_level": "thinking",
            "job_mode": "public",
            "mcp_id": []
        },
        "model_name": model,
        "resume": False,
        "ads_metadata": {"app_version": "1.1.28"}
    }
    
    resp = account.session.post(
        f"{CONFIG['BASE_API_URL']}/jobs/v0/submit-queue/",
        headers={
            "Authorization": f"Bearer {account.jwt}",
            "Origin": CONFIG["APP_URL"],
            "Referer": f"{CONFIG['APP_URL']}/",
            "Content-Type": "application/json"
        },
        json=body,
        timeout=30
    )
    resp.raise_for_status()
    return ref_id


def fetch_messages(account: Account, conv_id: str) -> List[Dict]:
    """获取消息"""
    messages = []
    try:
        resp = account.session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{conv_id}/history?limit=50",
            headers={"Authorization": f"Bearer {account.jwt}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        for item in data.get("data", []):
            payload = item.get("traj_payload", {})
            reasoning = payload.get("reasoning_content")
            if reasoning:
                messages.append({"type": "reasoning", "content": reasoning})
            text = payload.get("thought")
            if text:
                messages.append({"type": "text", "content": text})
    except Exception as e:
        logger.error(f"Fetch error: {e}")
    return messages


def is_complete(account: Account, conv_id: str) -> bool:
    """检查是否完成"""
    try:
        resp = account.session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{conv_id}/history?limit=50",
            headers={"Authorization": f"Bearer {account.jwt}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("latest_request_id") is not None
    except:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    """应用生命周期"""
    global account_pool
    
    try:
        logger.info("Initializing account pool...")
        account_pool = get_account_pool(accounts_file="accounts.json", max_accounts=20)
        
        # 如果账号不足，自动注册
        if account_pool.get_account_count() < 3:
            logger.info("Insufficient accounts, registering new ones...")
            account_pool.batch_register(count=3)
        
        logger.info(f"✅ Account pool ready: {account_pool.get_active_account_count()}/{account_pool.get_account_count()} accounts active")
    except Exception as e:
        logger.error(f"❌ Init failed: {e}")
        raise
    yield
    
    # 保存账号状态
    if account_pool:
        account_pool.save_accounts()
        logger.info("Account pool saved")


app = FastAPI(title="Emergent.sh OpenAI API (Multi-Account)", lifespan=lifespan)
SUPPORTED_MODELS = ["claude-opus-4-6", "claude-sonnet-4-5", "gpt-4o", "gpt-4o-mini"]


@app.get("/v1/models")
async def list_models():
    """列出模型"""
    return ModelsResponse(data=[ModelInfo(id=m) for m in SUPPORTED_MODELS])


@app.post("/v1/chat/completions")
async def chat(request: ChatCompletionRequest):
    """聊天完成 - 轮询使用账号池"""
    if not account_pool:
        raise HTTPException(status_code=503, detail="Account pool not initialized")
    
    # 获取下一个可用账号
    account = account_pool.get_next_account()
    if not account:
        # 如果没有可用账号，尝试注册新账号
        logger.warning("No active accounts, trying to register new ones...")
        new_accounts = account_pool.batch_register(count=1)
        if new_accounts:
            account = new_accounts[0]
        else:
            raise HTTPException(status_code=503, detail="No active accounts available")
    
    prompt = "\n\n".join([f"{m.role}: {m.content}" for m in request.messages])
    
    try:
        conv_id = create_conv(account, prompt, request.model)
        logger.info(f"Created conversation {conv_id} using account {account.email}")
    except Exception as e:
        logger.error(f"Failed to create conversation with {account.email}: {e}")
        # 如果失败，标记账号为不可用并重试
        account_pool.deactivate_account(account.jwt)
        raise HTTPException(status_code=500, detail=f"Account {account.email} failed: {str(e)}")
    
    if request.stream:
        return StreamingResponse(
            stream_resp(account, conv_id, request.model, prompt),
            media_type="text/event-stream"
        )
    return await non_stream_resp(account, conv_id, request.model, prompt)


async def non_stream_resp(account: Account, conv_id: str, model: str, prompt: str):
    """非流式响应"""
    texts = []
    for i in range(300):  # 最多等待150秒
        msgs = fetch_messages(account, conv_id)
        for m in msgs:
            if m["type"] == "text":
                texts.append(m["content"])
        
        if is_complete(account, conv_id):
            logger.info(f"Conversation complete after {i} iterations")
            break
        await asyncio.sleep(0.5)
    
    response = "".join(texts) if texts else "No response"
    
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": response},
            "finish_reason": "stop"
        }],
        "usage": {
            "prompt_tokens": len(prompt) // 4,
            "completion_tokens": len(response) // 4,
            "total_tokens": (len(prompt) + len(response)) // 4
        }
    }


async def stream_resp(account: Account, conv_id: str, model: str, prompt: str) -> AsyncGenerator[str, None]:
    """流式响应"""
    id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    
    # 发送开始标记
    yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})}\n\n'
    
    seen = set()
    for _ in range(300):
        msgs = fetch_messages(account, conv_id)
        for m in msgs:
            key = f"{m['type']}:{m['content']}"
            if key not in seen and m["type"] == "text":
                seen.add(key)
                yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"content": m["content"]}, "finish_reason": None}]})}\n\n'
        
        if is_complete(account, conv_id):
            break
        await asyncio.sleep(0.5)
    
    # 发送结束标记
    yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
    yield "data: [DONE]\n\n"


@app.get("/health")
async def health():
    """健康检查"""
    if account_pool:
        stats = account_pool.get_stats()
        return {"status": "healthy", **stats}
    return {"status": "initializing"}


@app.get("/stats")
async def stats():
    """账号池统计"""
    if account_pool:
        return account_pool.get_stats()
    return {"error": "Account pool not initialized"}


@app.post("/admin/register")
async def admin_register(count: int = 1):
    """管理员接口：批量注册账号"""
    if not account_pool:
        raise HTTPException(status_code=503, detail="Account pool not initialized")
    
    new_accounts = account_pool.batch_register(count=count)
    return {
        "registered": len(new_accounts),
        "total_accounts": account_pool.get_account_count()
    }


@app.get("/")
async def root():
    """根路径"""
    return {
        "message": "Emergent.sh OpenAI API (Multi-Account Pool)",
        "docs": "/docs",
        "endpoints": {
            "/v1/models": "List models",
            "/v1/chat/completions": "Chat completions (auto round-robin)",
            "/health": "Health check",
            "/stats": "Account pool statistics",
            "/admin/register?count=N": "Register N new accounts"
        }
    }


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
