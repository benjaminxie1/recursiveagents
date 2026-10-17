# Notes for coding agents

- Plan and architecture: `docs/PLAN.md`. Model roles: `gridtwin/config.py`. All LLM calls go through `gridtwin/llm.py` (retries on 429/503).
- Crusoe endpoint: `https://api.inference.crusoecloud.com/v1` (OpenAI-compatible, chat completions only). Model IDs are case-sensitive; check them with `GET /v1/models`.
- Full Crusoe reference: https://github.com/crusoecloud/crusoe-developer-hub/blob/main/examples/hackathons/crusoe-cv-recursive-agents-hackathon/crusoe-cv-recursive-agents-hackathon-skill.md
- Gotchas:
  - Reasoning models use up `max_tokens` while thinking, so keep it at 1024 or more. `finish_reason == "length"` with empty content means the model ran out of tokens.
  - Toggle thinking with `extra_body={"chat_template_kwargs": {"enable_thinking": ...}}` (Nemotron). The reasoning text comes back in `reasoning_content` or `reasoning`.
  - Rate limits are per model, at 30 RPM without a card on file and 600 RPM with one. Spread roles across models.
  - Keep system prompts and tool schemas byte-identical across turns to get prefix-cache hits.
  - The Crusoe key can contain `$`, so single-quote it in a shell.
- Secrets live only in `.env` (gitignored). Never commit keys.
