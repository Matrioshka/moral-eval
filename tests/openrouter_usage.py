import os
from pathlib import Path

import requests, json

# OPENROUTER_API_KEY = userdata.get("OPENROUTER_API_KEY") #For Colab
OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY")

if not OPENROUTER_API_KEY:
    env_path = Path(__file__).resolve().parents[1] / ".env"
    if env_path.exists():
        for line in env_path.read_text(encoding="utf-8").splitlines():
            key, sep, value = line.partition("=")
            if sep and key.strip() == "OPENROUTER_API_KEY":
                OPENROUTER_API_KEY = value.strip().strip("\"'")
                break

if not OPENROUTER_API_KEY:
    raise SystemExit(
        "OPENROUTER_API_KEY is not set. Add it to .env or set it in your shell before running this script."
    )

#os.environ["OPENROUTER_API_KEY"] = OPENROUTER_API_KEY




response = requests.get(
            "https://openrouter.ai/api/v1/auth/key",
            headers={"Authorization": f"Bearer {OPENROUTER_API_KEY}"},
            timeout=10
        )
# response.raise_for_status()
#print(response.json())
#print(response.json().dumps())
my_dict = response.json()
print("OPENROUTER_API_KEY usage -",f"Limit: {my_dict['data']['limit']},",
   f"Limit Remaining: {my_dict['data']['limit_remaining']}")
