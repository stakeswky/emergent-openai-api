# Emergent.sh OpenAI-Compatible API

OpenAI-compatible API server for Emergent.sh AI models with automatic account registration, account pool management, and round-robin load balancing.

## Features

- ✅ **OpenAI-compatible API** - Drop-in replacement for OpenAI API (`/v1/chat/completions`)
- ✅ **Auto Account Registration** - Automatically register Emergent.sh accounts with temp emails
- ✅ **Account Pool** - Multiple accounts with round-robin rotation for load balancing
- ✅ **Streaming Support** - SSE streaming for real-time responses
- ✅ **Multiple Models** - Claude and GPT models via Emergent.sh:
  - `claude-opus-4-6` - Most powerful Claude model
  - `claude-sonnet-4-5` - Balanced Claude model
  - `gpt-4o` - GPT-4 Omni
  - `gpt-4o-mini` - Lightweight GPT-4

## Quick Start

### 1. Install Dependencies

```bash
pip install -r requirements.txt
```

### 2. Register Accounts

```bash
# Register 10 accounts (recommended: 2-3 workers to avoid rate limits)
python batch_register.py -n 10 -w 2

# Or let the server auto-register on first run
```

### 3. Start API Server

```bash
# Start with account pool (recommended)
python openai_server_pool.py

# Or single account mode
python openai_server.py

# Server starts on http://localhost:8000
```

### 4. Test the API

```bash
# Using curl
curl -X POST http://localhost:8000/v1/chat/completions \
  -H "Content-Type: application/json" \
  -H "Authorization: Bearer sk-test" \
  -d '{
    "model": "claude-opus-4-6",
    "messages": [{"role": "user", "content": "Hello!"}],
    "stream": false
  }'
```

## API Endpoints

| Endpoint | Method | Description |
|----------|--------|-------------|
| `/v1/chat/completions` | POST | Chat completions (OpenAI-compatible) |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check with pool stats |
| `/stats` | GET | Detailed account pool statistics |
| `/admin/register` | POST | Manual batch account registration |

## Files

| File | Description |
|------|-------------|
| `openai_server_pool.py` | **Main server** with account pool & load balancing |
| `account_pool.py` | Account pool management |
| `batch_register.py` | Concurrent batch registration tool |
| `api.py` | Core Emergent.sh API client |
| `accounts.json` | Stored account credentials (auto-generated) |

## Usage with OpenAI SDK

```python
from openai import OpenAI

client = OpenAI(
    base_url="http://localhost:8000/v1",
    api_key="sk-test"  # Not validated, any string works
)

# Non-streaming
response = client.chat.completions.create(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "Hello!"}]
)
print(response.choices[0].message.content)

# Streaming
stream = client.chat.completions.create(
    model="claude-opus-4-6",
    messages=[{"role": "user", "content": "Hello!"}],
    stream=True
)
for chunk in stream:
    if chunk.choices[0].delta.content:
        print(chunk.choices[0].delta.content, end="")
```

## Architecture

```
Client (OpenAI SDK)
       │
       ▼
┌─────────────────┐
│  OpenAI API     │  HTTP/REST
│  Server         │
│  (FastAPI)      │
└────────┬────────┘
         │
    Round-Robin
         │
┌────────┴────────┐
│  Account Pool   │
│  (3 accounts)   │
└────────┬────────┘
         │
    curl_cffi
         │
    ┌────┴────┬────────┐
    ▼         ▼        ▼
Account 1  Account 2  Account N
    │         │        │
    └─────────┴────────┘
              │
              ▼
       Emergent.sh API
```

## Performance Benchmark

See `BENCHMARK_RESULTS.md` for detailed testing.

| Metric | Result |
|--------|--------|
| Average Response Time | 32.31 seconds |
| Success Rate | 100% |
| Max Context Size | 8,000+ tokens |

**Note**: `claude-opus-4-6` is powerful but slow. For faster responses, use `gpt-4o`.

## Account Pool Configuration

Accounts are stored in `accounts.json`:

```json
{
  "accounts": [
    {
      "jwt": "eyJ...",
      "email": "temp@example.com",
      "created_at": 1234567890,
      "total_requests": 42,
      "is_active": true
    }
  ],
  "created_at": 1234567890,
  "last_updated": 1234567890
}
```

The pool automatically:
- Rotates accounts in round-robin fashion
- Tracks request counts per account
- Deactivates failed accounts

## Batch Registration

```bash
# Register 50 accounts with 3 concurrent workers
python batch_register.py -n 50 -w 3

# Help
python batch_register.py --help
```

**Tip**: Limit to 2-3 workers to avoid temporary email service rate limits.

## Environment Setup

The default configuration uses built-in API keys. To customize:

```python
# In account_pool.py or api.py
CONFIG = {
    "API_KEY": "your-emergent-api-key",
    "BASE_AUTH_URL": "https://auth.emergent.sh",
    "BASE_API_URL": "https://api.emergent.sh",
    "EMAIL_API_URL": "https://mail.chatgpt.org.uk",
    "EMAIL_API_KEY": "gpt-test",
}
```

## Troubleshooting

### SSL Certificate Errors
The code automatically falls back to `requests` library if `curl_cffi` fails.

### JWT Expiration
Tokens expire after a period. Re-register accounts:
```bash
# Delete old accounts and register new ones
rm accounts.json
python batch_register.py -n 10
```

### Rate Limiting
If registration fails, reduce worker count:
```bash
python batch_register.py -n 10 -w 1  # Single worker
```

## Disclaimer

This is an **unofficial** wrapper for Emergent.sh's API. Please respect their terms of service. Use at your own risk.

## License

MIT License
