#!/usr/bin/env python3
import requests
import time
import statistics

BASE_URL = "http://localhost:8000"
MODEL = "claude-opus-4-6"

prompts = [
    "What is Python?",
    "Explain machine learning in one sentence.",
    "What is the capital of Japan?",
    "Write a hello world in Python.",
    "What is the difference between list and tuple?",
    "Explain REST API.",
    "What is Docker used for?",
    "How does HTTP work?",
    "What is git?",
    "Explain the concept of recursion."
]

times = []
success_count = 0

print("🚀 Testing opus-4.6 model - 10 Q&A rounds\n")
print("="*80)

for i, prompt in enumerate(prompts, 1):
    print(f"\nRound {i}/10: {prompt[:40]}...")
    
    payload = {
        "model": MODEL,
        "messages": [{"role": "user", "content": prompt}],
        "stream": False
    }
    
    start = time.time()
    try:
        resp = requests.post(f"{BASE_URL}/v1/chat/completions", json=payload, timeout=120)
        elapsed = time.time() - start
        
        if resp.status_code == 200:
            data = resp.json()
            content = data["choices"][0]["message"]["content"]
            tokens = len(content) / 4
            tok_per_sec = tokens / elapsed if elapsed > 0 else 0
            
            times.append(elapsed)
            success_count += 1
            print(f"  ✅ {elapsed:.2f}s | {tok_per_sec:.1f} tok/s | {len(content)} chars")
        else:
            print(f"  ❌ HTTP {resp.status_code}: {resp.text[:100]}")
    except Exception as e:
        print(f"  ❌ Error: {str(e)[:100]}")
    
    time.sleep(0.5)

print("\n" + "="*80)
print("\n📊 RESULTS SUMMARY:")
print(f"  Model: {MODEL}")
print(f"  Success Rate: {success_count}/10 ({success_count*10}%)")

if times:
    print(f"  Average Response Time: {statistics.mean(times):.2f}s")
    print(f"  Min/Max Time: {min(times):.2f}s / {max(times):.2f}s")
    print(f"  Median Time: {statistics.median(times):.2f}s")
    
    avg_tok_per_sec = statistics.mean([len(p)/4 / t for p, t in zip(prompts[:len(times)], times)])
    print(f"  Throughput: ~{avg_tok_per_sec:.1f} tokens/sec (avg)")

print("\n" + "="*80)
