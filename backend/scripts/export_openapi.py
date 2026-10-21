"""Export deterministic API contract without connecting to external services."""
import json
from pathlib import Path
from app.main import app

def main():
    target = Path(__file__).resolve().parents[2] / "frontend/src/generated/openapi.json"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(json.dumps(app.openapi(), ensure_ascii=False, sort_keys=True, indent=2) + "\n", encoding="utf-8")

if __name__ == "__main__":
    main()
