"""Model roles. One model per role spreads load, because Crusoe rate limits are per model.

Confirm every ID against GET /v1/models at kickoff; IDs are case-sensitive.
"""
import os

from dotenv import load_dotenv

load_dotenv()

BASE_URL = os.environ.get("CRUSOE_BASE_URL", "https://api.inference.crusoecloud.com/v1")

ROLES = {
    # Writes the definition of done and replans. Strongest Nemotron, built for agent teams.
    "planner": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
    # High-frequency sub-agents (inspection, diagnosis, operator actions).
    "worker": "nvidia/Nemotron-3.5-Lightning-30B-A3B",
    # Independent verifier: a different model family, so its errors don't line up with the workers'.
    "verifier": "openai/gpt-oss-120b",
    # Video, image, and audio perception of inspection footage and splat renders.
    "vision": "nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B",
    # Fast image captioning fallback (no reasoning phase).
    "vision_fast": "google/gemma-4-31b-it",
    # Local decision gate. A model no other role uses, so it has its own rate-limit bucket.
    "gate": "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B",
    # Supervisor drift judge, called once per round. Shares the planner's model.
    "supervisor": "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B",
}

# USD per 1M tokens: (input, output, cached input).
PRICES = {
    "nvidia/NVIDIA-Nemotron-3-Super-120B-A12B": (0.30, 2.40, 0.15),
    "nvidia/Nemotron-3.5-Lightning-30B-A3B": (0.05, 0.20, 0.03),
    "openai/gpt-oss-120b": (0.05, 0.20, 0.05),
    "nvidia/Nemotron-3-Nano-Omni-Reasoning-30B-A3B": (0.30, 1.83, 0.30),
    "google/gemma-4-31b-it": (0.14, 0.40, 0.14),
    "nvidia/NVIDIA-Nemotron-3-Nano-30B-A3B": (0.05, 0.20, 0.03),
}

# Requests per minute per model until the real limit is learned from the
# x-ratelimit-limit-requests header (30 without a card on file, 600 with one).
# Crusoe sends that header only on some responses (not on 200s as of kickoff), so
# CRUSOE_RPM overrides the default when a card is on file.
def _rpm(raw, default=30):
    try:
        n = int(str(raw).strip())
    except (TypeError, ValueError):
        return default
    return n if n > 0 else default


DEFAULT_RPM = _rpm(os.environ.get("CRUSOE_RPM"))
