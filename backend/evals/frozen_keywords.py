"""Versioned keyword ground truth. Normal scoring never invokes an extractor."""
import argparse
import asyncio
import hashlib
import json
from pathlib import Path
from pydantic import BaseModel, ConfigDict, Field, field_validator

NORMALIZER_VERSION = "korean-josa-substring-v1"

def digest(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()

class KeywordFixture(BaseModel):
    model_config = ConfigDict(extra="forbid")
    schema_version: str = "1"
    source_sha256: str = Field(pattern=r"^[0-9a-f]{64}$")
    provenance: str
    extractor: str
    keywords: list[str] = Field(min_length=1)

    @field_validator("keywords")
    @classmethod
    def unique_nonempty(cls, value):
        if any(not x.strip() or x != x.strip() for x in value) or len(value) != len(set(value)):
            raise ValueError("keywords must be unique, nonempty and trimmed")
        return value

    @field_validator("schema_version")
    @classmethod
    def version(cls, value):
        if value != "1": raise ValueError("unsupported fixture version")
        return value

def load_fixture(path: Path, raw_input: str) -> KeywordFixture:
    fixture = KeywordFixture.model_validate_json(path.read_text(encoding="utf-8"))
    if fixture.source_sha256 != digest(raw_input):
        raise ValueError("Ground truth source hash mismatch: freeze/review a new fixture for the changed input")
    return fixture

async def freeze(source: Path, output: Path):
    from evals.information_preservation import extract_keyword_pool
    if output.exists(): raise FileExistsError("Refusing to overwrite frozen ground truth")
    text = source.read_text(encoding="utf-8")
    keywords = await extract_keyword_pool(text, top_k=30)
    fixture = KeywordFixture(source_sha256=digest(text), provenance=source.name,
                             extractor="solar-pro3; reasoning_effort=low; review required", keywords=keywords)
    with output.open("x", encoding="utf-8") as file:
        file.write(fixture.model_dump_json(indent=2) + "\n")

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Extract ONCE, then review and version the fixture. Never overwrite.")
    parser.add_argument("--source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    args = parser.parse_args()
    asyncio.run(freeze(args.source, args.output))
