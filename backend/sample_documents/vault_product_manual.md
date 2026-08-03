# Northgate Vault: Product Manual

Product: Northgate Vault (custody and portfolio reporting platform)
Release 4.6 · Audience: relationship managers and client operations

## 1. What Vault does

Northgate Vault is the platform clients use to view custody positions, run portfolio
reports and initiate instructions. It consolidates holdings across our custodian network
into a single position view, refreshed at each end-of-day cycle.

Vault is not a trading system. Orders are placed through the execution desk; Vault shows
the resulting positions once they settle.

## 2. Account tiers

| Tier | Minimum AUM | Reporting | Support |
| --- | --- | --- | --- |
| Essential | EUR 250,000 | Monthly statement | Email, next business day |
| Advantage | EUR 2,000,000 | Monthly + on-demand | Email and phone, 4 hours |
| Private | EUR 10,000,000 | Daily + bespoke | Named relationship manager, 1 hour |

Tier is assessed quarterly on the average daily assets under management over the quarter.
A client crossing a threshold upward is upgraded at the start of the next quarter; a
client falling below a threshold keeps their tier for two further quarters before
downgrade, which gives time for planned inflows to land.

## 3. Getting a client started

### 3.1 Provisioning

Once onboarding checks are complete, the relationship manager raises a provisioning
request in the Client Operations queue. Provisioning takes one business day. The client
receives an activation email valid for 72 hours; if it expires, the relationship manager
can reissue it from the client record.

### 3.2 Entitlements

Every Vault user has one of three entitlement levels:

- **Viewer**: read-only access to positions and reports.
- **Instructor**: may prepare instructions but not release them.
- **Approver**: may release instructions up to the client's mandated limit.

Dual authorisation is mandatory for any cash movement: an Instructor prepares and a
different Approver releases. A single user can never hold both roles on the same
instruction, and the system enforces this.

## 4. Reporting

### 4.1 Standard reports

Vault ships with valuation, transaction history, income and realised-gains reports. All of
them export to PDF, XLSX and CSV. Report data is as of the previous end-of-day cycle; the
timestamp is printed in the header of every export.

### 4.2 Bespoke reports

Private-tier clients may request bespoke report templates. Requests go through the
relationship manager to Client Reporting, and typically take ten business days to build
and validate. Bespoke templates are re-validated after any change to the client's mandate.

### 4.3 Performance figures

Performance is calculated time-weighted by default, net of fees. Money-weighted (IRR)
figures are available on Advantage and Private tiers as an alternative view. Where a
portfolio has been transferred in mid-period, performance before the transfer date is
shown as unavailable rather than estimated.

## 5. Instructions and cut-off times

Cash and securities instructions submitted in Vault are picked up by Operations at the
following cut-offs, in Central European Time:

- Domestic cash payments: 15:00 same day
- Cross-border cash payments: 12:00 same day
- Securities transfers: 11:00 same day
- Standing instruction changes: 16:00, effective next business day

Anything submitted after the cut-off is processed on the next business day. Cut-offs are
one hour earlier on the last business day of the year.

## 6. Fees

Custody fees are charged quarterly in arrears, calculated on the average daily market
value of assets held. Transaction fees are charged per settled instruction. The full fee
schedule is in the client's agreement and mirrored on the Fees tab in Vault.

Fee disputes are raised through the relationship manager and investigated by Client
Operations within ten business days.

## 7. Support and incidents

Support requests are raised from the Help menu in Vault, which attaches diagnostic context
automatically. Response targets are set by tier as shown in section 2.

Service incidents are published on the Vault status page. Where an incident prevents
instruction submission before a cut-off, Operations extends that day's cut-off by the
duration of the outage and notifies affected clients directly.

## 8. Known limitations

- Positions are end-of-day; intraday valuations are not available.
- Vault does not support fractional shares for instruments held under the Milan custodian.
- The mobile app is read-only: instructions must be prepared and released on desktop.
