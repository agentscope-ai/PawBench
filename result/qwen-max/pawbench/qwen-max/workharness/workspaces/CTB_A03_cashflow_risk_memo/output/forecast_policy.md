# Treasury Forecast Policy

This task uses the following forecast methodology:

1. The forecast period is fixed at `2026-01` through `2026-06`.
2. The forecast method is fixed as "prior-year same-month basis":
   - Each month's `Forecast Inflow` for 2026 = the `cash_in` value from the same month in `2025`
   - Each month's `Forecast Outflow` for 2026 = the `cash_out` value from the same month in `2025`
3. The starting cash balance for 2026-01 is fixed at the ending cash balance as of `2025-12-31`, which is `7.4`.
4. The safety line is fixed at `5.0` (in millions of CNY).
5. `Funding Gap = max(0, Safety Line - Ending Cash Balance)`.
6. In the conclusions, clearly state:
   - Which months fall below the safety line
   - In which month the minimum cash balance occurs
   - In which month the peak funding gap occurs
7. The final memo should be addressed to the CFO's office, clearly structured, and ready for risk communication.
