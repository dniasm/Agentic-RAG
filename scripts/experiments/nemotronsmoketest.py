"""One-question smoke test of Nemotron Ultra through OpenRouter (not used by the app).

    pip install langchain-openai
    OPEN_ROUTER_API_KEY=... python scripts/experiments/nemotronsmoketest.py
"""
import os

from dotenv import load_dotenv
from langchain_openai import ChatOpenAI

load_dotenv()
llm = ChatOpenAI(
    model=os.environ.get("OPEN_ROUTER_MODEL", "nvidia/nemotron-3-ultra-550b-a55b:free"),
    base_url="https://openrouter.ai/api/v1",
    api_key=os.environ["OPEN_ROUTER_API_KEY"],
    temperature=0,
    max_retries=2,
)
print(llm.invoke("How many r's are in strawberry?").content)
