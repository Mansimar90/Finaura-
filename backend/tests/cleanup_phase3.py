"""One-off cleanup of Phase 3 QA test data (imported CSV txns + TEST_ goal)."""
from pymongo import MongoClient
from dotenv import dotenv_values

env = dotenv_values("/app/backend/.env")
db = MongoClient(env["MONGO_URL"])[env["DB_NAME"]]

descs = ["TEST_IMPORT_CHECK", "SWIGGY DINNER", "AMAZON ORDER", "SALARY AUG"]
r1 = db.finaura_transactions.delete_many({"description": {"$in": descs}})
r2 = db.finaura_goals.delete_many({"name": {"$regex": "^TEST_"}})
print("deleted transactions:", r1.deleted_count, "| deleted goals:", r2.deleted_count)
print("collections:", [c for c in db.list_collection_names() if "goal" in c or "transaction" in c])
