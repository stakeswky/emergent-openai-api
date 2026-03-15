"""
批量账号注册器 - 支持并发快速注册
"""
import json
import time
import uuid
import re
import os
from concurrent.futures import ThreadPoolExecutor, as_completed
from typing import List, Dict, Optional
from dataclasses import dataclass
from pathlib import Path

try:
    import curl_cffi
    USE_CURL_CFFI = True
except ImportError:
    print("Error: curl_cffi required")
    exit(1)

import logging

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(threadName)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

os.environ['NO_PROXY'] = '*'

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
    jwt: str
    email: str
    created_at: float


def create_session():
    return curl_cffi.Session(impersonate="chrome")


def get_email(session) -> str:
    resp = session.get(
        f"{CONFIG['EMAIL_API_URL']}/api/generate-email",
        headers={"x-api-key": CONFIG["EMAIL_API_KEY"]},
        timeout=30
    )
    resp.raise_for_status()
    return resp.json()["data"]["email"]


def get_link(session, email: str) -> Optional[str]:
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


def init_account(session, jwt: str):
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
    except Exception as e:
        logger.warning(f"Init warning: {e}")


def register_single_account(account_id: int) -> Optional[Account]:
    """注册单个账号（用于线程池）"""
    try:
        logger.info(f"[{account_id}] Starting registration...")
        session = create_session()
        
        # 访问 landing
        session.get(f"{CONFIG['APP_URL']}/landing/", timeout=30)
        
        # 获取邮箱
        email = get_email(session)
        logger.info(f"[{account_id}] Email: {email}")
        
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
        
        # 获取确认链接
        link = get_link(session, email)
        if not link:
            logger.error(f"[{account_id}] Failed to get confirmation link")
            return None
        
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
        
        # 初始化账户
        init_account(session, jwt)
        
        logger.info(f"[{account_id}] ✅ Success: {email}")
        return Account(jwt=jwt, email=email, created_at=time.time())
        
    except Exception as e:
        logger.error(f"[{account_id}] ❌ Failed: {e}")
        return None


def batch_register_accounts(count: int = 10, max_workers: int = 3) -> List[Account]:
    """
    批量注册账号（并发版本）
    
    Args:
        count: 要注册的账号数
        max_workers: 并发线程数（建议 3-5，避免被封）
    """
    logger.info(f"Starting batch registration: {count} accounts with {max_workers} workers")
    
    accounts = []
    success_count = 0
    fail_count = 0
    
    with ThreadPoolExecutor(max_workers=max_workers, thread_name_prefix="Register") as executor:
        # 提交所有任务
        future_to_id = {
            executor.submit(register_single_account, i): i 
            for i in range(count)
        }
        
        # 处理完成的任务
        for future in as_completed(future_to_id):
            account_id = future_to_id[future]
            try:
                account = future.result()
                if account:
                    accounts.append(account)
                    success_count += 1
                else:
                    fail_count += 1
            except Exception as e:
                logger.error(f"[{account_id}] Exception: {e}")
                fail_count += 1
            
            # 每10个账号显示进度
            if (success_count + fail_count) % 10 == 0:
                logger.info(f"Progress: {success_count + fail_count}/{count} (Success: {success_count}, Fail: {fail_count})")
    
    logger.info(f"✅ Batch complete: {success_count} success, {fail_count} failed")
    return accounts


def save_accounts_to_file(accounts: List[Account], filepath: str = "accounts.json"):
    """保存账号到文件"""
    data = []
    for acc in accounts:
        data.append({
            "jwt": acc.jwt,
            "email": acc.email,
            "created_at": acc.created_at,
            "is_active": True,
            "total_requests": 0
        })
    
    # 如果文件存在，合并现有账号
    existing = []
    if os.path.exists(filepath):
        try:
            with open(filepath, 'r') as f:
                existing = json.load(f)
        except:
            pass
    
    all_accounts = existing + data
    
    with open(filepath, 'w') as f:
        json.dump(all_accounts, f, indent=2)
    
    logger.info(f"💾 Saved {len(data)} new accounts (Total: {len(all_accounts)}) to {filepath}")
    return len(all_accounts)


if __name__ == "__main__":
    import argparse
    
    parser = argparse.ArgumentParser(description='批量注册 Emergent.sh 账号')
    parser.add_argument('-n', '--number', type=int, default=10, help='要注册的账号数量')
    parser.add_argument('-w', '--workers', type=int, default=3, help='并发线程数（默认3）')
    parser.add_argument('-o', '--output', type=str, default='accounts.json', help='输出文件')
    args = parser.parse_args()
    
    print(f"🚀 开始注册 {args.number} 个账号（并发: {args.workers}）")
    print(f"⏱️  预计时间: {args.number * 30 // args.workers}-{args.number * 60 // args.workers} 秒")
    print("-" * 50)
    
    start_time = time.time()
    accounts = batch_register_accounts(count=args.number, max_workers=args.workers)
    elapsed = time.time() - start_time
    
    if accounts:
        total = save_accounts_to_file(accounts, args.output)
        print("-" * 50)
        print(f"✅ 完成！")
        print(f"📊 统计:")
        print(f"  - 成功注册: {len(accounts)}/{args.number}")
        print(f"  - 成功率: {len(accounts)/args.number*100:.1f}%")
        print(f"  - 总账号数: {total}")
        print(f"  - 耗时: {elapsed:.1f} 秒 ({elapsed/60:.1f} 分钟)")
        print(f"  - 平均速度: {len(accounts)/elapsed*60:.1f} 账号/分钟")
        print(f"💾 账号已保存到: {args.output}")
    else:
        print("❌ 没有成功注册任何账号")
