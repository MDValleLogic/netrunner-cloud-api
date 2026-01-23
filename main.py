from fastapi import FastAPI, HTTPException
from typing import List, Optional
from datetime import datetime, timezone
import os
import psycopg2


app = FastAPI(title="NetRunner Cloud API", version="0.2")

# Supabase Postgres connection string goes in env var DATABASE_URL
DATABASE_URL = os.environ.get("DATABASE_URL")


def utcnow():
    return datetime.now(timezone.utc)


def get_conn():
    if not DATABASE_URL:
        raise HTTPException(status_code=500, detail="DATABASE_URL not set")
    # Supabase pooler/direct both work with psycopg2
    return psycopg2.connect(DATABASE_URL)


# ---- health ----
@app.get("/health")
def health():
    return {"status": "ok", "time_utc": utcnow().isoformat()}


# ---- device heartbeat ----
@app.post("/v1/device/heartbeat")
def heartbeat(
    device_id: str,
    node_role: Optional[str] = None,
    hostname: Optional[str] = None,
):
    """
    Upsert device row, update last_seen_at and store last_heartbeat JSON.
    NOTE: device_secret is hardcoded for now (MVP). We'll fix auth later.
    """
    hb = {
        "device_id": device_id,
        "node_role": node_role,
        "hostname": hostname,
        "time_utc": utcnow().isoformat(),
    }

    conn = get_conn()
    cur = conn.cursor()

    # device_secret is MVP placeholder
    cur.execute(
        """
        insert into devices (device_id, device_secret, hostname, node_role, last_seen_at, last_heartbeat)
        values (%s, %s, %s, %s, now(), %s::jsonb)
        on conflict (device_id)
        do update set
            hostname = excluded.hostname,
            node_role = excluded.node_role,
            last_seen_at = now(),
            last_heartbeat = excluded.last_heartbeat;
        """,
        (device_id, "dev-secret", hostname, node_role, psycopg2.extras.Json(hb)),
    )

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True, "device_id": device_id}


# ---- device config fetch ----
@app.get("/v1/device/config")
def get_config(device_id: str):
    """
    Returns config for device from device_urls table.
    If none exist, return a safe default config.
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        select url_id, url, enabled, interval_sec
        from device_urls
        where device_id = %s
        order by url_id asc;
        """,
        (device_id,),
    )
    rows = cur.fetchall()

    cur.close()
    conn.close()

    if not rows:
        return {
            "device_id": device_id,
            "urls": [
                {"id": "u1", "url": "https://example.org", "enabled": True, "interval_sec": 60}
            ],
            "default_interval_sec": 60,
        }

    urls = [
        {"id": r[0], "url": r[1], "enabled": r[2], "interval_sec": r[3]}
        for r in rows
    ]
    return {"device_id": device_id, "urls": urls, "default_interval_sec": 60}


# ---- upload results ----
@app.post("/v1/device/results")
def upload_results(device_id: str, results: List[dict]):
    """
    Inserts results rows into results table.
    Expected result fields (from the Pi agent): timestamp_utc, url_id, url, success, http_status, total_ms, error
    """
    if not isinstance(results, list):
        raise HTTPException(status_code=400, detail="results must be a list")

    conn = get_conn()
    cur = conn.cursor()

    inserted = 0
    for r in results:
        # tolerate missing fields (MVP)
        ts = r.get("timestamp_utc") or utcnow().isoformat()
        cur.execute(
            """
            insert into results (device_id, url_id, url, timestamp_utc, success, http_status, total_ms, error)
            values (%s, %s, %s, %s, %s, %s, %s, %s);
            """,
            (
                device_id,
                r.get("url_id"),
                r.get("url"),
                ts,
                r.get("success"),
                r.get("http_status"),
                r.get("total_ms"),
                r.get("error"),
            ),
        )
        inserted += 1

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True, "count": inserted}


# ---- admin: set config ----
@app.put("/v1/admin/config/{device_id}")
def set_config(device_id: str, body: dict):
    """
    Body format (same as your stub):
    {
      "urls":[{"id":"u1","url":"https://example.org","enabled":true,"interval_sec":60}, ...],
      "default_interval_sec":60
    }
    Writes rows into device_urls.
    """
    urls = body.get("urls", [])
    if not isinstance(urls, list):
        raise HTTPException(status_code=400, detail="body.urls must be a list")

    conn = get_conn()
    cur = conn.cursor()

    # ensure device exists (cheap upsert)
    cur.execute(
        """
        insert into devices (device_id, device_secret, last_seen_at)
        values (%s, %s, now())
        on conflict (device_id) do nothing;
        """,
        (device_id, "dev-secret"),
    )

    # Replace config rows
    cur.execute("delete from device_urls where device_id = %s;", (device_id,))

    for u in urls:
        cur.execute(
            """
            insert into device_urls (device_id, url_id, url, enabled, interval_sec)
            values (%s, %s, %s, %s, %s);
            """,
            (
                device_id,
                u.get("id"),
                u.get("url"),
                bool(u.get("enabled", True)),
                int(u.get("interval_sec", body.get("default_interval_sec", 60))),
            ),
        )

    conn.commit()
    cur.close()
    conn.close()

    return {"ok": True, "device_id": device_id, "urls": len(urls)}


# ---- admin: view results ----
@app.get("/v1/admin/results/{device_id}")
def admin_results(device_id: str):
    """
    Returns last 50 results (newest last, so UI can chart easily).
    """
    conn = get_conn()
    cur = conn.cursor()

    cur.execute(
        """
        select timestamp_utc, url_id, url, success, http_status, total_ms, error
        from results
        where device_id = %s
        order by timestamp_utc asc
        limit 50;
        """,
        (device_id,),
    )
    rows = cur.fetchall()

    cur.close()
    conn.close()

    latest = [
        {
            "timestamp_utc": r[0].isoformat() if hasattr(r[0], "isoformat") else str(r[0]),
            "url_id": r[1],
            "url": r[2],
            "success": r[3],
            "http_status": r[4],
            "total_ms": r[5],
            "error": r[6],
        }
        for r in rows
    ]

    return {"device_id": device_id, "count": len(latest), "latest": latest}
