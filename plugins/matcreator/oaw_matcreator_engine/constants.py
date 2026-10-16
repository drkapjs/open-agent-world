"""Model configuration supplied by the OAW worker, never a user-global config."""
import os

LLM_MODEL = os.environ["LLM_MODEL"]
LLM_API_KEY = os.environ.get("LLM_API_KEY", "")
LLM_BASE_URL = os.environ.get("LLM_BASE_URL", "")
