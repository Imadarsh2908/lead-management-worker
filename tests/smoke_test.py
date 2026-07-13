import urllib.request
import json

def test_api():
    print("Starting API smoke test...")
    
    # 1. Health check
    res = urllib.request.urlopen("http://127.0.0.1:8000/health")
    health = json.loads(res.read().decode())
    print("Health response:", health)
    assert health["status"] == "ok"

    # 2. Login
    login_data = json.dumps({"username": "admin_user", "password": "password123"}).encode("utf-8")
    req = urllib.request.Request(
        "http://127.0.0.1:8000/v1/auth/login",
        data=login_data,
        headers={"Content-Type": "application/json"}
    )
    res = urllib.request.urlopen(req)
    tokens = json.loads(res.read().decode())
    print("Login success. Received access token.")
    access_token = tokens["access_token"]

    # 3. Create lead
    lead_data = json.dumps({
        "email": "john.doe@google.com",
        "budget": 120000,
        "job_title": "CTO",
        "first_name": "John",
        "last_name": "Doe",
        "phone": "+15550199",
        "company": "Google",
        "enrichment_data": {"size": "Enterprise", "industry": "Technology"},
    }).encode("utf-8")
    
    req = urllib.request.Request(
        "http://127.0.0.1:8000/v1/leads/",
        data=lead_data,
        headers={
            "Content-Type": "application/json",
            "Authorization": f"Bearer {access_token}"
        }
    )
    res = urllib.request.urlopen(req)
    lead = json.loads(res.read().decode())
    print("Lead created successfully:", lead)
    assert lead["email"] == "john.doe@google.com"
    lead_id = lead["id"]

    # 4. Get Lead by ID
    req = urllib.request.Request(
        f"http://127.0.0.1:8000/v1/leads/{lead_id}",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )
    res = urllib.request.urlopen(req)
    fetched_lead = json.loads(res.read().decode())
    print("Lead fetched successfully:", fetched_lead)
    assert fetched_lead["id"] == lead_id

    # 5. List Leads
    req = urllib.request.Request(
        "http://127.0.0.1:8000/v1/leads/",
        headers={
            "Authorization": f"Bearer {access_token}"
        }
    )
    res = urllib.request.urlopen(req)
    leads_list = json.loads(res.read().decode())
    print("Leads list fetched successfully. Count:", len(leads_list))
    assert len(leads_list) > 0

    print("All smoke tests passed successfully!")

if __name__ == "__main__":
    test_api()
