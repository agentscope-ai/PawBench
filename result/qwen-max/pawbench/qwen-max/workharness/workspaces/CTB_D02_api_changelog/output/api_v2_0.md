# Analytics API v2.0

## 1. Authentication

- Endpoint: `POST /v2/auth/tokens`
- Request fields:
  - `client_id` (string)
  - `client_secret` (string)
- Response fields:
  - `access_token` (string)
  - `refresh_token` (string)
  - `expires_in` (integer)

## 2. Event Query

- Endpoint: `GET /v2/events`
- Query parameters:
  - `from` (YYYY-MM-DD, required)
  - `to` (YYYY-MM-DD, required)
  - `cursor` (string, optional)
- Removed parameter:
  - `page`
- Response root fields:
  - `total`
  - `items`

## 3. Order Creation

- Endpoint: `POST /v2/orders`
- Request body:
  - `account_id` (string, required)
  - `amount` (decimal string, required)
  - `currency` (ISO 4217 code, required)
  - `note` (string, optional)
- Removed fields:
  - `customer_id`
  - `amount_cents`
- Response body:
  - `order_id`
  - `status`

## 4. Order Estimate

- New endpoint: `POST /v2/orders/estimate`
- Purpose: return estimated tax and fee breakdown before order submission

## 5. Daily Report

- `GET /v1/reports/daily` is deprecated
- Use `GET /v2/reports/daily-summary` instead

## 6. Webhook Verification

- Required headers:
  - `X-Signature`
  - `X-Timestamp`
- Signature algorithm: `HMAC-SHA256`
- Reject webhook events with timestamps older than 5 minutes
