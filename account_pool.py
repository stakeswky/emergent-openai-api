"""
Emergent.sh 账号池管理器
支持批量注册和轮询使用
"""
import json
import time
import uuid
import os
from typing import List, Dict, Optional, Any
from dataclasses import dataclass, field
from pathlib import Path
import threading

try:
    import curl_cffi
    USE_CURL_CFFI = True
except ImportError:
    USE_CURL_CFFI = False
    print("Error: curl_cffi required")
    exit(1)

import logging

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


@dataclass
class Account:
    """账号信息"""
    jwt: str
    email: str
    created_at: float = field(default_factory=time.time)
    last_used: float = 0
    total_requests: int = 0
    is_active: bool = True
    session: Any = None
    
    def mark_used(self):
        self.last_used = time.time()
        self.total_requests += 1


class AccountPool:
    """账号池管理器"""
    
    def __init__(self, accounts_file: str = "accounts.json", max_accounts: int = 10):
        self.accounts_file = Path(accounts_file)
        self.max_accounts = max_accounts
        self.accounts: List[Account] = []
        self.current_index = 0
        self.lock = threading.Lock()
        
        # 加载已有账号
        self.load_accounts()
    
    def create_session(self) -> curl_cffi.Session:
        """创建 curl_cffi 会话"""
        return curl_cffi.Session(impersonate="chrome")
    
    def get_email(self, session: curl_cffi.Session) -> str:
        """获取临时邮箱"""
        resp = session.get(
            f"{CONFIG['EMAIL_API_URL']}/api/generate-email",
            headers={"x-api-key": CONFIG["EMAIL_API_KEY"]},
            timeout=30
        )
        resp.raise_for_status()
        return resp.json()["data"]["email"]
    
    def get_link(self, session: curl_cffi.Session, email: str) -> Optional[str]:
        """从邮件获取确认链接"""
        import re
        regex = r'https://[^\s"`]+'
        for i in range(20):
            resp = session.get(
                f"{CONFIG['EMAIL_API_URL']}/api/emails?email={email}",
                headers={"x-api-key": CONFIG["EMAIL_API_KEY"]},
                timeout=30
            )
            resp.raise_for_status()
            data = resp.json()
            for email_data in data["data"]["emails"]:
                if "emergent.sh" in email_data.get("from_address", "") and "Confirm" in email_data.get("subject", ""):
                    m = re.search(regex, email_data.get("html_content", ""))
                    if m:
                        return m.group(0)
            time.sleep(2)
        return None
    
    def register_account(self) -> Optional[Account]:
        """注册单个账号"""
        try:
            logger.info("Starting account registration...")
            session = self.create_session()
            
            # 访问 landing
            session.get(f"{CONFIG['APP_URL']}/landing/", timeout=30)
            
            # 获取邮箱
            email = self.get_email(session)
            logger.info(f"Got email: {email}")
            
            auth_headers = {
                "Apikey": CONFIG["API_KEY"],
                "Authorization": f"Bearer {CONFIG['API_KEY']}",
                "Origin": CONFIG["APP_URL"],
                "Referer": f"{CONFIG['APP_URL']}/"
            }
            
            # 注册
            sign_up_body = {
                "email": email,
                "password": email,
                "data": {"name": "User"},
                "gotrue_meta_security": {},
                "code_challenge": None,
                "code_challenge_method": None
            }
            
            resp = session.post(
                f"{CONFIG['BASE_AUTH_URL']}/auth/v1/signup",
                json=sign_up_body,
                headers=auth_headers,
                timeout=30
            )
            resp.raise_for_status()
            logger.info("Sign up successful")
            
            # 获取确认链接
            link = self.get_link(session, email)
            if not link:
                logger.error("Failed to get confirmation link")
                return None
            logger.info("Got confirmation link")
            
            # 确认
            session.get(link, timeout=30)
            
            # 获取 token
            token_body = {
                "email": email,
                "password": email,
                "gotrue_meta_security": {}
            }
            
            resp = session.post(
                f"{CONFIG['BASE_AUTH_URL']}/auth/v1/token?grant_type=password",
                json=token_body,
                headers=auth_headers,
                timeout=30
            )
            resp.raise_for_status()
            jwt = resp.json()["access_token"]
            logger.info(f"Got JWT: {jwt[:30]}...")
            
            # 初始化账户
            self._init_account(session, jwt)
            
            account = Account(jwt=jwt, email=email, session=session)
            logger.info(f"✅ Account registered: {email}")
            return account
            
        except Exception as e:
            logger.error(f"❌ Registration failed: {e}")
            return None
    
    def _init_account(self, session: curl_cffi.Session, jwt: str):
        """初始化账户"""
        try:
            session.post(
                f"{CONFIG['BASE_API_URL']}/user/details",
                data=json.dumps({"ads_metadata": {"app_version": "1.1.28", "showError": ""}}),
                headers={"Content-Type": "application/json", "Authorization": f"Bearer {jwt}"},
                timeout=30
            )
            session.get(
                f"{CONFIG['BASE_API_URL']}/credits/balance",
                headers={"Authorization": f"Bearer {jwt}"},
                timeout=30
            )
            logger.info("Account initialized")
        except Exception as e:
            logger.warning(f"Init warning: {e}")
    
    def batch_register(self, count: int = 5) -> List[Account]:
        """批量注册账号"""
        logger.info(f"Starting batch registration of {count} accounts...")
        new_accounts = []
        
        for i in range(count):
            logger.info(f"Registering account {i+1}/{count}...")
            account = self.register_account()
            if account:
                with self.lock:
                    self.accounts.append(account)
                new_accounts.append(account)
                self.save_accounts()
            time.sleep(2)  # 避免请求过快
        
        logger.info(f"✅ Registered {len(new_accounts)}/{count} accounts")
        return new_accounts
    
    def get_next_account(self) -> Optional[Account]:
        """轮询获取下一个可用账号"""
        with self.lock:
            if not self.accounts:
                return None
            
            # 轮询策略
            attempts = 0
            while attempts < len(self.accounts):
                account = self.accounts[self.current_index]
                self.current_index = (self.current_index + 1) % len(self.accounts)
                
                if account.is_active:
                    account.mark_used()
                    return account
                
                attempts += 1
            
            # 所有账号都不可用
            return None
    
    def get_account_count(self) -> int:
        """获取账号数量"""
        with self.lock:
            return len(self.accounts)
    
    def get_active_account_count(self) -> int:
        """获取活跃账号数量"""
        with self.lock:
            return sum(1 for acc in self.accounts if acc.is_active)
    
    def deactivate_account(self, jwt: str):
        """停用账号（如JWT过期）"""
        with self.lock:
            for acc in self.accounts:
                if acc.jwt == jwt:
                    acc.is_active = False
                    logger.warning(f"Account deactivated: {acc.email}")
                    break
        self.save_accounts()
    
    def save_accounts(self):
        """保存账号到文件"""
        data = []
        with self.lock:
            for acc in self.accounts:
                data.append({
                    "jwt": acc.jwt,
                    "email": acc.email,
                    "created_at": acc.created_at,
                    "total_requests": acc.total_requests,
                    "is_active": acc.is_active
                })
        
        with open(self.accounts_file, 'w') as f:
            json.dump(data, f, indent=2)
        logger.info(f"Saved {len(data)} accounts to {self.accounts_file}")
    
    def load_accounts(self):
        """从文件加载账号"""
        if not self.accounts_file.exists():
            logger.info("No existing accounts file")
            return
        
        try:
            with open(self.accounts_file, 'r') as f:
                data = json.load(f)
            
            for item in data:
                account = Account(
                    jwt=item["jwt"],
                    email=item["email"],
                    created_at=item.get("created_at", time.time()),
                    total_requests=item.get("total_requests", 0),
                    is_active=item.get("is_active", True)
                )
                # 为每个账号创建新session
                account.session = self.create_session()
                self.accounts.append(account)
            
            logger.info(f"✅ Loaded {len(self.accounts)} accounts from {self.accounts_file}")
        except Exception as e:
            logger.error(f"Failed to load accounts: {e}")
    
    def get_stats(self) -> Dict:
        """获取统计信息"""
        with self.lock:
            total = len(self.accounts)
            active = sum(1 for acc in self.accounts if acc.is_active)
            total_requests = sum(acc.total_requests for acc in self.accounts)
            
        return {
            "total_accounts": total,
            "active_accounts": active,
            "total_requests": total_requests
        }


# 全局账号池
_pool: Optional[AccountPool] = None


def get_account_pool(accounts_file: str = "accounts.json", max_accounts: int = 10) -> AccountPool:
    """获取全局账号池实例"""
    global _pool
    if _pool is None:
        _pool = AccountPool(accounts_file=accounts_file, max_accounts=max_accounts)
    return _pool


if __name__ == "__main__":
    # 测试批量注册
    pool = get_account_pool()
    
    # 如果账号不足，批量注册
    if pool.get_account_count() < 3:
        pool.batch_register(count=3)
    
    # 打印统计
    print("\nAccount Stats:")
    print(json.dumps(pool.get_stats(), indent=2))
    
    # 测试轮询
    print("\nTesting round-robin:")
    for i in range(5):
        acc = pool.get_next_account()
        if acc:
            print(f"  {i+1}. {acc.email} (requests: {acc.total_requests})")
