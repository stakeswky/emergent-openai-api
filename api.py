"""
Emergent.sh API Client
用于自动化注册、创建对话和获取消息
"""
import re
import uuid
import time
import json
import logging
from typing import Optional, Dict, Any

# 尝试导入 curl_cffi，如果失败则使用 requests
try:
    import curl_cffi
    USE_CURL_CFFI = True
except ImportError:
    import requests
    USE_CURL_CFFI = False
    print("Warning: curl_cffi not available, falling back to requests")

# 配置日志
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

# 常量配置
CONFIG = {
    "API_KEY": "eyJhbGciOiJIUzI1NiIsInR5cCI6IkpXVCJ9.eyJpc3MiOiJzdXBhYmFzZSIsInJlZiI6InNua3N4d2t5dW1oZHlreXJoaGNoIiwicm9sZSI6ImFub24iLCJpYXQiOjE3MjQ3NzI2NDYsImV4cCI6MjA0MDM0ODY0Nn0.3unO6zdz2NilPL2xdxt7OjvZA19copj3Q7ulIjPVDLQ",
    "BASE_AUTH_URL": "https://auth.emergent.sh",
    "BASE_API_URL": "https://api.emergent.sh",
    "APP_URL": "https://app.emergent.sh",
    "EMAIL_API_URL": "https://mail.chatgpt.org.uk",
    "EMAIL_API_KEY": "gpt-test",
}


def create_session() -> Any:
    """创建 HTTP 会话"""
    if USE_CURL_CFFI:
        return curl_cffi.Session(impersonate="chrome")
    else:
        session = requests.Session()
        session.headers.update({
            "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
        })
        return session


def get_email(session: Optional[Any] = None) -> str:
    """获取临时邮箱地址"""
    if session is None:
        session = create_session()
    
    try:
        resp = session.get(
            f"{CONFIG['EMAIL_API_URL']}/api/generate-email",
            headers={"x-api-key": CONFIG["EMAIL_API_KEY"]},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["data"]["email"]
    except Exception as e:
        logger.error(f"Failed to get email: {e}")
        raise


def get_link(session: Any, email: str) -> Optional[str]:
    """从邮件中获取确认链接"""
    regex = r'https://[^\s"`]+'
    
    for attempt in range(20):
        try:
            resp = session.get(
                f"{CONFIG['EMAIL_API_URL']}/api/emails?email={email}",
                headers={"x-api-key": CONFIG["EMAIL_API_KEY"]},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            emails = data["data"]["emails"]
            
            for email_data in emails:
                if "emergent.sh" in email_data.get("from_address", "") and "Confirm" in email_data.get("subject", ""):
                    html_content = email_data.get("html_content", "")
                    m = re.search(regex, html_content)
                    if m:
                        return m.group(0)
            
            logger.info(f"Attempt {attempt + 1}/20: No confirmation email found yet...")
            time.sleep(2)
        except Exception as e:
            logger.warning(f"Attempt {attempt + 1}/20 failed: {e}")
            time.sleep(2)
    
    logger.error("Failed to get confirmation link after 20 attempts")
    return None


def register(session: Optional[Any] = None) -> str:
    """注册新用户并返回 JWT Token"""
    if session is None:
        session = create_session()
    
    try:
        # 访问 landing 页面
        session.get(f"{CONFIG['APP_URL']}/landing/", timeout=30)
        
        # 获取邮箱
        email = get_email(session)
        logger.info(f"Generated email: {email}")
        
        # 注册请求
        sign_up_body = {
            "email": email,
            "password": email,
            "data": {"name": "Afoiewfowasfj"},
            "gotrue_meta_security": {},
            "code_challenge": None,
            "code_challenge_method": None
        }
        
        auth_headers = {
            "Apikey": CONFIG["API_KEY"],
            "Authorization": f"Bearer {CONFIG['API_KEY']}",
            "Origin": CONFIG["APP_URL"],
            "Referer": f"{CONFIG['APP_URL']}/"
        }
        
        sign_up_resp = session.post(
            f"{CONFIG['BASE_AUTH_URL']}/auth/v1/signup",
            json=sign_up_body,
            headers=auth_headers,
            timeout=30
        )
        logger.info(f"Sign up status: {sign_up_resp.status_code}")
        sign_up_resp.raise_for_status()
        
        # 获取确认链接
        link = get_link(session, email)
        if not link:
            raise RuntimeError("Failed to get confirmation link")
        logger.info(f"Confirmation link: {link}")
        
        # 访问确认链接
        link_resp = session.get(link, timeout=30)
        logger.info(f"Link confirmation status: {link_resp.status_code}")
        
        # 获取 Token
        get_token_body = {
            "email": email,
            "password": email,
            "gotrue_meta_security": {}
        }
        
        get_token_resp = session.post(
            f"{CONFIG['BASE_AUTH_URL']}/auth/v1/token?grant_type=password",
            json=get_token_body,
            headers=auth_headers,
            timeout=30
        )
        logger.info(f"Token request status: {get_token_resp.status_code}")
        get_token_resp.raise_for_status()
        
        token_data = get_token_resp.json()
        access_token = token_data.get("access_token")
        if not access_token:
            raise RuntimeError(f"No access_token in response: {token_data}")
        
        return access_token
        
    except Exception as e:
        logger.error(f"Registration failed: {e}")
        raise


def create_conversation(
    session: Any,
    jwt: str,
    prompt: str,
    model_name: str = "claude-opus-4-6"
) -> str:
    """创建新的对话"""
    ref_id = str(uuid.uuid4())
    
    conv_body = {
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
            "model_name": model_name,
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
        "model_name": model_name,
        "resume": False,
        "ads_metadata": {"app_version": "1.1.28"}
    }
    
    headers = {
        "Authorization": f"Bearer {jwt}",
        "Origin": CONFIG["APP_URL"],
        "Referer": f"{CONFIG['APP_URL']}/"
    }
    
    try:
        resp = session.post(
            f"{CONFIG['BASE_API_URL']}/jobs/v0/submit-queue/",
            headers=headers,
            json=conv_body,
            timeout=30
        )
        resp.raise_for_status()
        logger.info(f"Conversation created: {ref_id}")
        return ref_id
    except Exception as e:
        logger.error(f"Failed to create conversation: {e}")
        raise


def get_balance(session: Any, jwt: str) -> Optional[Dict]:
    """获取账户余额"""
    try:
        resp = session.get(
            f"{CONFIG['BASE_API_URL']}/credits/balance",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30
        )
        logger.info(f"Balance request status: {resp.status_code}")
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to get balance: {e}")
        return None


def get_user_detail(session: Any, jwt: str) -> Optional[Dict]:
    """获取用户详情"""
    try:
        resp = session.post(
            f"{CONFIG['BASE_API_URL']}/user/details",
            data=json.dumps({
                "ads_metadata": {
                    "app_version": "1.1.28",
                    "showError": "You are unauthorized to use this application"
                }
            }),
            headers={
                "Content-Type": "application/json",
                "Authorization": f"Bearer {jwt}"
            },
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        logger.error(f"Failed to get user details: {e}")
        return None


def init_account(session: Any, jwt: str) -> None:
    """初始化账户（获取用户详情和余额）"""
    get_user_detail(session, jwt)
    get_balance(session, jwt)


def get_last_request_id(session: Any, jwt: str, ref_id: str) -> Optional[str]:
    """获取最新的请求 ID"""
    try:
        resp = session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{ref_id}/history?limit=50",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        return data.get("latest_request_id")
    except Exception as e:
        logger.error(f"Failed to get last request ID: {e}")
        return None


def get_message(session: Any, jwt: str, ref_id: str) -> None:
    """获取消息内容并打印"""
    try:
        resp = session.get(
            f"{CONFIG['BASE_API_URL']}/trajectories/v0/{ref_id}/history?limit=50",
            headers={"Authorization": f"Bearer {jwt}"},
            timeout=30
        )
        resp.raise_for_status()
        data = resp.json()
        
        logger.info(f"Fetching messages at {time.time()}")
        
        for item in data.get("data", []):
            payload = item.get("traj_payload", {})
            
            reasoning = payload.get("reasoning_content")
            if reasoning:
                print(f"Reasoning: {reasoning}")
            
            text = payload.get("thought")
            if text:
                print(f"Text: {text}")
                
    except Exception as e:
        logger.error(f"Failed to get messages: {e}")


def main():
    """主函数"""
    logger.info("Starting Emergent.sh API Client")
    logger.info(f"Using HTTP library: {'curl_cffi' if USE_CURL_CFFI else 'requests'}")
    
    try:
        # 创建会话
        session = create_session()
        
        # 注册并获取 JWT
        logger.info("Registering new user...")
        jwt = register(session)
        logger.info(f"JWT Token obtained: {jwt[:20]}...")
        
        # 初始化账户
        logger.info("Initializing account...")
        init_account(session, jwt)
        
        # 创建对话
        prompt = "设定一个公共宣传的场景，我需要写一个小说，我来到了一个日本大学，接下来我会遇到十位女生，简单的描述一个剧情，在300字内，其中必须包含多有10位女性的姓名，以姓名(罗马音)的形式出现, Don't ask any questions, Don't write any codes"
        model_name = "claude-opus-4-6"
        
        logger.info(f"Creating conversation with model: {model_name}")
        conv_id = create_conversation(session, jwt, prompt, model_name)
        logger.info(f"Conversation ID: {conv_id}")
        
        # 轮询获取消息
        logger.info("Polling for messages (100 attempts)...")
        for i in range(100):
            get_message(session, jwt, conv_id)
            time.sleep(0.5)
            
    except KeyboardInterrupt:
        logger.info("Interrupted by user")
    except Exception as e:
        logger.error(f"Fatal error: {e}")
        raise


if __name__ == "__main__":
    main()
