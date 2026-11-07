# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""AR-01: employees whose cost-center total differs from net salary.

Each row shows the deductions that can explain the difference, and what is left unexplained.
An unexplained difference needs fixing; an explained one (an advance, GOSI, lateness) is expected.
"""

import frappe
from frappe import _
from frappe.utils import flt

from payroll_distribution.report_utils import deductions_by_slip, get_slips, status_label, tolerance
from payroll_distribution.utils import get_settings


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	currency = {"fieldtype": "Currency", "width": 120}
	return [
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{"fieldname": "employee_name", "label": _("Employee Name"), "fieldtype": "Data", "width": 170},
		{
			"fieldname": "department",
			"label": _("Department"),
			"fieldtype": "Link",
			"options": "Department",
			"width": 130,
		},
		{
			"fieldname": "salary_slip",
			"label": _("Salary Slip"),
			"fieldtype": "Link",
			"options": "Salary Slip",
			"width": 170,
		},
		{"fieldname": "status", "label": _("Status"), "fieldtype": "Data", "width": 80},
		{"fieldname": "net_pay", "label": _("Net Salary"), **currency},
		{"fieldname": "distribution_total", "label": _("Distribution Total"), **currency},
		{"fieldname": "difference", "label": _("Difference"), **currency},
		{"fieldname": "advance", "label": _("Advance"), **currency},
		{"fieldname": "gosi", "label": _("GOSI"), **currency},
		{"fieldname": "lateness", "label": _("Lateness"), **currency},
		{"fieldname": "other_deduction", "label": _("Other Deductions"), **currency},
		{"fieldname": "unexplained", "label": _("Unexplained Difference"), **currency},
	]


def get_data(filters):
	settings = get_settings()
	limit = tolerance()
	slips = [s for s in get_slips(filters) if abs(flt(s.custom_distribution_difference)) >= limit]
	if filters.get("only_unexplained"):
		slips = [s for s in slips if abs(flt(s.custom_unexplained_difference)) >= limit]

	deductions = deductions_by_slip([s.name for s in slips])
	named = {
		"advance": settings.advance_component,
		"gosi": settings.gosi_component,
		"lateness": settings.lateness_component,
	}
	data = []
	for slip in slips:
		amounts = deductions.get(slip.name, {})
		row = {key: flt(amounts.get(component)) for key, component in named.items()}
		explained_elsewhere = sum(row.values()) + flt(amounts.get(settings.absence_component))
		row["other_deduction"] = flt(slip.total_deduction) - explained_elsewhere
		row.update(
			{
				"employee": slip.employee,
				"employee_name": slip.employee_name,
				"department": slip.department,
				"salary_slip": slip.name,
				"status": status_label(slip.docstatus),
				"net_pay": slip.net_pay,
				"distribution_total": slip.custom_distribution_total,
				"difference": slip.custom_distribution_difference,
				"unexplained": slip.custom_unexplained_difference,
			}
		)
		data.append(row)
	return data
