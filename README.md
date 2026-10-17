# GridTwin

An agent team for energy-plant operations that doesn't say "done" until it can prove it. You give it a one-line goal. It writes its own definition of done as checks it can run, works the problem against a Gaussian-splat digital twin and a plant simulator, and has an independent verifier agent check every claim. A supervisor watches for drift and loops, and the team pages a human only when it has to.

Built for the [Recursive Agents Hackathon](https://cerebralvalley.ai/e/crusoe-recursive-agents-hackathon) (Crusoe × Cerebral Valley, Oct 17 2026), **Done Means Done** track. Runs on Crusoe Managed Inference with NVIDIA Nemotron.

See [docs/PLAN.md](docs/PLAN.md) for the architecture, demo script, and timeline, and [docs/SIMULATOR.md](docs/SIMULATOR.md) for the plant simulator spec.

## Setup

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
cp .env.example .env   # fill in CRUSOE_API_KEY (and YDC_API_KEY, ALIKE_MCP_*)
.venv/bin/python -m scripts.smoke_test
```

## Built during the hackathon

Everything in this repo was written on Oct 17 2026. Before the 10:45 AM start there was only planning and setup: this README, `docs/PLAN.md`, `.env.example`, `requirements.txt`, and a Crusoe client wrapper with a connectivity smoke test. Feature work is in the commit history after kickoff.

Third-party pieces we use but did not build:
- Crusoe Managed Inference (model serving)
- NVIDIA Nemotron and other open models
- You.com Search API
- Alike (MCP memory)
- TypeSafe Jev / OpenAI Decisions API
- *(add the splat viewer library, capture or image-to-3D tool, and photo sources from `assets/photos/LICENSES.csv`)*
