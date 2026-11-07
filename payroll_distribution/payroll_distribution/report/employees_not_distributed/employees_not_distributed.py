# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""AR-03: unposted slips with no cost-center distribution. They can't be posted until they have one."""

import frappe
from frappe import _
from frappe.utils import flt

from payroll_distribution.report_utils import get_slips


def execute(filters=None):
	filters = frappe._dict(filters or {})
	return get_columns(), get_data(filters)


def get_columns():
	return [
		{
			"fieldname": "employee",
			"label": _("Employee"),
			"fieldtype": "Link",
			"options": "Employee",
			"width": 120,
		},
		{"fieldname": "employee_name", "label": _("Employee Name"), "fieldtype": "Data", "width": 180},
		{
			"fieldname": "department",
			"label": _("Department"),
			"fieldtype": "Link",
			"options": "Department",
			"width": 140,
		},
		{
			"fieldname": "salary_slip",
			"label": _("Salary Slip"),
			"fieldtype": "Link",
			"options": "Salary Slip",
			"width": 170,
		},
		{"fieldname": "payable_days", "label": _("Payable Days"), "fieldtype": "Float", "width": 110},
		{"fieldname": "ot_hours", "label": _("Overtime Hours"), "fieldtype": "Float", "width": 120},
		{"fieldname": "net_pay", "label": _("Net Salary"), "fieldtype": "Currency", "width": 120},
	]


def get_data(filters):
	slips = get_slips(filters, docstatus=0)
	names = [s.name for s in slips]
	rows_per_slip = {}
	if names:
		for row in frappe.get_all(
			"Salary Slip Cost Center",
			filters={
				"parenttype": "Salary Slip",
				"parentfield": "custom_cost_centers",
				"parent": ("in", names),
			},
			fields=["parent", "basic_days", "ot_hours"],
		):
			rows_per_slip.setdefault(row.parent, []).append(row)

	data = []
	for slip in slips:
		rows = rows_per_slip.get(slip.name, [])
		if rows and any(flt(r.basic_days) or flt(r.ot_hours) for r in rows):
			continue
		if rows and not flt(slip.custom_payable_days) and not flt(slip.custom_ot_hours):
			continue  # nothing to distribute (for example a full month of absence)
		data.append(
			{
				"employee": slip.employee,
				"employee_name": slip.employee_name,
				"department": slip.department,
				"salary_slip": slip.name,
				"payable_days": slip.custom_payable_days,
				"ot_hours": slip.custom_ot_hours,
				"net_pay": slip.net_pay,
			}
		)
	return data
