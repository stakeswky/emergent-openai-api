"""
OpenAI Compatible API Server for Emergent.sh
使用 curl_cffi 模拟浏览器请求
"""
import asyncio
import json
import time
import uuid
import re
import os
os.environ['NO_PROXY'] = '*'

try:
    import curl_cffi
    USE_CURL_CFFI = True
except ImportError:
    print("Error: curl_cffi required")
    exit(1)

import logging
from typing import AsyncGenerator, Optional, List, Dict, Any
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

CONFIG = {
    "API_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNua3N4d2t5dW1oZHlreXJoaGNoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjQ3NzI2NDYsImV4cCI6MjA0MDM0ODY0Nn0.3unO6zdz2NilPL2xdxt7OjvZA19copj3Q7ulIjPVDLQ",
    "BASE_AUTH_URL": "https://auth.emergent.sh",
    "BASE_API_URL": "https://api.emergent.sh",
    "APP_URL": "https://app.emergent.sh",
    "EMAIL_API_URL": "https://mail.chatgpt.org.uk",
    "EMAIL_API_KEY": "gpt-test",
}

app_state = {"session": None, "jwt": None, "initialized": False}


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


def create_session():
    return curl_cffi.Session(impersonate="chrome")


def get_email(session) -> str:
    resp = session.get(f"{CONFIG['EMAIL_API_URL']}/api/generate-email", headers={"x-api-key": CONFIG["EMAIL_API_KEY"]}, timeout=30)
    resp.raise_for_status()
    return resp.json()["data"]["email"]


def get_link(session, email: str) -> Optional[str]:
    regex = r'https://[^\s"`]+'
    for i in range(20):
        resp = session.get(f"{CONFIG['EMAIL_API_URL']}/api/emails?email={email}", headers={"x-api-key": CONFIG["EMAIL_API_KEY"]}, timeout=30)
        resp.raise_for_status()
        data = resp.json()
        for email_data in data["data"]["emails"]:
            if "emergent.sh" in email_data.get("from_address", "") and "Confirm" in email_data.get("subject", ""):
                m = re.search(regex, email_data.get("html_content", ""))
                if m:
                    return m.group(0)
        time.sleep(2)
    return None


def register(session) -> str:
    session.get(f"{CONFIG['APP_URL']}/landing/", timeout=30)
    email = get_email(session)
    logger.info(f"Generated email: {email}")
    
    auth_headers = {
        "Apikey": CONFIG["API_KEY"],
        "Authorization": f"Bearer {CONFIG['API_KEY']}",
        "Origin": CONFIG["APP_URL"],
        "Referer": f"{CONFIG['APP_URL']}/"
    }
    
    sign_up_body = {
        "email": email,
        "password": email,
        "data": {"name": "User"},
        "gotrue_meta_security": {},
        "code_challenge": None,
        "code_challenge_method": None
    }
    
    resp = session.post(f"{CONFIG['BASE_AUTH_URL']}/auth/v1/signup", json=sign_up_body, headers=auth_headers, timeout=30)
    resp.raise_for_status()
    logger.info(f"Sign up success: {resp.status_code}")
    
    link = get_link(session, email)
    if not link:
        raise RuntimeError("Failed to get confirmation link")
    logger.info(f"Got confirmation link")
    
    session.get(link, timeout=30)
    
    token_body = {"email": email, "password": email, "gotrue_meta_security": {}}
    resp = session.post(f"{CONFIG['BASE_AUTH_URL']}/auth/v1/token?grant_type=password", json=token_body, headers=auth_headers, timeout=30)
    resp.raise_for_status()
    jwt = resp.json()["access_token"]
    
    # 初始化账户
    init_account(session, jwt)
    
    return jwt


def init_account(session, jwt: str):
    try:
        session.post(
            f"{CONFIG['BASE_API_URL']}/user/details",
            data=json.dumps({"ads_metadata": {"app_version": "1.1.28", "showError": ""}}),
            headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt}"},
            timeout=30
        )
        logger.info("User details initialized")
        
        try:
            session.get(f"{CONFIG['BASE_API_URL']}/credits/balance", headers={"Authorization": f"Bearer {jwt}"}, timeout=30)
        except:
            pass
    except Exception as e:
        logger.warning(f"Init warning: {e}")


def create_conv(session, jwt: str, prompt: str, model: str) -> str:
    ref_id = str(uuid.uuid4())
    body = {
        "client_ref_id": ref_id,
        "payload": {
            "processor_type": "env_only",
            "is_cloud": True,
            "env_image": "us-central1-docker.pkg.dev/emergent-default/emergent-container-hub/fastapi_react_mongo_shadcn_base_image_cloud_arm:release-26092025-2",
            "branch": "", "repository": "", "enable_visual_edit": True,
            "prompt_name": "auto_prompt_selector", "prompt_version": "latest",
            "work_space_dir": "", "task": prompt, "model_name": model,
            "model_manually_selected": True, "per_instance_cost_limit": 25,
            "agentic_skills": [], "plugin_version": "release-10092025-1",
            "base64_image_list": [], "human_timestamp": int(time.time() * 1000),
            "asset_upload_enabled": True, "is_pro_user": False, "testMode": False,
            "thinking_level": "thinking", "job_mode": "public", "mcp_id": []
        },
        "model_name": model, "resume": False,
        "ads_metadata": {"app_version": "1.1.28"}
    }
    
    resp = session.post(
        f"{CONFIG['BASE_API_URL']}/jobs/v0/submit-queue/",
        headers={"Authorization": f"Bearer {jwt}", "Origin": CONFIG["APP_URL"], "Referer": f"{CONFIG['APP_URL']}/", "Content-Type": "application/json"},
        json=body, timeout=30
    )
    resp.raise_for_status()
    return ref_id


def fetch_messages(session, jwt: str, conv_id: str) -> List[Dict]:
    messages = []
    try:
        resp = session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{conv_id}/history?limit=50",
            headers={"Authorization": f"Bearer {jwt}"},
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


def is_complete(session, jwt: str, conv_id: str) -> bool:
    try:
        resp = session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{conv_id}/history?limit=50",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("latest_request_id") is not None
    except:
        return False


@asynccontextmanager
async def lifespan(app: FastAPI):
    try:
        logger.info("Initializing...")
        app_state["session"] = create_session()
        
        jwt = os.environ.get("EMERGENT_JWT")
        if not jwt and os.path.exists("jwt.txt"):
            with open("jwt.txt") as f:
                jwt = f.read().strip()
        
        if jwt:
            logger.info("Using existing JWT")
            app_state["jwt"] = jwt
            init_account(app_state["session"], jwt)
        else:
            logger.info("Registering new account...")
            app_state["jwt"] = register(app_state["session"])
            with open("jwt.txt", "w") as f:
                f.write(app_state["jwt"])
            logger.info("JWT saved")
        
        app_state["initialized"] = True
        logger.info("✅ Ready!")
    except Exception as e:
        logger.error(f"❌ Init failed: {e}")
        raise
    yield


app = FastAPI(title="Emergent.sh OpenAI API", lifespan=lifespan)
SUPPORTED_MODELS = ["claude-opus-4-6", "claude-sonnet-4-5", "gpt-4o", "gpt-4o-mini"]


@app.get("/v1/models")
async def list_models():
    return ModelsResponse(data=[ModelInfo(id=m) for m in SUPPORTED_MODELS])


@app.post("/v1/chat/completions")
async def chat(request: ChatCompletionRequest):
    if not app_state["initialized"]:
        raise HTTPException(status_code=503, detail="Not initialized")
    
    prompt = "\n\n".join([f"{m.role}: {m.content}" for m in request.messages])
    
    try:
        conv_id = create_conv(app_state["session"], app_state["jwt"], prompt, request.model)
        logger.info(f"Created conversation: {conv_id}")
    except Exception as e:
        logger.error(f"Failed to create conversation: {e}")
        raise HTTPException(status_code=500, detail=str(e))
    
    if request.stream:
        return StreamingResponse(stream_resp(conv_id, request.model, prompt), media_type="text/event-stream")
    return await non_stream_resp(conv_id, request.model, prompt)


async def non_stream_resp(conv_id: str, model: str, prompt: str):
    texts = []
    for i in range(300):
        msgs = fetch_messages(app_state["session"], app_state["jwt"], conv_id)
        for m in msgs:
            if m["type"] == "text":
                texts.append(m["content"])
                logger.info(f"Got text: {m['content'][:50]}...")
        
        if is_complete(app_state["session"], app_state["jwt"], conv_id):
            logger.info(f"Conversation complete after {i} iterations")
            break
        await asyncio.sleep(0.5)
    
    response = "".join(texts) if texts else "No response"
    logger.info(f"Final response length: {len(response)}")
    
    return {
        "id": f"chatcmpl-{uuid.uuid4().hex}",
        "object": "chat.completion",
        "created": int(time.time()),
        "model": model,
        "choices": [{"index": 0, "message": {"role": "assistant", "content": response}, "finish_reason": "stop"}],
        "usage": {"prompt_tokens": len(prompt)//4, "completion_tokens": len(response)//4, "total_tokens": (len(prompt)+len(response))//4}
    }


async def stream_resp(conv_id: str, model: str, prompt: str) -> AsyncGenerator[str, None]:
    id = f"chatcmpl-{uuid.uuid4().hex}"
    created = int(time.time())
    yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}]})}\n\n'
    
    seen = set()
    for _ in range(300):
        msgs = fetch_messages(app_state["session"], app_state["jwt"], conv_id)
        for m in msgs:
            key = f"{m['type']}:{m['content']}"
            if key not in seen and m["type"] == "text":
                seen.add(key)
                yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {"content": m["content"]}, "finish_reason": None}]})}\n\n'
        if is_complete(app_state["session"], app_state["jwt"], conv_id):
            break
        await asyncio.sleep(0.5)
    
    yield f'data: {json.dumps({"id": id, "object": "chat.completion.chunk", "created": created, "model": model, "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}]})}\n\n'
    yield "data: [DONE]\n\n"


@app.get("/health")
async def health():
    return {"status": "healthy" if app_state["initialized"] else "initializing"}


@app.get("/")
async def root():
    return {"message": "Emergent.sh OpenAI API", "docs": "/docs"}


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="0.0.0.0", port=8000)
