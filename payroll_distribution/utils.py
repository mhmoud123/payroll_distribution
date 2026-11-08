# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.query_builder.functions import Sum
from frappe.utils import cint, flt, get_last_day, getdate


def get_settings():
	return frappe.get_cached_doc("Payroll Distribution Settings")


def get_period(month, year):
	"""First and last day of a calendar month."""
	month, year = cint(month), cint(year)
	if not 1 <= month <= 12 or year < 2000:
		frappe.throw(_("Choose a valid month and year."))
	start = getdate(f"{year:04d}-{month:02d}-01")
	return start, getdate(get_last_day(start))


def get_salary_base(employee, on_date, salary_structure=None):
	"""Monthly base from the employee's latest submitted Salary Structure Assignment."""
	filters = {"employee": employee, "docstatus": 1, "from_date": ("<=", on_date)}
	if salary_structure:
		filters["salary_structure"] = salary_structure
	return flt(frappe.db.get_value("Salary Structure Assignment", filters, "base", order_by="from_date desc"))


def get_advance_balance(employee):
	"""What the employee still owes on Employee Advances: paid out, less claimed and returned.

	Read as the employee's balance on their advance accounts in the ledger, not from the advance's
	return amount: HRMS v15 counts an advance as returned as soon as its Additional Salary is
	submitted, before the salary that recovers it is posted."""
	accounts = frappe.get_all(
		"Employee Advance",
		filters={"employee": employee, "docstatus": 1},
		pluck="advance_account",
		distinct=True,
	)
	accounts = [a for a in accounts if a]
	if not accounts:
		return 0.0
	GLEntry = frappe.qb.DocType("GL Entry")
	balance = (
		frappe.qb.from_(GLEntry)
		.select(Sum(GLEntry.debit) - Sum(GLEntry.credit))
		.where(GLEntry.party_type == "Employee")
		.where(GLEntry.party == employee)
		.where(GLEntry.account.isin(accounts))
		.where(GLEntry.is_cancelled == 0)
	).run()
	return flt(balance[0][0]) if balance else 0.0


def get_default_cost_center(employee, company):
	"""Employee's payroll cost center, then the department's, then the company's."""
	cost_center, department = frappe.db.get_value("Employee", employee, ["payroll_cost_center", "department"])
	if not cost_center and department:
		cost_center = frappe.db.get_value("Department", department, "payroll_cost_center")
	if not cost_center:
		cost_center = frappe.get_cached_value("Company", company, "cost_center")
	return cost_center


def is_valid_cost_center(cost_center, company):
	values = frappe.db.get_value(
		"Cost Center", cost_center, ["company", "is_group", "disabled"], as_dict=True
	)
	return bool(values and values.company == company and not values.is_group and not values.disabled)


def component_amount(doc, component, parentfield="deductions"):
	if not component:
		return 0.0
	return sum(flt(d.amount) for d in doc.get(parentfield) or [] if d.salary_component == component)


def split_amount(amount, weights, precision=2):
	"""Split `amount` across `weights` [(key, weight)]. The shares add up to exactly `amount`.

	Each share is taken from what is left, so none can go negative. A share that rounds to nothing is
	left out: a journal entry refuses a line with no amount."""
	weights = [(k, flt(w)) for k, w in weights if flt(w) > 0]
	remaining = flt(amount, precision)
	remaining_weight = sum(w for _k, w in weights)
	result = []
	for i, (key, weight) in enumerate(weights):
		is_last = i == len(weights) - 1
		share = remaining if is_last else flt(remaining * weight / remaining_weight, precision)
		remaining = flt(remaining - share, precision)
		remaining_weight -= weight
		if share:
			result.append((key, share))
	return result
