# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""Journal entries for posted salary slips, split by cost center.

A Salary Slip posts nothing to the ledger on its own in HRMS; normally Payroll Entry does it.
This app doesn't use Payroll Entry, so posting a slip (or a month) creates the entry here:

- earnings: debit each component's account, split by cost center (overtime by hours, the rest by days)
- deductions: credit each component's account; profit-and-loss accounts are split by days, advance
  recoveries credit the Employee Advance account with a reference so HRMS marks the advance returned
- GOSI employer share: debit the company's GOSI expense account by days, credit GOSI payable
- the balance (net pay) is credited to Payroll Payable with the employee as party
"""

import frappe
from frappe import _
from frappe.utils import flt, format_date

from payroll_distribution.utils import get_settings, split_amount


def make_journal_entry(slips):
	"""Create and submit one Journal Entry for the given submitted salary slips (same company)."""
	slips = list(slips)
	if not slips:
		return None
	company = slips[0].company
	if any(s.company != company for s in slips):
		frappe.throw(_("Salary slips of different companies cannot share one journal entry."))

	company_currency = frappe.get_cached_value("Company", company, "default_currency")
	for slip in slips:
		if slip.currency and slip.currency != company_currency:
			frappe.throw(
				_(
					"Salary Slip {0} is in {1}. Only salaries in the company currency ({2}) can be posted from here."
				).format(slip.name, slip.currency, company_currency)
			)

	precision = frappe.get_precision("Journal Entry Account", "debit_in_account_currency") or 2
	accounts = []
	for slip in slips:
		accounts.extend(get_slip_accounts(slip, precision))
	if not accounts:
		return None  # nothing to post, for example a slip with no pay

	je = frappe.new_doc("Journal Entry")
	je.voucher_type = "Journal Entry"
	je.company = company
	je.posting_date = max(s.posting_date for s in slips)
	period = f"{format_date(slips[0].start_date)} - {format_date(slips[0].end_date)}"
	if len(slips) == 1:
		je.title = _("Salary {0} - {1}").format(slips[0].employee_name or slips[0].employee, period)
	else:
		je.title = _("Salaries {0} ({1} employees)").format(period, len(slips))
	je.user_remark = _("Salary slips: {0}").format(", ".join(s.name for s in slips))
	je.set("accounts", accounts)
	je.flags.ignore_permissions = True
	je.insert()
	je.submit()

	for slip in slips:
		frappe.db.set_value("Salary Slip", slip.name, "journal_entry", je.name, update_modified=False)
		slip.journal_entry = je.name
	return je


def get_slip_accounts(slip, precision):
	settings = get_settings()
	company = slip.company
	default_cost_center = frappe.get_cached_value("Company", company, "cost_center")
	rows = slip.get("custom_cost_centers") or []

	day_weights = [(r.cost_center, r.basic_days) for r in rows]
	hour_weights = [(r.cost_center, r.ot_hours) for r in rows]
	value_weights = [(r.cost_center, r.row_total) for r in rows]

	def weights_for(component):
		if component == settings.overtime_component and sum(flt(w) for _c, w in hour_weights) > 0:
			return hour_weights
		for weights in (day_weights, value_weights):
			if sum(flt(w) for _c, w in weights) > 0:
				return weights
		return [(default_cost_center, 1)]

	def row(account, cost_center, debit=0.0, credit=0.0, **extra):
		values = {
			"account": account,
			"cost_center": cost_center,
			"debit_in_account_currency": flt(debit, precision),
			"credit_in_account_currency": flt(credit, precision),
			"user_remark": slip.name,
		}
		values.update(extra)
		return values

	accounts = []
	debit_total = credit_total = 0.0

	for earning in slip.get("earnings") or []:
		if not counts_in_accounts(earning):
			continue
		account = get_component_account(earning.salary_component, company)
		for cost_center, amount in split_amount(
			earning.amount, weights_for(earning.salary_component), precision
		):
			accounts.append(row(account, cost_center, debit=amount))
			debit_total += amount

	advances = get_advance_recoveries(slip)
	for deduction in slip.get("deductions") or []:
		if not counts_in_accounts(deduction):
			continue
		amount = flt(deduction.amount, precision)
		advance = advances.get(deduction.additional_salary)
		if advance:
			accounts.append(
				row(
					advance.advance_account,
					default_cost_center,
					credit=amount,
					party_type="Employee",
					party=slip.employee,
					reference_type="Employee Advance",
					reference_name=advance.employee_advance,
				)
			)
		else:
			account = get_component_account(deduction.salary_component, company)
			if is_profit_and_loss(account):
				for cost_center, part in split_amount(
					amount, weights_for(deduction.salary_component), precision
				):
					accounts.append(row(account, cost_center, credit=part))
			else:
				accounts.append(row(account, default_cost_center, credit=amount))
		credit_total += amount

	employer_gosi = flt(slip.get("custom_gosi_employer_amount"), precision)
	if employer_gosi:
		expense_account, payable_account = get_gosi_employer_accounts(company)
		for cost_center, part in split_amount(employer_gosi, weights_for(None), precision):
			accounts.append(row(expense_account, cost_center, debit=part))
		accounts.append(row(payable_account, default_cost_center, credit=employer_gosi))

	payable = flt(debit_total - credit_total, precision)
	if payable:
		payable_account = get_payroll_payable_account(company)
		accounts.append(
			row(
				payable_account,
				default_cost_center,
				credit=payable if payable > 0 else 0,
				debit=-payable if payable < 0 else 0,
				party_type="Employee",
				party=slip.employee,
			)
		)
	return accounts


def counts_in_accounts(detail):
	"""Same rule Payroll Entry uses: skip zero and statistical lines, and lines kept out of totals and accounts."""
	if not flt(detail.amount) or detail.get("statistical_component"):
		return False
	if detail.get("do_not_include_in_total") and detail.get("do_not_include_in_accounts"):
		return False
	return True


def get_component_account(component, company):
	account = frappe.db.get_value(
		"Salary Component Account",
		{"parent": component, "parenttype": "Salary Component", "company": company},
		"account",
	)
	if not account:
		frappe.throw(
			_("Set the account for salary component {0} for company {1} before posting.").format(
				frappe.bold(component), frappe.bold(company)
			),
			title=_("Missing Account"),
		)
	return account


def get_gosi_employer_accounts(company):
	"""Expense and payable accounts for the employer share of GOSI, from the Company."""
	expense_account, payable_account = frappe.get_cached_value(
		"Company", company, ["custom_gosi_expense_account", "custom_gosi_payable_account"]
	)
	if not payable_account and get_settings().gosi_component:
		payable_account = frappe.db.get_value(
			"Salary Component Account",
			{"parent": get_settings().gosi_component, "parenttype": "Salary Component", "company": company},
			"account",
		)
	for account, label in (
		(expense_account, _("GOSI Employer Expense Account")),
		(payable_account, _("GOSI Payable Account")),
	):
		if not account:
			frappe.throw(
				_("Set the {0} on company {1} before posting the employer share of GOSI.").format(
					frappe.bold(label), frappe.bold(company)
				),
				title=_("Missing Account"),
			)
		if frappe.get_cached_value("Account", account, "company") != company:
			frappe.throw(
				_("{0} {1} does not belong to company {2}.").format(label, frappe.bold(account), company),
				title=_("Wrong Account"),
			)
	return expense_account, payable_account


def get_payroll_payable_account(company):
	account = get_settings().payroll_payable_account
	if not account or frappe.get_cached_value("Account", account, "company") != company:
		account = frappe.get_cached_value("Company", company, "default_payroll_payable_account")
	if not account:
		frappe.throw(
			_(
				"Set the Default Payroll Payable Account for company {0}, or set one in Payroll Distribution Settings."
			).format(frappe.bold(company)),
			title=_("Missing Account"),
		)
	return account


def is_profit_and_loss(account):
	return frappe.get_cached_value("Account", account, "report_type") == "Profit and Loss"


def get_advance_recoveries(slip):
	"""Employee Advances recovered by this slip, keyed by the Additional Salary that recovers them."""
	names = [d.additional_salary for d in slip.get("deductions") or [] if d.get("additional_salary")]
	if not names:
		return {}
	AdditionalSalary = frappe.qb.DocType("Additional Salary")
	EmployeeAdvance = frappe.qb.DocType("Employee Advance")
	rows = (
		frappe.qb.from_(AdditionalSalary)
		.join(EmployeeAdvance)
		.on(AdditionalSalary.ref_docname == EmployeeAdvance.name)
		.select(
			AdditionalSalary.name.as_("additional_salary"),
			EmployeeAdvance.name.as_("employee_advance"),
			EmployeeAdvance.advance_account,
		)
		.where(AdditionalSalary.name.isin(names))
		.where(AdditionalSalary.ref_doctype == "Employee Advance")
	).run(as_dict=True)
	return {r.additional_salary: r for r in rows}


def before_cancel_journal_entry(doc, method=None):
	"""A posted salary's entry goes when its slip is cancelled, which unlinks the slip first. Cancelled
	on its own, HRMS would just unlink the slip and leave it posted with nothing in the ledger."""
	slip = frappe.db.get_value(
		"Salary Slip", {"journal_entry": doc.name, "docstatus": 1, "payroll_entry": ("is", "not set")}
	)
	if slip:
		frappe.throw(
			_("Journal Entry {0} is the posting of Salary Slip {1}. Cancel the salary slip instead.").format(
				frappe.bold(doc.name), frappe.bold(slip)
			),
			title=_("Posted by a Salary Slip"),
		)


def validate_not_paid(je_name, employee):
	"""Refuse to cancel a salary that has been paid: ERPNext would just remove a payment's reference."""
	payment = (
		frappe.db.get_value(
			"Journal Entry Account",
			{
				"reference_type": "Journal Entry",
				"reference_name": je_name,
				"docstatus": 1,
				"parent": ("!=", je_name),
			},
			"parent",
		)
		or frappe.db.get_value(
			"Payment Entry Reference",
			{"reference_doctype": "Journal Entry", "reference_name": je_name, "docstatus": 1},
			"parent",
		)
		or find_payment_by_balance(je_name, employee)
	)
	if payment:
		frappe.throw(
			_("Journal Entry {0} has been paid by {1}. Cancel the payment first.").format(
				frappe.bold(je_name), frappe.bold(payment)
			),
			title=_("Already Paid"),
		)


def find_payment_by_balance(je_name, employee):
	"""A payment covering this salary that doesn't name its journal entry, as an ordinary bank entry doesn't.

	Payments are matched to what the employee is owed oldest first, so this salary is paid once the
	employee's payments go beyond what they were owed before it.
	"""
	account = frappe.db.get_value(
		"Journal Entry Account",
		{
			"parent": je_name,
			"party_type": "Employee",
			"party": employee,
			"reference_type": ("is", "not set"),
		},
		"account",
	)
	if not account:
		return None
	posting_date = frappe.db.get_value("Journal Entry", je_name, "posting_date")
	entries = frappe.get_all(
		"GL Entry",
		filters={"account": account, "party_type": "Employee", "party": employee, "is_cancelled": 0},
		fields=["voucher_type", "voucher_no", "posting_date", "debit", "credit"],
		order_by="posting_date asc, creation asc",
	)
	owed_before = sum(flt(e.credit) for e in entries if e.posting_date < posting_date)
	payments = [
		e
		for e in entries
		if flt(e.debit) and e.voucher_type in ("Journal Entry", "Payment Entry") and e.voucher_no != je_name
	]
	if sum(flt(e.debit) for e in payments) > owed_before + 0.005:
		return payments[-1].voucher_no
	return None


def cancel_journal_entry_for_slip(slip):
	"""Cancel the slip's journal entry. If the entry also covered other slips, post them again in a new entry."""
	je_name = slip.journal_entry or frappe.db.get_value("Salary Slip", slip.name, "journal_entry")
	if not je_name:
		return
	validate_not_paid(je_name, slip.employee)

	others = frappe.get_all(
		"Salary Slip",
		filters={"journal_entry": je_name, "docstatus": 1, "name": ("!=", slip.name)},
		pluck="name",
	)
	# Unlink the slips first, so that the cancel's link check only stops on real dependents such as
	# a payment. The cancelled slip stays unlinked: an amended copy can't link a cancelled entry.
	frappe.db.set_value(
		"Salary Slip", {"journal_entry": je_name}, "journal_entry", None, update_modified=False
	)
	slip.journal_entry = None
	if frappe.db.get_value("Journal Entry", je_name, "docstatus") != 1:
		return

	je = frappe.get_doc("Journal Entry", je_name)
	je.flags.ignore_permissions = True
	je.cancel()

	if others:
		new_je = make_journal_entry([frappe.get_doc("Salary Slip", name) for name in others])
		if new_je:
			frappe.msgprint(
				_(
					"Journal Entry {0} was cancelled. The other {1} salary slips in it were posted again in {2}."
				).format(frappe.bold(je_name), len(others), frappe.bold(new_je.name)),
				alert=True,
			)
