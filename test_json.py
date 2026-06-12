import json
from src.models import ReconciliationRequest
from pydantic import ValidationError

with open("JSON/Inputjson.json", "r") as f:
    data = json.load(f)

try:
    req = ReconciliationRequest(**data)
    print("SUCCESS")
    print(req.model_dump())
except ValidationError as e:
    print("VALIDATION ERROR:")
    print(e)
