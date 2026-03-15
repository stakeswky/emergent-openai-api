# Emergent.sh OpenAI-Compatible API (Go)

OpenAI-compatible and Anthropic-compatible API proxy for Emergent.sh, written in pure Go with zero dependencies. Features automatic account registration, pool management, and round-robin load balancing.

## Features

- `/v1/messages` — Anthropic Messages API (streaming + non-streaming)
- `/v1/chat/completions` — OpenAI Chat Completions API (streaming + non-streaming)
- `/v1/models` — Model listing
- `/health`, `/stats` — Monitoring endpoints
- `/admin/register` — Manual account registration trigger
- Auto account registration & replenishment
- Account pool with round-robin rotation
- Tool use routing to external API
- Extended thinking/reasoning support in streams

## Quick Start

```bash
# Copy and fill in your config
cp setting.json.example setting.json

# Build
go build -o hybrid_proxy main.go

# Run
./hybrid_proxy
# Server starts on :8001
```

## Configuration

Edit `setting.json`:

```jsonc
{
  "listen_addr": ":8001",
  "accounts_file": "accounts.json",
  "poll_interval_seconds": 2,
  "max_poll_attempts": 60,
  "emergent": {
    "api_url": "https://api.emergent.sh",
    "app_url": "https://app.emergent.sh"
  },
  "tool_use": {
    "base_url": "https://your-tool-api/v1",
    "api_key": "your-key",
    "model": "gpt-4o"
  },
  "registration": {
    "email_api_url": "https://your-email-api",
    "email_api_key": "your-key",
    "supabase_api_key": "your-supabase-anon-key",
    "base_auth_url": "https://auth.emergent.sh",
    "min_accounts": 3,
    "max_accounts": 3
  }
}
```

## Usage with Claude Code

```json
{
  "env": {
    "ANTHROPIC_AUTH_TOKEN": "sk-test",
    "ANTHROPIC_BASE_URL": "http://localhost:8001"
  }
}
```

## API Endpoints

| Endpoint | Method | Description |
|---|---|---|
| `/v1/messages` | POST | Anthropic Messages API |
| `/v1/chat/completions` | POST | OpenAI Chat Completions API |
| `/v1/models` | GET | List available models |
| `/health` | GET | Health check |
| `/stats` | GET | Account pool statistics |
| `/admin/register` | POST | Trigger account registration (`{"count": 3}`) |

## How It Works

- Requests **without tools** → routed to Emergent.sh via account pool
- Requests **with tools** → routed to external OpenAI-compatible API
- When active accounts drop below `min_accounts`, auto-replenish kicks in
- If no accounts available at request time, on-demand registration is attempted

## License

MIT
