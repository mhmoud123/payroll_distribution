# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""AR-04: the month's salary cost per cost center: the distribution rows, and what is in the ledger.

The two differ where a slip's distribution doesn't cover its whole salary, and by the employer's GOSI."""

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
		{"fieldname": "posted_cost", "label": _("Posted in Ledger"), "fieldtype": "Currency", "width": 130},
	]


def get_data(filters):
	docstatus = 1 if filters.get("posted_only") else ("<", 2)
	slips = {s.name: s for s in get_slips(filters, docstatus=docstatus, extra_fields=("journal_entry",))}
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

	def entry_for(cost_center):
		return totals.setdefault(
			cost_center,
			{
				"cost_center": cost_center,
				"employees": set(),
				"days": 0.0,
				"basic_amount": 0.0,
				"ot_hours": 0.0,
				"ot_amount": 0.0,
				"total": 0.0,
				"posted_cost": 0.0,
			},
		)

	for row in rows:
		entry = entry_for(row.cost_center)
		entry["employees"].add(slips[row.parent].employee)
		entry["days"] += flt(row.basic_days)
		entry["basic_amount"] += flt(row.basic_amount)
		entry["ot_hours"] += flt(row.ot_hours)
		entry["ot_amount"] += flt(row.ot_amount)
		entry["total"] += flt(row.row_total)
	for cost_center, amount in get_posted_cost(slips).items():
		entry_for(cost_center)["posted_cost"] = amount
	data = []
	for entry in sorted(totals.values(), key=lambda e: e["cost_center"]):
		entry["employees"] = len(entry["employees"])
		data.append(entry)
	return data


def get_posted_cost(slips):
	"""What the posted slips put on each cost center: expense lines, net of the deductions credited to them."""
	posted = [s for s in slips.values() if s.docstatus == 1 and s.journal_entry]
	if not posted:
		return {}
	lines = frappe.get_all(
		"Journal Entry Account",
		filters={
			"parenttype": "Journal Entry",
			"parent": ("in", list({s.journal_entry for s in posted})),
			"user_remark": ("in", [s.name for s in posted]),
		},
		fields=["account", "cost_center", "debit", "credit"],
	)
	expense_accounts = set(
		frappe.get_all(
			"Account",
			filters={
				"name": ("in", list({line.account for line in lines})),
				"report_type": "Profit and Loss",
			},
			pluck="name",
		)
	)
	result = {}
	for line in lines:
		if line.account in expense_accounts:
			result[line.cost_center] = result.get(line.cost_center, 0.0) + flt(line.debit) - flt(line.credit)
	return result
