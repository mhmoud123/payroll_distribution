# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe.custom.doctype.custom_field.custom_field import create_custom_fields


def after_install():
	create_custom_fields(get_custom_fields(), ignore_validate=True)
	create_salary_components()
	set_default_settings()


def after_migrate():
	create_custom_fields(get_custom_fields(), ignore_validate=True)


def before_uninstall():
	for doctype, fields in get_custom_fields().items():
		for df in fields:
			name = frappe.db.get_value("Custom Field", {"dt": doctype, "fieldname": df["fieldname"]})
			if name:
				frappe.delete_doc("Custom Field", name, ignore_permissions=True, force=True)
	frappe.clear_cache()


def get_custom_fields():
	return {
		"Salary Slip": [
			# Monthly inputs, shown above earnings and deductions
			{
				"fieldname": "custom_monthly_inputs_section",
				"fieldtype": "Section Break",
				"label": "Monthly Inputs",
				"insert_after": "base_hour_rate",
			},
			{
				"fieldname": "custom_ot_hours",
				"fieldtype": "Float",
				"label": "Overtime Hours",
				"non_negative": 1,
				"insert_after": "custom_monthly_inputs_section",
			},
			{
				"fieldname": "custom_late_hours",
				"fieldtype": "Float",
				"label": "Late Hours",
				"non_negative": 1,
				"insert_after": "custom_ot_hours",
			},
			{
				"fieldname": "custom_absent_days",
				"fieldtype": "Float",
				"label": "Absence Days",
				"non_negative": 1,
				"insert_after": "custom_late_hours",
			},
			{
				"fieldname": "custom_column_break_inputs",
				"fieldtype": "Column Break",
				"insert_after": "custom_absent_days",
			},
			{
				"fieldname": "custom_other_deduction",
				"fieldtype": "Currency",
				"label": "Other Deduction",
				"non_negative": 1,
				"options": "currency",
				"insert_after": "custom_column_break_inputs",
			},
			{
				"fieldname": "custom_other_deduction_remark",
				"fieldtype": "Small Text",
				"label": "Other Deduction Remark",
				"insert_after": "custom_other_deduction",
			},
			{
				"fieldname": "custom_advance_balance",
				"fieldtype": "Currency",
				"label": "Outstanding Advance Balance",
				"options": "currency",
				"read_only": 1,
				"no_copy": 1,
				"insert_after": "custom_other_deduction_remark",
			},
			# Cost center distribution tab
			{
				"fieldname": "custom_cost_centers_tab",
				"fieldtype": "Tab Break",
				"label": "Cost Centers",
				"insert_after": "leave_details",
			},
			{
				"fieldname": "custom_cost_centers",
				"fieldtype": "Table",
				"label": "Cost Center Distribution",
				"options": "Salary Slip Cost Center",
				"insert_after": "custom_cost_centers_tab",
			},
			{
				"fieldname": "custom_distribution_summary_section",
				"fieldtype": "Section Break",
				"label": "Distribution Summary",
				"insert_after": "custom_cost_centers",
			},
			{
				"fieldname": "custom_payable_days",
				"fieldtype": "Float",
				"label": "Payable Days",
				"read_only": 1,
				"insert_after": "custom_distribution_summary_section",
			},
			{
				"fieldname": "custom_distributed_days",
				"fieldtype": "Float",
				"label": "Distributed Days",
				"read_only": 1,
				"insert_after": "custom_payable_days",
			},
			{
				"fieldname": "custom_distributed_ot_hours",
				"fieldtype": "Float",
				"label": "Distributed Overtime Hours",
				"read_only": 1,
				"insert_after": "custom_distributed_days",
			},
			{
				"fieldname": "custom_column_break_summary",
				"fieldtype": "Column Break",
				"insert_after": "custom_distributed_ot_hours",
			},
			{
				"fieldname": "custom_distribution_total",
				"fieldtype": "Currency",
				"label": "Distribution Total",
				"options": "currency",
				"read_only": 1,
				"insert_after": "custom_column_break_summary",
			},
			{
				"fieldname": "custom_distribution_difference",
				"fieldtype": "Currency",
				"label": "Difference (Distribution - Net Pay)",
				"options": "currency",
				"read_only": 1,
				"insert_after": "custom_distribution_total",
			},
			{
				"fieldname": "custom_unexplained_difference",
				"fieldtype": "Currency",
				"label": "Difference Not Explained by Deductions",
				"options": "currency",
				"read_only": 1,
				"insert_after": "custom_distribution_difference",
			},
			{
				"fieldname": "custom_parameters_section",
				"fieldtype": "Section Break",
				"label": "Calculation Parameters",
				"collapsible": 1,
				"insert_after": "custom_unexplained_difference",
			},
			{
				"fieldname": "custom_days_basis",
				"fieldtype": "Float",
				"label": "Days Basis",
				"read_only": 1,
				"insert_after": "custom_parameters_section",
			},
			{
				"fieldname": "custom_hours_per_day",
				"fieldtype": "Float",
				"label": "Hours per Day",
				"read_only": 1,
				"insert_after": "custom_days_basis",
			},
			{
				"fieldname": "custom_ot_multiplier",
				"fieldtype": "Float",
				"label": "Overtime Multiplier",
				"read_only": 1,
				"insert_after": "custom_hours_per_day",
			},
			{
				"fieldname": "custom_daily_rate",
				"fieldtype": "Float",
				"label": "Daily Rate",
				"precision": "6",
				"read_only": 1,
				"insert_after": "custom_ot_multiplier",
			},
			{
				"fieldname": "custom_hourly_rate",
				"fieldtype": "Float",
				"label": "Hourly Rate",
				"precision": "6",
				"read_only": 1,
				"insert_after": "custom_daily_rate",
			},
			{
				"fieldname": "custom_column_break_params",
				"fieldtype": "Column Break",
				"insert_after": "custom_hourly_rate",
			},
			{
				"fieldname": "custom_gosi_applicable",
				"fieldtype": "Check",
				"label": "GOSI Applicable",
				"read_only": 1,
				"insert_after": "custom_column_break_params",
			},
			{
				"fieldname": "custom_gosi_employee_rate",
				"fieldtype": "Percent",
				"label": "GOSI Employee Rate",
				"read_only": 1,
				"insert_after": "custom_gosi_applicable",
			},
			{
				"fieldname": "custom_gosi_employer_rate",
				"fieldtype": "Percent",
				"label": "GOSI Employer Rate",
				"read_only": 1,
				"insert_after": "custom_gosi_employee_rate",
			},
			{
				"fieldname": "custom_gosi_salary_cap",
				"fieldtype": "Currency",
				"label": "GOSI Salary Cap",
				"read_only": 1,
				"insert_after": "custom_gosi_employer_rate",
			},
			{
				"fieldname": "custom_gosi_employer_amount",
				"fieldtype": "Currency",
				"label": "GOSI Employer Share",
				"options": "currency",
				"read_only": 1,
				"description": "Company cost, not deducted from the employee. Posted to the GOSI accounts set on the Company.",
				"insert_after": "custom_gosi_salary_cap",
			},
		],
		"Company": [
			{
				"fieldname": "custom_gosi_expense_account",
				"fieldtype": "Link",
				"label": "GOSI Employer Expense Account",
				"options": "Account",
				"description": "Debited with the employer share of GOSI, split by cost center.",
				"insert_after": "default_payroll_payable_account",
			},
			{
				"fieldname": "custom_gosi_payable_account",
				"fieldtype": "Link",
				"label": "GOSI Payable Account",
				"options": "Account",
				"description": "Credited with the employer share of GOSI. Leave empty to use the GOSI salary component's account.",
				"insert_after": "custom_gosi_expense_account",
			},
		],
		"Employee": [
			{
				"fieldname": "custom_nationality",
				"fieldtype": "Link",
				"label": "Nationality",
				"options": "Country",
				"insert_after": "gender",
			},
			{
				"fieldname": "custom_staff_type",
				"fieldtype": "Select",
				"label": "Staff Type",
				"options": "\nAdministrative\nWorker",
				"insert_after": "designation",
			},
			{
				"fieldname": "custom_sponsor",
				"fieldtype": "Data",
				"label": "Sponsor (Kafeel)",
				"insert_after": "custom_staff_type",
			},
		],
	}


# Formulas use fields the app fills on every Salary Slip save (see overrides/salary_slip.py):
# custom_days_basis, custom_hours_per_day, custom_ot_multiplier and the GOSI fields come from
# Payroll Distribution Settings; total_working_days and payment_days are standard HRMS fields.
# HRMS can evaluate formulas with no slip behind them (v16 does on Salary Structure Assignment
# submit), so the slip fields read as 0 there; the `or` fallbacks keep that from failing.
# HRMS v15 formulas can't call min() or max(), so the formulas use conditional expressions instead.
DAYS = "(custom_days_basis or 30)"
HOURLY = f"base / ({DAYS} * (custom_hours_per_day or 8))"
MISSED_DAYS = "(total_working_days - payment_days)"
GOSI_BASE = "(custom_gosi_salary_cap if custom_gosi_salary_cap and base > custom_gosi_salary_cap else base)"

SALARY_COMPONENTS = [
	{
		"salary_component": "Inclusive Salary",
		"salary_component_abbr": "IS",
		"type": "Earning",
		"formula": f"base * ({DAYS} - {MISSED_DAYS}) / {DAYS} if {DAYS} > {MISSED_DAYS} else 0",
		"description": "Monthly salary on a fixed days-per-month basis. Days before joining or after relieving are left out.",
	},
	{
		"salary_component": "Overtime",
		"salary_component_abbr": "OT",
		"type": "Earning",
		"formula": f"custom_ot_hours * {HOURLY} * (custom_ot_multiplier or 1)",
		"description": "Overtime hours x hourly rate x overtime multiplier.",
	},
	{
		"salary_component": "Advance Deduction",
		"salary_component_abbr": "ADV",
		"type": "Deduction",
		"formula": "",
		"description": "Filled by the Additional Salary that Employee Advance creates when it is repaid from salary.",
	},
	{
		"salary_component": "Other Deduction",
		"salary_component_abbr": "OTH",
		"type": "Deduction",
		"formula": "custom_other_deduction",
		"description": "Amount entered on the Monthly Salary Entry page.",
	},
	{
		"salary_component": "GOSI",
		"salary_component_abbr": "GOSI",
		"type": "Deduction",
		"condition": "custom_gosi_applicable",
		"formula": f"{GOSI_BASE} * custom_gosi_employee_rate / 100",
		"description": "Employee share of GOSI for employees of the GOSI nationality.",
	},
	{
		"salary_component": "Lateness",
		"salary_component_abbr": "LATE",
		"type": "Deduction",
		"formula": f"custom_late_hours * {HOURLY}",
		"description": "Late hours x hourly rate.",
	},
	{
		"salary_component": "Absence",
		"salary_component_abbr": "ABS",
		"type": "Deduction",
		"formula": f"custom_absent_days * base / {DAYS}",
		"description": "Absence days x daily rate.",
	},
]


def create_salary_components(update_existing=False):
	"""Create the salary components the entry page relies on.

	Existing components are left as they are, unless `update_existing` is set, which rewrites their
	condition and formula to the app's (structures keep their own copies until edited)."""
	created = []
	for spec in SALARY_COMPONENTS:
		if frappe.db.exists("Salary Component", spec["salary_component"]):
			if update_existing:
				doc = frappe.get_doc("Salary Component", spec["salary_component"])
				doc.condition = spec.get("condition") or ""
				doc.formula = spec["formula"]
				doc.amount_based_on_formula = 1 if spec["formula"] else 0
				doc.save(ignore_permissions=True)
			continue
		if frappe.db.exists("Salary Component", {"salary_component_abbr": spec["salary_component_abbr"]}):
			continue
		doc = frappe.new_doc("Salary Component")
		doc.update(
			{
				"salary_component": spec["salary_component"],
				"salary_component_abbr": spec["salary_component_abbr"],
				"type": spec["type"],
				"description": spec["description"],
				"depends_on_payment_days": 0,
				"is_tax_applicable": 0,
				"remove_if_zero_valued": 0,
				"condition": spec.get("condition") or "",
				"amount_based_on_formula": 1 if spec["formula"] else 0,
				"formula": spec["formula"],
			}
		)
		doc.insert(ignore_permissions=True)
		created.append(doc.name)
	return created


def set_default_settings():
	settings = frappe.get_single("Payroll Distribution Settings")
	defaults = {
		"days_per_month": 30,
		"hours_per_day": 8,
		"overtime_multiplier": 1,
		"difference_tolerance": 0.01,
		"basic_component": "Inclusive Salary",
		"overtime_component": "Overtime",
		"absence_component": "Absence",
		"lateness_component": "Lateness",
		"other_deduction_component": "Other Deduction",
		"advance_component": "Advance Deduction",
		"gosi_component": "GOSI",
		"gosi_salary_cap": 45000,
		"journal_entry_per": "Month",
	}
	defaults["gosi_country"] = "Saudi Arabia"
	meta = frappe.get_meta("Payroll Distribution Settings")
	for field, value in defaults.items():
		if settings.get(field):
			continue
		df = meta.get_field(field)
		if df.fieldtype == "Link" and not frappe.db.exists(df.options, value):
			continue
		settings.set(field, value)
	settings.flags.ignore_permissions = True
	settings.save()
