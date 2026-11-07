# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""Salary Slip document events.

Order on every save, as Frappe runs them:
1. before_validate (here): copy settings onto the slip so salary component formulas can use them.
2. Salary Slip.validate (HRMS): working days, payment days, every component, net pay.
3. validate (here): rates, payable days, cost-center amounts and the difference against net pay.
"""

import frappe
from frappe import _
from frappe.utils import flt

from payroll_distribution.utils import (
	component_amount,
	get_advance_balance,
	get_default_cost_center,
	get_salary_base,
	get_settings,
	is_valid_cost_center,
)

FLOAT_EPSILON = 1e-6


def before_insert(doc, method=None):
	if not doc.get("custom_cost_centers"):
		prefill_cost_centers(doc)


def before_validate(doc, method=None):
	# Submitted slips keep the parameters they were calculated with.
	if doc.docstatus != 0:
		return
	settings = get_settings()
	doc.custom_days_basis = settings.days_per_month or 30
	doc.custom_hours_per_day = flt(settings.hours_per_day) or 8
	doc.custom_ot_multiplier = flt(settings.overtime_multiplier) or 1

	nationality = frappe.db.get_value("Employee", doc.employee, "custom_nationality")
	doc.custom_gosi_applicable = 1 if settings.gosi_country and nationality == settings.gosi_country else 0
	doc.custom_gosi_employee_rate = flt(settings.gosi_employee_rate)
	doc.custom_gosi_employer_rate = flt(settings.gosi_employer_rate)
	doc.custom_gosi_salary_cap = flt(settings.gosi_salary_cap)


def validate(doc, method=None):
	if doc.docstatus == 2:
		return
	precision = doc.precision("net_pay") or 2

	days_basis = flt(doc.custom_days_basis) or 30
	hours_per_day = flt(doc.custom_hours_per_day) or 8
	multiplier = flt(doc.custom_ot_multiplier) or 1

	base = get_base(doc)
	daily_rate = base / days_basis
	hourly_rate = daily_rate / hours_per_day
	doc.custom_daily_rate = flt(daily_rate, 6)
	doc.custom_hourly_rate = flt(hourly_rate, 6)

	validate_inputs(doc, days_basis)

	missed_days = max(flt(doc.total_working_days) - flt(doc.payment_days), 0)
	doc.custom_payable_days = max(days_basis - missed_days - flt(doc.custom_absent_days), 0)

	rows = doc.get("custom_cost_centers") or []
	if len(rows) == 1 and rows[0].follow_salary_details:
		rows[0].basic_days = doc.custom_payable_days
		rows[0].ot_hours = flt(doc.custom_ot_hours)
	elif len(rows) > 1:
		for row in rows:
			row.follow_salary_details = 0

	validate_cost_centers(doc, days_basis)

	for row in rows:
		row.basic_amount = flt(flt(row.basic_days) * daily_rate, precision)
		row.ot_amount = flt(flt(row.ot_hours) * hourly_rate * multiplier, precision)
		row.row_total = flt(row.basic_amount + row.ot_amount, precision)

	doc.custom_distributed_days = sum(flt(r.basic_days) for r in rows)
	doc.custom_distributed_ot_hours = sum(flt(r.ot_hours) for r in rows)
	doc.custom_distribution_total = flt(sum(flt(r.row_total) for r in rows), precision)
	doc.custom_distribution_difference = flt(doc.custom_distribution_total - flt(doc.net_pay), precision)

	# Deductions explain a difference, except absence: absent days are already left out of the split.
	absence = component_amount(doc, get_settings().absence_component)
	doc.custom_unexplained_difference = flt(
		doc.custom_distribution_difference - (flt(doc.total_deduction) - absence), precision
	)

	if doc.docstatus == 0:
		doc.custom_advance_balance = get_advance_balance(doc.employee)


def before_submit(doc, method=None):
	if not doc.get("custom_cost_centers"):
		frappe.throw(
			_(
				"Salary Slip {0} for {1} has no cost center distribution. Add at least one cost center before posting."
			).format(doc.name, doc.employee_name or doc.employee),
			title=_("Not Distributed"),
		)


def on_submit(doc, method=None):
	"""Post the slip's journal entry when it is submitted on its own (outside Payroll Entry and Post Month)."""
	if doc.payroll_entry or frappe.flags.get("payroll_distribution_batch"):
		return
	from payroll_distribution.accounting import make_journal_entry

	make_journal_entry([doc])


def on_cancel(doc, method=None):
	if doc.payroll_entry:
		return
	from payroll_distribution.accounting import cancel_journal_entry_for_slip

	cancel_journal_entry_for_slip(doc)


def get_base(doc):
	assignment = getattr(doc, "_salary_structure_assignment", None)
	if assignment and flt(assignment.get("base")):
		return flt(assignment.get("base"))
	return get_salary_base(doc.employee, doc.end_date or doc.start_date, doc.salary_structure)


def validate_inputs(doc, days_basis):
	for field in ("custom_ot_hours", "custom_late_hours", "custom_absent_days", "custom_other_deduction"):
		if flt(doc.get(field)) < 0:
			frappe.throw(_("{0} cannot be negative.").format(_(doc.meta.get_label(field))))
	if flt(doc.custom_absent_days) > days_basis + FLOAT_EPSILON:
		frappe.throw(_("Absence Days cannot be more than {0}.").format(days_basis))


def validate_cost_centers(doc, days_basis):
	seen = set()
	total_days = 0.0
	for row in doc.get("custom_cost_centers") or []:
		if not row.cost_center:
			frappe.throw(_("Row {0}: choose a cost center.").format(row.idx))
		if row.cost_center in seen:
			frappe.throw(
				_("Row {0}: cost center {1} is already in the distribution. Merge the rows.").format(
					row.idx, frappe.bold(row.cost_center)
				)
			)
		seen.add(row.cost_center)
		if not is_valid_cost_center(row.cost_center, doc.company):
			frappe.throw(
				_("Row {0}: {1} must be an active, non-group cost center of company {2}.").format(
					row.idx, frappe.bold(row.cost_center), doc.company
				)
			)
		if flt(row.basic_days) < 0 or flt(row.ot_hours) < 0:
			frappe.throw(_("Row {0}: days and overtime hours cannot be negative.").format(row.idx))
		if flt(row.basic_days) > days_basis + FLOAT_EPSILON:
			frappe.throw(_("Row {0}: days cannot be more than {1}.").format(row.idx, days_basis))
		total_days += flt(row.basic_days)
	if total_days > days_basis + FLOAT_EPSILON:
		frappe.throw(
			_("Distributed days ({0}) cannot be more than {1}.").format(flt(total_days, 2), days_basis)
		)


def prefill_cost_centers(doc):
	"""Start from last month's split, or from the employee's default payroll cost center."""
	last_slip = frappe.get_all(
		"Salary Slip",
		filters={
			"employee": doc.employee,
			"company": doc.company,
			"docstatus": 1,
			"end_date": ("<", doc.start_date),
		},
		order_by="end_date desc",
		limit=1,
		pluck="name",
	)
	rows = []
	if last_slip:
		rows = frappe.get_all(
			"Salary Slip Cost Center",
			filters={
				"parent": last_slip[0],
				"parenttype": "Salary Slip",
				"parentfield": "custom_cost_centers",
			},
			fields=["cost_center", "basic_days"],
			order_by="idx asc",
		)
		rows = [r for r in rows if is_valid_cost_center(r.cost_center, doc.company)]

	if rows:
		single = len(rows) == 1
		for r in rows:
			doc.append(
				"custom_cost_centers",
				{
					"cost_center": r.cost_center,
					"basic_days": r.basic_days,
					"ot_hours": 0,
					"follow_salary_details": 1 if single else 0,
				},
			)
		return

	cost_center = get_default_cost_center(doc.employee, doc.company)
	if cost_center and is_valid_cost_center(cost_center, doc.company):
		doc.append("custom_cost_centers", {"cost_center": cost_center, "follow_salary_details": 1})
