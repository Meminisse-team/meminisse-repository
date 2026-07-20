"""Claude output_config.format json_schema 응답 블록 타입 확인용 임시 스크립트."""
import asyncio
import os
import sys

sys.path.insert(0, ".")
from dotenv import load_dotenv

load_dotenv(".env")

import anthropic


async def test():
    client = anthropic.AsyncAnthropic(api_key=os.environ["ANTHROPIC_API_KEY"])
    schema = {
        "type": "object",
        "properties": {"result": {"type": "string"}},
        "required": ["result"],
        "additionalProperties": False,
    }
    async with client.messages.stream(
        model="claude-sonnet-5",
        max_tokens=256,
        messages=[{"role": "user", "content": 'Return {"result": "hello"}'}],
        thinking={"type": "adaptive"},
        output_config={"format": {"type": "json_schema", "schema": schema}},
    ) as stream:
        msg = await stream.get_final_message()
    print("stop_reason:", msg.stop_reason)
    print("num content blocks:", len(msg.content))
    for i, b in enumerate(msg.content):
        print(f"  block[{i}]: type={b.type}")
        if hasattr(b, "text"):
            print(f"    text: {b.text[:200]}")


asyncio.run(test())
