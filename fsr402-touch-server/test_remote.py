import urllib.request
import json

req = urllib.request.Request(
    "http://43.133.211.140:9333/touch",
    data=json.dumps({"pressure": 456, "sensor": "remote_test"}).encode(),
    headers={"Content-Type": "application/json"},
)
resp = urllib.request.urlopen(req, timeout=10)
print("status:", resp.status)
print("body:", resp.read().decode())
