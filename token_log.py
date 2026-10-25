from langchain_core.callbacks import BaseCallbackHandler
from backend.graph.workflow import stream_agent

total_in = total_out = calls = 0


class TokenLog(BaseCallbackHandler):
    def on_llm_end(self, response, **kwargs):
        global total_in, total_out, calls

        for gens in response.generations:
            for g in gens:
                u = getattr(getattr(g, "message", None), "usage_metadata", None)

                if u:
                    calls += 1
                    total_in += u["input_tokens"]
                    total_out += u["output_tokens"]

                    print(
                        f"   LLM call #{calls}: "
                        f"in={u['input_tokens']:>6}  "
                        f"out={u['output_tokens']:>6}"
                    )


USER = {
    "id": "345f5f363ccc41739c2468994a53d9af",
    "username": "kristeena",
}

DOCS = ["b68daa6bb06d4c09ac54ead5f6d79781.pdf"]

QUERY = "What are the latest major developments in the Russia-Ukraine war?"


for kind, data in stream_agent(
    QUERY,
    USER,
    [],
    DOCS,
    callbacks=[TokenLog()],
):
    if kind == "status":
        print(data["node"])


print(f"\nTOTAL: {calls} calls, in={total_in}, out={total_out}")