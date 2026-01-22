from fastapi import FastAPI, Header, HTTPException
from typing import List, Optional
from datetime import datetime

app = FastAPI(title="NetRunner Cloud API", version="0.1")

# ---- in-memory stub (we will replace with Supabase next) ----
DEVICES = {}
CONFIGS = {}
RESULTS = {}

# ---- health ----
@app.get("/health")
def health():
    return {"status": "ok", "time_utc": datetime.utcnow().isoformat()}

# ---- device heartbeat ----
@app.post("/v1/device/heartbeat")
def heartbeat(
    device_id: str,
    node_role: Optional[str] = None,
    hostname: Optional[str] = None,
):
    DEVICES[device_id] = {
        "device_id": device_id,
        "node_role": node_role,
        "hostname": hostname,
        "last_seen_at": datetime.utcnow().isoformat(),
    }
    return {"ok": True}

# ---- device config fetch ----
@app.get("/v1/device/config")
def get_config(device_id: str):
    cfg = CONFIGS.get(device_id)
    if not cfg:
        # default config so devices always work
        return {
            "device_id": device_id,
            "urls": [
                {
                    "id": "u1",
                    "url": "https://example.org",
                    "enabled": True,
                    "interval_sec": 60,
                }
            ],
            "default_interval_sec": 60,
        }
    return cfg

# ---- upload results ----
@app.post("/v1/device/results")
def upload_results(device_id: str, results: List[dict]):
    RESULTS.setdefault(device_id, []).extend(results)
    return {"ok": True, "count": len(results)}

# ---- admin: set config ----
@app.put("/v1/admin/config/{device_id}")
def set_config(device_id: str, body: dict):
    CONFIGS[device_id] = {
        "device_id": device_id,
        **body,
    }
    return {"ok": True, "device_id": device_id}

# ---- admin: view results ----
@app.get("/v1/admin/results/{device_id}")
def admin_results(device_id: str):
    data = RESULTS.get(device_id, [])
    return {
        "device_id": device_id,
        "count": len(data),
        "latest": data[-50:],
    }
