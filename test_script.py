import requests
import json

data = {
    "test_cases": [{
        "tc_id": "TC-01",
        "page_url": "https://example.com",
        "automation_steps": [
            "assert text 'Example Domain'"
        ]
    }]
}
res = requests.post(
    "http://127.0.0.1:10000/execute", 
    headers={"X-Internal-Secret": "dev_secret_change_me"},
    json=data
)
print(json.dumps(res.json(), indent=2))
