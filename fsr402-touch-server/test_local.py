import urllib.request
import json

req = urllib.request.Request(
    "http://127.0.0.1:9333/touch",
    data=json.dumps({"pressure": 123, "sensor": "test"}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req)
print("status:", resp.status)
print("body:", resp.read().decode())
