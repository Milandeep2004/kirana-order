# Kirana Order

Order capture, scheme engine and order desk for a Jabalpur FMCG distributor (hiring assignment, Software Engineering track, brief "Company A").

- Live app: https://kirana-order.onrender.com (free hosting: if it has been idle, the first load can take up to a minute)
- Run it, deploy it, demo logins: see [deployment.md](deployment.md)
- Design, assumptions, what was cut: see the Technical Design Document submitted with this repository

## What it does

A retailer sees the catalogue with their own prices, offers and stock, orders in a few taps, or pastes a WhatsApp-style message ("bhaiya 5 peti biscuit chhota, 2 peti washing powder 1 kilo") and reviews the lines the app understood. The order desk checks orders, handles credit holds, and exports confirmed orders to Tally as CSV.

- Scheme engine (`app/pricing.py`): free goods (buy 10, get 1) and slab discounts (3% off 5+ cases), a pure function with its own tests
- Message capture (`app/capture.py`): rules and fuzzy matching, not an LLM. Each line shows the retailer's words, a confidence label and two alternatives; unclear units are asked, not guessed; nothing becomes an order until a person confirms
- Stack: FastAPI, SQLAlchemy and SQLite, plain JavaScript front end (no build step), installable web app

## Quick start

```
pip install -r requirements.txt
copy .env.example .env      # macOS/Linux: cp .env.example .env
uvicorn app.main:app --reload
python -m pytest -q
```

All data is made up. Demo mode shows a fixed login code on purpose and must not be used with real data.
