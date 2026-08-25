"""Seed a fresh user with a bank + UPI statement for frontend testing."""
import json
import os
import sys

import requests
from dotenv import dotenv_values

base = (os.environ.get("REACT_APP_BACKEND_URL")
        or dotenv_values("/app/frontend/.env")["REACT_APP_BACKEND_URL"]).rstrip("/")
API = f"{base}/api"

EMAIL = "it17fe@qa.finaura.dev"
PWD = "testpass123"

BANK_CSV = (
    "Date,Description,Debit,Credit\n"
    "10/08/2025,UPI/SWIGGY/412345678901/Food order,450,\n"
    "05/08/2025,SALARY CREDIT ACME,,80000\n"
    "12/08/2025,ATM WITHDRAWAL BLR,5000,\n"
    "18/08/2025,Self transfer to own account,25000,\n"
)
UPI_CSV = (
    "Date,Description,Amount,UPI Ref\n"
    "12/08/2025,Swiggy,450,412345678901\n"
    "21/08/2025,Refund from Nykaa,1200,412345678903\n"
    "22/08/2025,Amazon Pay,2500,412345678904\n"
)

s = requests.Session()
r = s.post(f"{API}/auth/register", json={"email": EMAIL, "password": PWD, "name": "FE Tester"}, timeout=40)
if r.status_code == 409:
    r = s.post(f"{API}/auth/login", json={"email": EMAIL, "password": PWD}, timeout=40)
tok = r.json().get("token") or r.json().get("access_token")
assert tok, r.text[:300]
s.headers.update({"Authorization": f"Bearer {tok}"})
s.delete(f"{API}/financial/data", timeout=30)

for csv_text, src, name in ((BANK_CSV, "bank", "bank_aug_2025.csv"), (UPI_CSV, "upi", "upi_aug_2025.csv")):
    files = {"file": (name, csv_text.encode(), "text/csv")}
    prev = s.post(f"{API}/statements/preview", files=files, data={"source": src}, timeout=40).json()
    files = {"file": (name, csv_text.encode(), "text/csv")}
    parsed = s.post(f"{API}/statements/parse", files=files,
                    data={"mapping": json.dumps(prev.get("guess") or {}), "source": src}, timeout=40).json()
    res = s.post(f"{API}/statements/confirm-import",
                 json={"transactions": parsed["transactions"], "source": src, "file_name": name}, timeout=40).json()
    print(src, res)

print("statements:", json.dumps(s.get(f"{API}/statements/list", timeout=30).json(), indent=1))
ov = s.get(f"{API}/financial/overview", timeout=30).json()
print("summary:", ov["summary"], "txns:", len(ov["transactions"]),
      "has_demo:", ov["has_demo_data"], "has_real:", ov["has_real_data"])
print("CREDENTIALS", EMAIL, PWD)
