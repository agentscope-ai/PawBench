# Analytics API v1.2

## 1. Authentication

- Endpoint: `POST /v1/sessions`
- Request fields:
  - `client_id` (string)
  - `client_secret` (string)
- Response fields:
  - `session_id` (string)
  - `expires_in_seconds` (integer)

## 2. Event Query

- Endpoint: `GET /v1/events`
- Query parameters:
  - `start_date` (YYYY-MM-DD, required)
  - `end_date` (YYYY-MM-DD, required)
  - `page` (integer, optional)
- Response root fields:
  - `event_count`
  - `items`

## 3. Order Creation

- Endpoint: `POST /v1/orders`
- Request body:
  - `customer_id` (string, required)
  - `amount_cents` (integer, required)
  - `note` (string, optional)
- Response body:
  - `order_id`
  - `status`

## 4. Daily Report

- Endpoint: `GET /v1/reports/daily`
- Purpose: returns daily sales and refund summary

## 5. Webhook Verification

- Required header: `X-Signature`
- Signature algorithm: `HMAC-SHA1`
- No timestamp header is required in v1.2
