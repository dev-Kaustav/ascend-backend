# Ascend Foods Backend

## Quickstart
1) Build and start services:
```bash
docker compose up --build -d
```
If your Docker install uses the legacy CLI, replace `docker compose` with `docker-compose`.

2) Run migrations:
```bash
docker compose exec app alembic upgrade head
```

3) Seed an admin user:
```bash
docker compose exec app python seed.py
```

4) Run tests:
```bash
docker compose exec app pytest
```

## Environment
- `DATABASE_URL` (default in `docker-compose.yml`)
- `SECRET_KEY`

## Curl Examples
Login:
```bash
curl -X POST http://localhost:8000/auth/login \
  -H "Content-Type: application/json" \
  -d '{"email":"admin@ascend.com","password":"password"}'
```

Set token:
```bash
export TOKEN="your-access-token"
```

Create brand:
```bash
curl -X POST http://localhost:8000/admin/brands \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"name":"Brand A","contact_info":"brand@example.com"}'
```

Add inventory:
```bash
curl -X POST http://localhost:8000/admin/inventory \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "brand_id": 1,
    "warehouse_id": 1,
    "items": [
      {
        "sku_id": 1,
        "quantity": 10,
        "mfg_date": "2024-01-01",
        "expiry_date": "2025-01-01"
      }
    ]
  }'
```

Create outgoing order:
```bash
curl -X POST http://localhost:8000/orders \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "retailer_id": 1,
    "warehouse_id": 1,
    "items": [
      {
        "sku_id": 1,
        "quantity": 5,
        "unit_price": 100,
        "discount_amount": 0,
        "taxes": [{"tax_type":"GST","rate":18}]
      }
    ]
  }'
```

Confirm order (generates invoice number):
```bash
curl -X PATCH http://localhost:8000/orders/1/status \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"status":"CONFIRMED"}'
```

Invoice view:
```bash
curl -X GET http://localhost:8000/orders/1/invoice-view \
  -H "Authorization: Bearer $TOKEN"
```

Record payment:
```bash
curl -X POST http://localhost:8000/accounting/orders/1/payments \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{"amount": 100, "transaction_reference": "txn-001"}'
```

Create credit note:
```bash
curl -X POST http://localhost:8000/accounting/credit-notes \
  -H "Authorization: Bearer $TOKEN" \
  -H "Content-Type: application/json" \
  -d '{
    "order_id": 1,
    "restock": true,
    "items": [{"sku_id": 1, "quantity": 1, "unit_price": 100}]
  }'
```

Credit note view:
```bash
curl -X GET http://localhost:8000/credit-notes/1/view \
  -H "Authorization: Bearer $TOKEN"
```
