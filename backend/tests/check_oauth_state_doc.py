import asyncio, os, requests
from datetime import datetime, timezone
from urllib.parse import urlparse, parse_qs
from dotenv import dotenv_values
from motor.motor_asyncio import AsyncIOMotorClient

be = dotenv_values("/app/backend/.env")
fe = dotenv_values("/app/frontend/.env")
API = fe["REACT_APP_BACKEND_URL"].rstrip("/") + "/api"


async def main():
    r = requests.get(f"{API}/auth/google/start?next=/settings", allow_redirects=False)
    state = parse_qs(urlparse(r.headers["location"]).query)["state"][0]
    c = AsyncIOMotorClient(be["MONGO_URL"])
    db = c[be["DB_NAME"]]
    doc = await db.oauth_states.find_one({"state": state})
    assert doc, "state not persisted"
    print("provider:", doc.get("provider"), "| next:", doc.get("next"))
    exp = doc["expires_at"].replace(tzinfo=timezone.utc)
    mins = (exp - datetime.now(timezone.utc)).total_seconds() / 60
    print(f"expires_in_minutes: {mins:.2f}")
    assert 9 <= mins <= 10.1, mins
    print("indexes:", await db.oauth_states.index_information())
    print("pending states count:", await db.oauth_states.count_documents({}))
    await db.oauth_states.delete_one({"state": state})
    print("OK")


asyncio.run(main())
