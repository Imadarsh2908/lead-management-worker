"""
tests/test_lead_import.py
-------------------------
Covers bulk lead import from all three sources — file upload, pasted text, and
the inbound-email webhook — plus the pure parsers in app/core/lead_import.

The endpoints queue a real background workflow per created lead; the conftest
`client` fixture runs on SQLite with overridden deps, and process_lead is safe
to enqueue (it's a no-op background task in TestClient's synchronous flow that
we don't assert on here — we assert the import *summary* and DB rows).
"""
import io
import uuid

from openpyxl import Workbook

from app.core.lead_import import parse_csv, parse_json, parse_xlsx, parse_inbound_email, normalize_row


# ─────────────────────────────────────────────────────────────
# Pure parsers
# ─────────────────────────────────────────────────────────────

def test_normalize_row_aliases_and_budget_coercion():
    row = normalize_row({"Email Address": " Jane@Corp.com ", "First Name": "Jane",
                         "Designation": "CEO", "Budget": "₹5,00,000", "Unknown Col": "x"})
    assert row["email"] == "Jane@Corp.com"      # trimmed (not lowercased here — the model does that)
    assert row["first_name"] == "Jane"
    assert row["job_title"] == "CEO"             # 'Designation' alias
    assert row["budget"] == 500000.0            # currency/thousands stripped
    assert "Unknown Col" not in row              # unrecognized column dropped


def test_parse_csv_and_blank_handling():
    rows = parse_csv("email,company\na@b.com,Acme\n,\nc@d.com,")
    assert rows[0] == {"email": "a@b.com", "company": "Acme"}
    assert rows[1] == {}                          # fully blank row → empty dict
    assert rows[2] == {"email": "c@d.com"}        # blank company dropped


def test_parse_json_single_and_array():
    assert parse_json('{"email":"x@y.com","title":"VP"}') == [{"email": "x@y.com", "job_title": "VP"}]
    two = parse_json('[{"email":"a@b.com"},{"email":"c@d.com","org":"Acme"}]')
    assert two == [{"email": "a@b.com"}, {"email": "c@d.com", "company": "Acme"}]


def test_parse_xlsx_roundtrip():
    wb = Workbook()
    ws = wb.active
    ws.append(["Email", "Company", "Budget"])
    ws.append(["jane@corp.com", "Globex", 750000])
    ws.append([None, None, None])                 # blank row skipped
    buf = io.BytesIO(); wb.save(buf)
    rows = parse_xlsx(buf.getvalue())
    assert rows == [{"email": "jane@corp.com", "company": "Globex", "budget": 750000.0}]


def test_parse_inbound_email_extracts_sender():
    row = parse_inbound_email({"from": "Sam Rao <sam.rao@acme.com>", "subject": "Interested"})
    assert row == {"email": "sam.rao@acme.com", "first_name": "Sam", "last_name": "Rao"}


# ─────────────────────────────────────────────────────────────
# API: paste import
# ─────────────────────────────────────────────────────────────

def test_import_paste_csv_partial_success(client):
    uniq = uuid.uuid4().hex[:8]
    csv = (
        "email,company,budget\n"
        f"good1-{uniq}@corp.com,Acme,600000\n"
        "not-an-email,BadCo,100\n"          # invalid email → error row
        f"good2-{uniq}@corp.com,Beta,\n"
    )
    resp = client.post("/v1/leads/import/paste", json={"format": "csv", "data": csv})
    assert resp.status_code == 200
    s = resp.json()
    assert s["total"] == 3
    assert s["created"] == 2
    assert s["errors"] == 1
    assert s["error_details"][0]["row"] == 2          # the invalid-email row
    assert "email" in s["error_details"][0]["reason"].lower()


def test_import_paste_dedup_skips(client):
    uniq = uuid.uuid4().hex[:8]
    email = f"dup-{uniq}@corp.com"
    first = client.post("/v1/leads/import/paste", json={"format": "csv", "data": f"email\n{email}"})
    assert first.json()["created"] == 1
    # same email again → skipped, not created, not an error
    again = client.post("/v1/leads/import/paste", json={"format": "csv", "data": f"email\n{email}"})
    s = again.json()
    assert s["created"] == 0 and s["skipped_duplicates"] == 1 and s["errors"] == 0


def test_import_paste_json(client):
    uniq = uuid.uuid4().hex[:8]
    body = f'[{{"email":"j1-{uniq}@corp.com","job_title":"CEO"}}]'
    resp = client.post("/v1/leads/import/paste", json={"format": "json", "data": body})
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_import_paste_bad_text_is_422(client):
    resp = client.post("/v1/leads/import/paste", json={"format": "json", "data": "{not valid json"})
    assert resp.status_code == 422


# ─────────────────────────────────────────────────────────────
# API: file upload
# ─────────────────────────────────────────────────────────────

def test_import_file_csv(client):
    uniq = uuid.uuid4().hex[:8]
    csv = f"email,company\nfile-{uniq}@corp.com,Acme\n".encode("utf-8")
    resp = client.post(
        "/v1/leads/import/file",
        files={"file": ("leads.csv", csv, "text/csv")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_import_file_xlsx(client):
    uniq = uuid.uuid4().hex[:8]
    wb = Workbook(); ws = wb.active
    ws.append(["Email", "Job Title"])
    ws.append([f"xl-{uniq}@corp.com", "Director"])
    buf = io.BytesIO(); wb.save(buf)
    resp = client.post(
        "/v1/leads/import/file",
        files={"file": ("leads.xlsx", buf.getvalue(),
                        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1


def test_import_file_unsupported_type_415(client):
    resp = client.post(
        "/v1/leads/import/file",
        files={"file": ("leads.txt", b"whatever", "text/plain")},
    )
    assert resp.status_code == 415


# ─────────────────────────────────────────────────────────────
# API: inbound-email webhook (token-guarded)
# ─────────────────────────────────────────────────────────────

def test_inbound_email_disabled_without_token(client):
    """Webhook is inert (404) unless INBOUND_EMAIL_TOKEN is configured."""
    resp = client.post("/v1/leads/inbound-email", json={"from": "a@b.com"})
    assert resp.status_code == 404


def test_inbound_email_wrong_token_404(client):
    from app.core.config import settings
    from unittest.mock import patch
    from pydantic import SecretStr
    with patch.object(settings, "INBOUND_EMAIL_TOKEN", SecretStr("s3cret")):
        resp = client.post("/v1/leads/inbound-email?token=wrong", json={"from": "a@b.com"})
    assert resp.status_code == 404


def test_inbound_email_creates_lead_with_valid_token(client):
    from app.core.config import settings
    from unittest.mock import patch
    from pydantic import SecretStr
    uniq = uuid.uuid4().hex[:8]
    with patch.object(settings, "INBOUND_EMAIL_TOKEN", SecretStr("s3cret")):
        resp = client.post(
            "/v1/leads/inbound-email?token=s3cret",
            json={"from": f"Lead Person <inbound-{uniq}@corp.com>", "subject": "Demo request"},
        )
    assert resp.status_code == 200
    assert resp.json()["created"] == 1
