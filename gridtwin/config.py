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
}
