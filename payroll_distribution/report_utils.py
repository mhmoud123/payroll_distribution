# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""Shared filters and lookups for the audit reports."""

import frappe
from frappe import _
from frappe.utils import cint, flt

from payroll_distribution.utils import get_period, get_settings


def get_filtered_period(filters):
	if not filters.get("company"):
		frappe.throw(_("Choose a company."))
	return get_period(filters.get("month"), filters.get("year"))


def get_slips(filters, docstatus=("<", 2), extra_fields=()):
	"""Salary slips of the month, narrowed by department and staff type."""
	start, end = get_filtered_period(filters)
	slip_filters = {"company": filters.company, "start_date": start, "end_date": end, "docstatus": docstatus}
	if filters.get("department"):
		slip_filters["department"] = filters.department
	slips = frappe.get_all(
		"Salary Slip",
		filters=slip_filters,
		fields=[
			"name",
			"employee",
			"employee_name",
			"department",
			"docstatus",
			"net_pay",
			"total_deduction",
			"custom_payable_days",
			"custom_ot_hours",
			"custom_distributed_days",
			"custom_distributed_ot_hours",
			"custom_distribution_total",
			"custom_distribution_difference",
			"custom_unexplained_difference",
			*extra_fields,
		],
		order_by="employee asc",
	)
	if filters.get("staff_type"):
		allowed = set(
			frappe.get_all(
				"Employee",
				filters={"company": filters.company, "custom_staff_type": filters.staff_type},
				pluck="name",
			)
		)
		slips = [s for s in slips if s.employee in allowed]
	return slips


def deductions_by_slip(slip_names):
	"""{slip: {component: amount}} for the given slips."""
	if not slip_names:
		return {}
	rows = frappe.get_all(
		"Salary Detail",
		filters={"parenttype": "Salary Slip", "parentfield": "deductions", "parent": ("in", slip_names)},
		fields=["parent", "salary_component", "amount"],
	)
	result = {}
	for row in rows:
		result.setdefault(row.parent, {}).setdefault(row.salary_component, 0.0)
		result[row.parent][row.salary_component] += flt(row.amount)
	return result


def tolerance():
	return flt(get_settings().difference_tolerance) or 0.01


def status_label(docstatus):
	return _("Posted") if cint(docstatus) == 1 else _("Draft")
