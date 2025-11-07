Tints & Audio — Manager MVP

Overview
- Lightweight, static web app to manage customers, appointments, and sales for a car tinting and audio shop.
- Pure HTML/CSS/JS. Data is stored in the browser via localStorage.

Quick start
1) Open index.html directly in your browser
   - macOS: open tints/index.html
   - Windows: double‑click tints/index.html
   - Linux: xdg-open tints/index.html

   Or serve with a simple local server (optional):
   - Python 3: cd tints && python -m http.server 8080
   - Then open http://localhost:8080

Features (MVP)
- Dashboard KPIs (customers, upcoming appointments, monthly revenue)
- Customers: add/edit/delete, search, table listing
- Appointments: add/edit/delete, search, table listing
- Sales: add/edit/delete, search, table listing
- Settings: basic shop details (persisted locally), export/import JSON, reset data

Data & storage
- Data is scoped to your browser profile using localStorage (no server).
- Keys used:
  - tints_customers
  - tints_appointments
  - tints_sales
  - tints_settings

Notes
- This is for internal/local use. There is no authentication and no backend.
- Clearing site data or using a different browser/profile will reset the app data.
