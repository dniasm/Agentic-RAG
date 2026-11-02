from backend.config.llm import get_nemotron_llm

reply = get_nemotron_llm().invoke("How many r's are in strawberry?")
print(reply.content)