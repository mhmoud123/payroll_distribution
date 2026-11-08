# Payroll Distribution

Monthly salary entry with per-employee cost center distribution for **Frappe HRMS v15**.

It replaces a legacy desktop payroll screen: the payroll officer picks a month and an employee, enters
the month's variables, splits the employee's days and overtime hours across cost centers (projects),
and posts. Posting creates the journal entry with the salary cost split by cost center.

Built on standard HRMS (Salary Component, Salary Structure, Salary Slip). It does **not** use the
Payroll Entry cycle: a Salary Slip posts nothing to the ledger on its own, so this app creates the
journal entry itself.

Requires Frappe v15, ERPNext v15 and HRMS v15 (Python 3.10 or later). The same code also runs on v16.

## What it adds

| Piece | What it does |
| --- | --- |
| **Monthly Salary Entry** page | Company, month, year and employee pickers, previous/next through the month, two tabs (salary details, cost centers), Save, Open Month, Post Employee, Post Month, Excel template and import, Print, links to the reports. Arabic and right-to-left supported. |
| Salary Slip custom fields | Monthly inputs (overtime hours, late hours, absence days, other deduction and remark), the cost-center table, the distribution totals and difference, the GOSI employer share, and the calculation parameters copied from settings. |
| **Salary Slip Cost Center** (child table) | Cost center, days, days value, overtime hours, overtime value, and "Auto" (a single row follows the salary details automatically). |
| **Payroll Distribution Settings** | Days per month (30), hours per day (8), overtime multiplier, GOSI rates, base cap and nationality, payroll payable account, one journal entry per month or per employee. |
| Salary components | Inclusive Salary, Overtime, Advance Deduction, Other Deduction, GOSI, Lateness, Absence, created on install with their formulas. |
| Employee custom fields | Nationality (drives GOSI), Staff Type (Administrative / Worker), Sponsor (Kafeel). |
| Company custom fields | GOSI Employer Expense Account and GOSI Payable Account, next to Default Payroll Payable Account. |
| Reports | Salary Differences (AR-01), Employees Without Salary (AR-02), Employees Not Distributed (AR-03), Salary Cost by Cost Center (AR-04). |

## How the numbers work

- Daily rate = salary ÷ 30, hourly rate = daily rate ÷ 8, both kept unrounded (20 h = 166.67, not 166.60).
- Inclusive Salary = `base × (30 − days outside joining/relieving dates) ÷ 30`. Joiners and leavers are
  prorated from HRMS's own `payment_days` and `total_working_days`.
- Overtime, lateness and absence are quantity × rate. Other deduction is the amount entered. Advance
  recovery comes from Employee Advance → Additional Salary (standard HRMS).
- GOSI (employee) is a deduction: `min(salary, cap) × employee rate`, for employees of the GOSI nationality.
- GOSI employer share = `min(salary, cap) × employer rate`, calculated on the slip as a company cost. It
  doesn't touch net pay. (HRMS v15 has no employer-contribution components, so the app keeps it on the slip.)
- Cost-center rows: days × daily rate + overtime hours × hourly rate × multiplier.
- **Balancing is on money**: difference = distribution total − net salary. It never blocks; it shows at
  the foot of the cost-center tab and on the Salary Differences report, next to the deductions that can
  explain it (advance, GOSI, lateness, other) and the part they don't explain. A difference up to the
  Difference Tolerance in settings (0.01) is rounding and isn't flagged.
- Salary Cost by Cost Center shows the distribution rows and, beside them, what is in the ledger. The
  two differ where a slip's distribution doesn't cover its whole salary, and by the employer's GOSI.
- A slip with no cost-center rows, or with no days or overtime hours on them while there are days to pay, can't be posted.

## Posting

Post Employee submits one slip and creates its journal entry. Post Month submits every draft slip of
the month (each in its own savepoint, so a refused slip is listed and skipped) and creates one journal
entry for all of them (or one per employee, per settings). Submitting a slip from its own form posts it
the same way. Slips created by a Payroll Entry are left to Payroll Entry: the page doesn't post them, and
Post Month lists them as not posted. A slip with no pay is posted with no journal entry.

The journal entry:

- debits each earning's account per cost center (overtime split by hours, everything else by days)
- credits each deduction's account (profit-and-loss accounts split by days); advance recoveries credit
  the Employee Advance account with a reference to the advance
- debits the GOSI employer share to the company's GOSI Employer Expense Account by days and credits the
  GOSI Payable Account
- credits Payroll Payable with the employee as party for the net

Cancelling a posted slip cancels its journal entry. If that entry also covered other slips, they are
posted again in a new entry automatically. The journal entry can't be cancelled on its own: cancel the
slip. A salary that has been paid can't be cancelled until the payment is. A payment counts if it names
the journal entry, or if the employee's payments on the payable account go beyond what they were owed
before this salary (payments are matched to salaries oldest first).

## Excel template

Download it from Month > Download Excel Template, fill it in, and import it into the same month.

- One row per cost center. A row with no employee code or name continues the employee above it.
- Overtime, late hours, absence and other deduction go on each employee's first row only.
- A single row with its days and hours blank follows the salary details (Auto). Typing a number in
  either turns Auto off for that row.
- The file records its month and is refused on any other month. The same file can be imported again.

## Installation

```bash
cd frappe-bench
bench get-app https://github.com/mhmoud123/payroll_distribution
bench --site <site> install-app payroll_distribution
```

Or from a copy of this folder:

```bash
cd frappe-bench
cp -r /path/to/payroll_distribution apps/
./env/bin/pip install -e apps/payroll_distribution
echo "payroll_distribution" >> sites/apps.txt   # make sure apps.txt ends with a newline first
bench --site <site> install-app payroll_distribution
bench build --app payroll_distribution
```

## Setup checklist (per company)

1. **Salary component accounts**: open each component (Inclusive Salary, Overtime, Absence, Lateness,
   Other Deduction, Advance Deduction, GOSI) and add the company's account.
2. **GOSI accounts on the Company**: set GOSI Employer Expense Account and GOSI Payable Account (Company →
   next to Default Payroll Payable Account). If GOSI Payable Account is empty, the GOSI component's
   account is used.
3. **Salary Structures**: add Inclusive Salary and Overtime to earnings; Other Deduction, GOSI, Lateness and
   Absence to deductions. The formulas are copied from the components. For administrative vs worker
   staff, use two structures whose earnings components point to different expense accounts.
4. **Salary Structure Assignment** for every employee (base = monthly inclusive salary).
5. **Payroll Settings**: Payroll Based On = Leave, Include Holidays in Total Working Days = on. Don't record
   absences as Leave Without Pay as well, or they are deducted twice.
6. **Holiday list**: set a Default Holiday List on the Company, or a Holiday List on each Employee (HRMS v15
   needs one to create slips). On v16, use a Holiday List Assignment instead.
7. **Employee Advances account** must be of type Receivable (HRMS rule; the standard chart makes it Payable).
8. **Payroll Distribution Settings**: set the overtime multiplier and GOSI rates once confirmed with the client.
9. Optional: set Payroll Cost Center on each employee; new slips start from it (or from last month's split).

Install and `bench migrate` give HR User and HR Manager Select permission on Company and Cost Center (a rule
already set for those roles is left as it is): the page and reports need it to pick them, and ERPNext gives
those roles no access.

Good to know on v15: HRMS marks an Employee Advance "Returned" as soon as its Additional Salary is
submitted, before the salary is posted. The page's Outstanding Advance Balance is read from the ledger,
so it still shows the amount until the salary that recovers it is posted.

## Open points to confirm with the client

These are settings, not code: overtime multiplier (1 = legacy; Saudi Labor Law Article 107 suggests
1.5 on basic), GOSI rates, base and cap, and the 30-day month basis.

## Testing

`payroll_distribution/tests/scenario_check.py` runs the acceptance scenarios end to end (salary figures,
cost-center splits, journal entries, post month, cancel and re-post, advance recovery, Excel import,
reports). It creates its own test company data, so run it on a test site only:

```bash
bench --site test.local execute payroll_distribution.tests.scenario_check.setup_test_data --kwargs "{'company': 'Test Contracting Co'}"
bench --site test.local execute payroll_distribution.tests.scenario_check.run --kwargs "{'company': 'Test Contracting Co'}"
```

`run` rolls everything back at the end. `run` works on August and September 2026; `make_demo` leaves an
October 2026 month in place for trying the page.

## Limits

- Salaries in the company currency only.
- Earnings other than Inclusive Salary and Overtime (housing, transport) are posted by days, but the
  cost-center day value is based on the salary structure base only.
- Bank transfer files (WPS, Mudad) and end-of-service provisions are not included.

## License

MIT
