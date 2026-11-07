# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""AR-04: the month's salary cost per cost center, from the distribution rows."""

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
			"fieldname": "cost_center",
			"label": _("Cost Center"),
			"fieldtype": "Link",
			"options": "Cost Center",
			"width": 200,
		},
		{"fieldname": "employees", "label": _("Employees"), "fieldtype": "Int", "width": 100},
		{"fieldname": "days", "label": _("Days"), "fieldtype": "Float", "width": 90},
		{"fieldname": "basic_amount", "label": _("Days Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "ot_hours", "label": _("Overtime Hours"), "fieldtype": "Float", "width": 120},
		{"fieldname": "ot_amount", "label": _("Overtime Value"), "fieldtype": "Currency", "width": 130},
		{"fieldname": "total", "label": _("Total"), "fieldtype": "Currency", "width": 130},
	]


def get_data(filters):
	docstatus = 1 if filters.get("posted_only") else ("<", 2)
	slips = {s.name: s for s in get_slips(filters, docstatus=docstatus)}
	if not slips:
		return []
	rows = frappe.get_all(
		"Salary Slip Cost Center",
		filters={
			"parenttype": "Salary Slip",
			"parentfield": "custom_cost_centers",
			"parent": ("in", list(slips)),
		},
		fields=["parent", "cost_center", "basic_days", "basic_amount", "ot_hours", "ot_amount", "row_total"],
	)
	totals = {}
	for row in rows:
		entry = totals.setdefault(
			row.cost_center,
			{
				"cost_center": row.cost_center,
				"employees": set(),
				"days": 0.0,
				"basic_amount": 0.0,
				"ot_hours": 0.0,
				"ot_amount": 0.0,
				"total": 0.0,
			},
		)
		entry["employees"].add(slips[row.parent].employee)
		entry["days"] += flt(row.basic_days)
		entry["basic_amount"] += flt(row.basic_amount)
		entry["ot_hours"] += flt(row.ot_hours)
		entry["ot_amount"] += flt(row.ot_amount)
		entry["total"] += flt(row.row_total)
	data = []
	for entry in sorted(totals.values(), key=lambda e: e["cost_center"]):
		entry["employees"] = len(entry["employees"])
		data.append(entry)
	return data
