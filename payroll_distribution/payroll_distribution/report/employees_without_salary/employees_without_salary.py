# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""AR-02: active employees with no salary slip this month."""

import frappe
from frappe import _

from payroll_distribution.report_utils import get_filtered_period


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
			"fieldname": "designation",
			"label": _("Designation"),
			"fieldtype": "Link",
			"options": "Designation",
			"width": 130,
		},
		{"fieldname": "staff_type", "label": _("Staff Type"), "fieldtype": "Data", "width": 110},
		{"fieldname": "date_of_joining", "label": _("Date of Joining"), "fieldtype": "Date", "width": 110},
		{"fieldname": "relieving_date", "label": _("Relieving Date"), "fieldtype": "Date", "width": 110},
		{
			"fieldname": "has_salary_structure",
			"label": _("Has Salary Structure"),
			"fieldtype": "Data",
			"width": 140,
		},
	]


def get_data(filters):
	start, end = get_filtered_period(filters)
	Employee = frappe.qb.DocType("Employee")
	query = (
		frappe.qb.from_(Employee)
		.select(
			Employee.name.as_("employee"),
			Employee.employee_name,
			Employee.department,
			Employee.designation,
			Employee.custom_staff_type.as_("staff_type"),
			Employee.date_of_joining,
			Employee.relieving_date,
		)
		.where(
			(Employee.company == filters.company)
			& (Employee.status != "Inactive")
			& ((Employee.date_of_joining <= end) | Employee.date_of_joining.isnull())
			& ((Employee.relieving_date >= start) | Employee.relieving_date.isnull())
		)
		.orderby(Employee.name)
	)
	if filters.get("department"):
		query = query.where(Employee.department == filters.department)
	if filters.get("staff_type"):
		query = query.where(Employee.custom_staff_type == filters.staff_type)
	employees = query.run(as_dict=True)

	with_slip = set(
		frappe.get_all(
			"Salary Slip",
			filters={"company": filters.company, "start_date": start, "end_date": end, "docstatus": ("<", 2)},
			pluck="employee",
		)
	)
	with_structure = set(
		frappe.get_all(
			"Salary Structure Assignment",
			filters={"company": filters.company, "docstatus": 1, "from_date": ("<=", end)},
			pluck="employee",
		)
	)
	data = []
	for row in employees:
		if row.employee in with_slip:
			continue
		row["has_salary_structure"] = _("Yes") if row.employee in with_structure else _("No")
		data.append(row)
	return data
