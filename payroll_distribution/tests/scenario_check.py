# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""End-to-end check of the acceptance scenarios from the requirements document.

Run on a TEST site only, after the ERPNext setup wizard:

    bench --site test.local execute payroll_distribution.tests.scenario_check.setup_test_data \
        --kwargs "{'company': 'Test Contracting Co'}"
    bench --site test.local execute payroll_distribution.tests.scenario_check.run \
        --kwargs "{'company': 'Test Contracting Co'}"

`setup_test_data` creates (and commits) cost centers, accounts, a holiday list, a salary structure and
four employees on a 2,000 salary. `run` works on August 2026, prints PASS/FAIL per check and rolls
everything back at the end, so it can be run again.
"""

import copy
import io
import json
from unittest.mock import patch

import frappe
from frappe.utils import flt
from openpyxl import load_workbook

from payroll_distribution import api
from payroll_distribution.accounting import make_journal_entry
from payroll_distribution.utils import split_amount

YEAR, MONTH = 2026, 8
BASE = 2000
EMPLOYEES = {
	"nonsaudi": ("PD Test Ahmed", "India"),
	"saudi": ("PD Test Fahad", "Saudi Arabia"),
	"spare": ("PD Test Omar", "Egypt"),
	"extra": ("PD Test Khaled", "Egypt"),
}


# ------------------------------------------------------------------ setup


def setup_test_data(company):
	from payroll_distribution.setup.install import create_salary_components

	create_salary_components(update_existing=True)
	abbr = frappe.get_cached_value("Company", company, "abbr")
	root_cc = frappe.db.get_value(
		"Cost Center", {"company": company, "is_group": 1, "parent_cost_center": ("is", "not set")}
	)

	for name in ("PD Project A", "PD Project B"):
		if not frappe.db.exists("Cost Center", f"{name} - {abbr}"):
			frappe.get_doc(
				{
					"doctype": "Cost Center",
					"cost_center_name": name,
					"company": company,
					"parent_cost_center": root_cc,
					"is_group": 0,
				}
			).insert(ignore_permissions=True)

	gosi_payable = ensure_account(
		company, "PD GOSI Payable", parent_name="Duties and Taxes", root_type="Liability"
	)
	# HRMS wants the employee advance account to be Receivable (the standard chart makes it Payable)
	advance_account = frappe.get_cached_value("Company", company, "default_employee_advance_account")
	if advance_account:
		frappe.db.set_value("Account", advance_account, "account_type", "Receivable")
	salary = f"Salary - {abbr}"

	accounts = {
		"Inclusive Salary": salary,
		"Overtime": salary,
		"Absence": salary,
		"Lateness": salary,
		"Other Deduction": salary,
		"Advance Deduction": f"Employee Advances - {abbr}",
		"GOSI": gosi_payable,
	}
	for component, account in accounts.items():
		doc = frappe.get_doc("Salary Component", component)
		doc.set("accounts", [r for r in doc.accounts if r.company != company])
		doc.append("accounts", {"company": company, "account": account})
		doc.save(ignore_permissions=True)
	# Employer share of GOSI: expense on the salary account, payable on the GOSI account
	frappe.db.set_value(
		"Company",
		company,
		{"custom_gosi_expense_account": salary, "custom_gosi_payable_account": gosi_payable},
	)

	holiday_list = "PD Test Holidays 2026"
	if not frappe.db.exists("Holiday List", holiday_list):
		frappe.get_doc(
			{
				"doctype": "Holiday List",
				"holiday_list_name": holiday_list,
				"from_date": "2026-01-01",
				"to_date": "2026-12-31",
			}
		).insert(ignore_permissions=True)
	frappe.db.set_value("Company", company, "default_holiday_list", holiday_list)
	# HRMS v15 reads the company's default holiday list; v16 looks it up through Holiday List Assignment
	if frappe.db.exists("DocType", "Holiday List Assignment") and not frappe.db.exists(
		"Holiday List Assignment", {"applicable_for": "Company", "assigned_to": company, "docstatus": 1}
	):
		assignment = frappe.get_doc(
			{
				"doctype": "Holiday List Assignment",
				"applicable_for": "Company",
				"assigned_to": company,
				"holiday_list": holiday_list,
				"from_date": "2026-01-01",
			}
		)
		assignment.insert(ignore_permissions=True)
		assignment.submit()

	payroll_settings = frappe.get_single("Payroll Settings")
	payroll_settings.payroll_based_on = "Leave"
	payroll_settings.include_holidays_in_total_working_days = 1
	payroll_settings.email_salary_slip_to_employee = 0
	payroll_settings.save(ignore_permissions=True)

	settings = frappe.get_single("Payroll Distribution Settings")
	settings.gosi_employee_rate = 9.75
	settings.gosi_employer_rate = 11.75
	settings.save(ignore_permissions=True)

	structure_name = "PD Test Structure"
	if not frappe.db.exists("Salary Structure", structure_name):
		structure = frappe.new_doc("Salary Structure")
		structure.name = structure_name
		structure.__newname = structure_name
		structure.company = company
		structure.payroll_frequency = "Monthly"
		structure.currency = frappe.get_cached_value("Company", company, "default_currency")
		for component in ("Inclusive Salary", "Overtime"):
			structure.append("earnings", structure_row(component))
		for component in ("Other Deduction", "GOSI", "Lateness", "Absence"):
			structure.append("deductions", structure_row(component))
		structure.insert(ignore_permissions=True)
		structure.submit()

	for key, (full_name, country) in EMPLOYEES.items():
		employee = frappe.db.get_value("Employee", {"employee_name": full_name, "company": company})
		if not employee:
			first, last = full_name.rsplit(" ", 1)
			doc = frappe.get_doc(
				{
					"doctype": "Employee",
					"first_name": first,
					"last_name": last,
					"gender": "Male",
					"date_of_birth": "1990-01-01",
					"date_of_joining": "2026-01-01",
					"company": company,
					"status": "Active",
					"custom_nationality": country,
					"custom_staff_type": "Worker",
				}
			).insert(ignore_permissions=True)
			employee = doc.name
		if key != "extra" and not frappe.db.exists(
			"Salary Structure Assignment", {"employee": employee, "docstatus": 1}
		):
			assignment = frappe.get_doc(
				{
					"doctype": "Salary Structure Assignment",
					"employee": employee,
					"salary_structure": structure_name,
					"company": company,
					"from_date": "2026-01-01",
					"base": BASE,
				}
			)
			assignment.insert(ignore_permissions=True)
			assignment.submit()

	frappe.db.commit()
	print("Test data ready for", company)


def ensure_account(company, account_name, parent_name, root_type):
	abbr = frappe.get_cached_value("Company", company, "abbr")
	name = f"{account_name} - {abbr}"
	if not frappe.db.exists("Account", name):
		frappe.get_doc(
			{
				"doctype": "Account",
				"account_name": account_name,
				"company": company,
				"parent_account": f"{parent_name} - {abbr}",
				"root_type": root_type,
				"is_group": 0,
			}
		).insert(ignore_permissions=True)
	return name


def structure_row(component):
	values = frappe.db.get_value(
		"Salary Component",
		component,
		["condition", "formula", "amount_based_on_formula", "salary_component_abbr"],
		as_dict=True,
	)
	return {
		"salary_component": component,
		"abbr": values.salary_component_abbr,
		"condition": values.condition,
		"formula": values.formula,
		"amount_based_on_formula": values.amount_based_on_formula,
	}


# ------------------------------------------------------------------ checks

RESULTS = []


def check(label, condition, detail=""):
	RESULTS.append((label, bool(condition)))
	print(("PASS  " if condition else "FAIL  ") + label + (f"   [{detail}]" if detail else ""))


def close(a, b, tol=0.005):
	return abs(flt(a) - flt(b)) <= tol


def emp(company, key):
	return frappe.db.get_value("Employee", {"employee_name": EMPLOYEES[key][0], "company": company})


def save(slip, values=None, rows=None):
	return api.save_slip(slip["name"], values=values or {}, cost_centers=rows, modified=slip["modified"])[
		"slip"
	]


def je_lines(je_name):
	return frappe.get_all(
		"Journal Entry Account",
		filters={"parent": je_name},
		fields=[
			"account",
			"cost_center",
			"debit_in_account_currency as debit",
			"credit_in_account_currency as credit",
			"party",
			"reference_type",
		],
	)


def run(company):
	RESULTS.clear()
	abbr = frappe.get_cached_value("Company", company, "abbr")
	cc_a, cc_b, cc_main = f"PD Project A - {abbr}", f"PD Project B - {abbr}", f"Main - {abbr}"
	salary_account = f"Salary - {abbr}"
	gosi_payable = f"PD GOSI Payable - {abbr}"
	payable = f"Payroll Payable - {abbr}"
	frappe.flags.mute_emails = True
	try:
		_run(company, cc_a, cc_b, cc_main, salary_account, gosi_payable, payable)
	finally:
		frappe.db.rollback()
	passed = sum(1 for _l, ok in RESULTS if ok)
	print(f"\n{passed}/{len(RESULTS)} checks passed (all changes rolled back)")
	return {"passed": passed, "total": len(RESULTS), "failed": [l for l, ok in RESULTS if not ok]}


def _run(company, cc_a, cc_b, cc_main, salary_account, gosi_payable, payable):
	ahmed, fahad, omar, khaled = (emp(company, k) for k in ("nonsaudi", "saudi", "spare", "extra"))

	# T-14 / T-01: picking an employee with no slip creates one, pre-filled with the default cost center
	payload = api.get_slip(company, MONTH, YEAR, ahmed, create=1)
	slip = payload["slip"]
	check(
		"T-14 slip created as a draft for the picked month",
		slip and slip["docstatus"] == 0 and slip["start_date"] == "2026-08-01",
	)
	check(
		"T-14 cost centers pre-filled with one Auto row",
		len(slip["cost_centers"]) == 1 and slip["cost_centers"][0]["follow_salary_details"] == 1,
		slip["cost_centers"],
	)
	check(
		"T-01 net 2,000 and distribution 2,000, no difference",
		close(slip["net_pay"], 2000)
		and close(slip["distribution_total"], 2000)
		and close(slip["distribution_difference"], 0),
		(slip["net_pay"], slip["distribution_total"]),
	)
	check(
		"rates: daily 66.67, hourly 8.33",
		close(slip["daily_rate"], 66.666667, 0.0001) and close(slip["hourly_rate"], 8.333333, 0.0001),
	)

	# move the Auto row to Project A
	slip = save(
		slip, rows=[{"cost_center": cc_a, "basic_days": 30, "ot_hours": 0, "follow_salary_details": 1}]
	)

	# T-04: 20 OT hours entered; the Auto row follows
	slip = save(slip, values={"custom_ot_hours": 20})
	ot = next((l["amount"] for l in slip["earnings"] if l["salary_component"] == "Overtime"), 0)
	check(
		"T-04 overtime 166.67 (20 h x 2,000/240), net 2,166.67",
		close(ot, 166.67) and close(slip["net_pay"], 2166.67),
		(ot, slip["net_pay"]),
	)
	check(
		"T-04 Auto row follows: 30 days, 20 h, no difference",
		close(slip["cost_centers"][0]["ot_hours"], 20) and close(slip["distribution_difference"], 0),
		slip["cost_centers"][0],
	)

	# T-02: video case A: 20 h distributed, 0 h entered
	slip = save(
		slip,
		values={"custom_ot_hours": 0},
		rows=[{"cost_center": cc_a, "basic_days": 30, "ot_hours": 20, "follow_salary_details": 0}],
	)
	check(
		"T-02 difference 166.67 and all of it unexplained",
		close(slip["distribution_difference"], 166.67) and close(slip["unexplained_difference"], 166.67),
		(slip["distribution_difference"], slip["unexplained_difference"]),
	)

	# T-03: video case B: 10 days and 15 h distributed
	slip = save(slip, rows=[{"cost_center": cc_a, "basic_days": 10, "ot_hours": 15}])
	check(
		"T-03 distribution 791.67 against net 2,000, difference -1,208.33",
		close(slip["distribution_total"], 791.67) and close(slip["distribution_difference"], -1208.33),
		(slip["distribution_total"], slip["distribution_difference"]),
	)

	# T-05: 2 absent days, Auto row
	slip = save(
		slip,
		values={"custom_absent_days": 2},
		rows=[{"cost_center": cc_a, "basic_days": 0, "ot_hours": 0, "follow_salary_details": 1}],
	)
	absence = next((l["amount"] for l in slip["deductions"] if l["salary_component"] == "Absence"), 0)
	check(
		"T-05 absence 133.33, net 1,866.67, 28 days distributed, no difference",
		close(absence, 133.33)
		and close(slip["net_pay"], 1866.67)
		and close(slip["cost_centers"][0]["basic_days"], 28)
		and close(slip["distribution_difference"], 0),
		(absence, slip["net_pay"], slip["cost_centers"][0]["basic_days"]),
	)

	# T-06 (explained difference): a 500 deduction
	slip = save(
		slip,
		values={
			"custom_absent_days": 0,
			"custom_other_deduction": 500,
			"custom_other_deduction_remark": "Test",
		},
	)
	check(
		"T-06 net 1,500, difference 500 all explained",
		close(slip["net_pay"], 1500)
		and close(slip["distribution_difference"], 500)
		and close(slip["unexplained_difference"], 0),
		(slip["net_pay"], slip["distribution_difference"], slip["unexplained_difference"]),
	)

	# T-07: 3 late hours
	slip = save(slip, values={"custom_other_deduction": 0, "custom_late_hours": 3})
	late = next((l["amount"] for l in slip["deductions"] if l["salary_component"] == "Lateness"), 0)
	check(
		"T-07 lateness 25.00, net 1,975, difference 25 explained",
		close(late, 25) and close(slip["net_pay"], 1975) and close(slip["unexplained_difference"], 0),
		(late, slip["net_pay"]),
	)
	slip = save(slip, values={"custom_late_hours": 0})

	# V-02 / V-03 validation
	for label, rows in (
		(
			"V-03 duplicate cost center refused",
			[{"cost_center": cc_a, "basic_days": 10}, {"cost_center": cc_a, "basic_days": 5}],
		),
		(
			"V-02 more than 30 days refused",
			[{"cost_center": cc_a, "basic_days": 20}, {"cost_center": cc_b, "basic_days": 15}],
		),
		(
			"V-03 group cost center refused",
			[
				{
					"cost_center": frappe.db.get_value("Cost Center", {"company": company, "is_group": 1}),
					"basic_days": 30,
				}
			],
		),
	):
		try:
			save(slip, rows=rows)
			check(label, False, "saved without error")
		except frappe.ValidationError:
			frappe.local.message_log = []
			check(label, True)
		slip = api.get_slip(company, MONTH, YEAR, ahmed)["slip"]

	# T-11 + T-08: Saudi employee, two cost centers, post employee -> journal entry split by cost center
	fahad_slip = api.get_slip(company, MONTH, YEAR, fahad, create=1)["slip"]
	fahad_slip = save(
		fahad_slip,
		values={"custom_ot_hours": 20},
		rows=[
			{"cost_center": cc_a, "basic_days": 20, "ot_hours": 12},
			{"cost_center": cc_b, "basic_days": 10, "ot_hours": 8},
		],
	)
	gosi = next((l["amount"] for l in fahad_slip["deductions"] if l["salary_component"] == "GOSI"), 0)
	gosi_er = fahad_slip["gosi_employer_amount"]
	ahmed_gosi = next((l["amount"] for l in slip["deductions"] if l["salary_component"] == "GOSI"), 0)
	check(
		"T-11 GOSI 195.00 for the Saudi employee, 0 for the other",
		close(gosi, 195) and close(ahmed_gosi, 0),
		(gosi, ahmed_gosi),
	)
	check(
		"T-11 GOSI employer 235.00, 0 for the other, net pay untouched",
		close(gosi_er, 235) and close(slip["gosi_employer_amount"], 0),
		(gosi_er, slip["gosi_employer_amount"]),
	)

	posted = api.post_employee(fahad_slip["name"])["slip"]
	check(
		"T-08 post employee submits the slip and links a journal entry",
		posted["docstatus"] == 1 and posted["journal_entry"],
		posted["journal_entry"],
	)
	lines = je_lines(posted["journal_entry"])

	def debit(account, cc):
		return sum(flt(l.debit) for l in lines if l.account == account and l.cost_center == cc)

	def credit(account):
		return sum(flt(l.credit) for l in lines if l.account == account)

	# Salary account carries Inclusive Salary + Overtime + GOSI employer per cost center
	check(
		"T-08 Project A debit = 1,333.33 + 100.00 + 156.67",
		close(debit(salary_account, cc_a), 1333.33 + 100.00 + 156.67),
		debit(salary_account, cc_a),
	)
	check(
		"T-08 Project B debit = 666.67 + 66.67 + 78.33",
		close(debit(salary_account, cc_b), 666.67 + 66.67 + 78.33),
		debit(salary_account, cc_b),
	)
	check("T-08 GOSI payable credit 195 + 235", close(credit(gosi_payable), 430), credit(gosi_payable))
	check(
		"T-08 payroll payable credit 1,971.67 to the employee",
		close(credit(payable), 1971.67) and any(l.party == fahad for l in lines if l.account == payable),
		credit(payable),
	)
	total_debit = sum(flt(l.debit) for l in lines)
	total_credit = sum(flt(l.credit) for l in lines)
	check("T-08 journal entry balances", close(total_debit, total_credit), (total_debit, total_credit))

	# T-13: editing a posted slip is refused
	try:
		api.save_slip(posted["name"], values={"custom_ot_hours": 1})
		check("T-13 editing a posted slip is refused", False)
	except frappe.ValidationError:
		frappe.local.message_log = []
		check("T-13 editing a posted slip is refused", True)

	# T-15: post month with one slip that has no cost centers
	omar_slip = api.get_slip(company, MONTH, YEAR, omar, create=1)["slip"]
	frappe.db.delete("Salary Slip Cost Center", {"parent": omar_slip["name"]})
	result = api.post_month(company, MONTH, YEAR)
	check(
		"T-15 post month posts Ahmed and refuses Omar (no cost centers)",
		result["submitted"] == 1 and len(result["refused"]) == 1 and result["refused"][0]["employee"] == omar,
		result,
	)
	check(
		"T-15 one journal entry for the month", len(result["journal_entries"]) == 1, result["journal_entries"]
	)

	# T-09 / AR reports
	from payroll_distribution.payroll_distribution.report.employees_not_distributed import (
		employees_not_distributed,
	)
	from payroll_distribution.payroll_distribution.report.employees_without_salary import (
		employees_without_salary,
	)
	from payroll_distribution.payroll_distribution.report.salary_cost_by_cost_center import (
		salary_cost_by_cost_center,
	)
	from payroll_distribution.payroll_distribution.report.salary_differences import salary_differences

	filters = frappe._dict(company=company, month=str(MONTH), year=YEAR)
	without = employees_without_salary.execute(filters)[1]
	check(
		"T-09 AR-02 lists Khaled (no salary structure, no slip)",
		any(r["employee"] == khaled for r in without),
		[r["employee"] for r in without],
	)
	not_dist = employees_not_distributed.execute(filters)[1]
	check(
		"AR-03 lists Omar's undistributed slip",
		any(r["employee"] == omar for r in not_dist),
		[r["employee"] for r in not_dist],
	)
	cost = {
		r["cost_center"]: r for r in salary_cost_by_cost_center.execute(filters)[1] if r.get("cost_center")
	}
	check(
		"AR-04 Project B total 733.34 (666.67 + 66.67)",
		close(cost.get(cc_b, {}).get("total"), 733.34),
		cost.get(cc_b),
	)

	differences = {r["employee"]: r for r in salary_differences.execute(filters)[1] if r.get("employee")}
	row = differences.get(fahad) or {}
	check(
		"AR-01 lists Fahad: difference 195 explained by GOSI, unexplained 0",
		close(row.get("difference"), 195)
		and close(row.get("gosi"), 195)
		and close(row.get("unexplained"), 0),
		row,
	)

	# T-13 cancel: cancelling a posted slip cancels its journal entry
	ahmed_slip = frappe.get_doc("Salary Slip", slip["name"])
	je_name = ahmed_slip.journal_entry
	ahmed_slip.cancel()
	check(
		"T-13 cancelling a slip cancels its journal entry",
		frappe.db.get_value("Journal Entry", je_name, "docstatus") == 2,
		je_name,
	)

	# Excel import
	from openpyxl import Workbook

	omar_name = omar_slip["name"]
	wb = Workbook()
	ws = wb.active
	ws.append(api.TEMPLATE_HEADER)
	ws.append([omar, "", 10, 2, 1, 0, "", cc_a, 15, 10])
	ws.append([omar, "", None, None, None, None, None, cc_b, 14, 0])
	ws.append(["NOT-AN-EMPLOYEE", "", 1, 0, 0, 0, "", cc_a, 1, 0])
	buffer = io.BytesIO()
	wb.save(buffer)
	file_doc = frappe.get_doc(
		{"doctype": "File", "file_name": "pd_import_test.xlsx", "content": buffer.getvalue(), "is_private": 1}
	).insert(ignore_permissions=True)
	result = api.import_month(company, MONTH, YEAR, file_doc.file_url)
	omar_doc = frappe.get_doc("Salary Slip", omar_name)
	check(
		"Import updates Omar: 10 OT h, 2 late h, 1 absent day, two cost centers",
		result["updated"] == 1
		and flt(omar_doc.custom_ot_hours) == 10
		and len(omar_doc.custom_cost_centers) == 2,
		result,
	)
	check(
		"Import reports the unknown employee with its Excel row",
		len(result["errors"]) == 1 and result["errors"][0]["row"] == 4,
		result["errors"],
	)
	check(
		"Import result balances: 29 days distributed (30 - 1 absent)",
		close(omar_doc.custom_distributed_days, 29) and close(omar_doc.custom_payable_days, 29),
		(omar_doc.custom_distributed_days, omar_doc.custom_payable_days),
	)

	# Open month (September): drafts for every employee with a salary structure, none for Khaled
	result = api.open_month(company, 9, YEAR)
	september = {r["employee"]: r for r in api.get_month_slips(company, 9, YEAR)}
	check(
		"Open month creates drafts for Ahmed, Fahad and Omar, not Khaled",
		result["created"] == 3 and set(september) == {ahmed, fahad, omar},
		result,
	)
	again = api.open_month(company, 9, YEAR)
	check("Open month a second time creates nothing new", again["created"] == 0, again)
	fahad_sep = frappe.get_doc("Salary Slip", september[fahad]["name"])
	check(
		"Pre-fill copies last month's two-cost-center split",
		[r.cost_center for r in fahad_sep.custom_cost_centers] == [cc_a, cc_b],
		[(r.cost_center, r.basic_days) for r in fahad_sep.custom_cost_centers],
	)

	# Employee Advance recovered through salary (Fahad, September)
	from hrms.hr.doctype.employee_advance.employee_advance import create_return_through_additional_salary

	advance = frappe.get_doc(
		{
			"doctype": "Employee Advance",
			"employee": fahad,
			"company": company,
			"purpose": "Test advance",
			"advance_amount": 500,
			"posting_date": "2026-08-20",
			"exchange_rate": 1,
			"advance_account": frappe.get_cached_value(
				"Company", company, "default_employee_advance_account"
			),
			"repay_unclaimed_amount_from_salary": 1,
		}
	).insert(ignore_permissions=True)
	advance.submit()
	cash = frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0})
	pay = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": company,
			"posting_date": "2026-08-20",
			"accounts": [
				{
					"account": advance.advance_account,
					"party_type": "Employee",
					"party": fahad,
					"debit_in_account_currency": 500,
					"reference_type": "Employee Advance",
					"reference_name": advance.name,
					"is_advance": "Yes",
				},
				{"account": cash, "credit_in_account_currency": 500},
			],
		}
	).insert(ignore_permissions=True)
	pay.submit()
	advance.reload()
	recovery = create_return_through_additional_salary(advance)
	recovery.salary_component = "Advance Deduction"
	recovery.payroll_date = "2026-09-15"
	recovery.amount = 500
	recovery.insert(ignore_permissions=True)
	recovery.submit()
	fahad_sep.save()  # pick up the Additional Salary
	adv_line = [d for d in fahad_sep.deductions if d.salary_component == "Advance Deduction"]
	check(
		"Advance recovery appears on the slip as a 500 deduction",
		adv_line and close(adv_line[0].amount, 500),
		[(d.salary_component, d.amount) for d in fahad_sep.deductions],
	)
	check(
		"Header shows the outstanding advance balance (500)",
		close(fahad_sep.custom_advance_balance, 500),
		fahad_sep.custom_advance_balance,
	)

	# Post September: one journal entry for three slips
	result = api.post_month(company, 9, YEAR)
	check(
		"Post month posts all three September slips in one journal entry",
		result["submitted"] == 3 and len(result["journal_entries"]) == 1,
		result,
	)
	sep_je = result["journal_entries"][0]
	adv_rows = [l for l in je_lines(sep_je) if l.reference_type == "Employee Advance"]
	check(
		"Advance recovery credits the advance account against the Employee Advance",
		len(adv_rows) == 1 and close(adv_rows[0].credit, 500) and adv_rows[0].party == fahad,
		adv_rows,
	)
	advance.reload()
	check(
		"Employee Advance shows the 500 returned",
		close(advance.return_amount, 500),
		(advance.return_amount, advance.status),
	)
	from payroll_distribution.utils import get_advance_balance

	check(
		"Slips are named after their employee (Sal Slip/<employee>/#####)",
		all(
			name.startswith(f"Sal Slip/{employee}/")
			for name, employee in frappe.get_all("Salary Slip", fields=["name", "employee"], as_list=True)
		),
		frappe.get_all("Salary Slip", pluck="name"),
	)
	check(
		"Outstanding advance balance is 0 once the recovery is posted",
		close(get_advance_balance(fahad), 0),
		get_advance_balance(fahad),
	)

	# Cancel one slip of a shared entry: the entry is cancelled and the others are posted again
	ahmed_sep = frappe.get_doc("Salary Slip", september[ahmed]["name"])
	ahmed_sep.cancel()
	omar_je = frappe.db.get_value("Salary Slip", september[omar]["name"], "journal_entry")
	fahad_je = frappe.db.get_value("Salary Slip", september[fahad]["name"], "journal_entry")
	check(
		"Cancelling one slip cancels the shared journal entry",
		frappe.db.get_value("Journal Entry", sep_je, "docstatus") == 2,
	)
	check(
		"The other two slips are re-posted together in a new submitted entry",
		omar_je
		and omar_je == fahad_je
		and omar_je != sep_je
		and frappe.db.get_value("Journal Entry", omar_je, "docstatus") == 1,
		(omar_je, fahad_je),
	)

	_run_review_fixes(company, cc_a, ahmed, fahad, omar, salary_account, je_name)
	_run_second_review_fixes(company, cc_a, cc_b, cc_main, payable, ahmed, fahad, omar, khaled)


def refused(fn):
	"""True if `fn` is stopped by a validation or permission error. Its changes are undone."""
	frappe.db.savepoint("pd_refused")
	try:
		fn()
		return False
	except (frappe.ValidationError, frappe.PermissionError):
		return True
	finally:
		frappe.db.rollback(save_point="pd_refused")
		frappe.local.message_log = []


def pay_by_bank(company, employee, account, amount, posting_date, reference=None):
	"""A bank payment to the employee, naming the salary's journal entry only if `reference` is given."""
	row = {
		"account": account,
		"party_type": "Employee",
		"party": employee,
		"debit_in_account_currency": amount,
	}
	if reference:
		row.update(reference_type="Journal Entry", reference_name=reference)
	cash = frappe.db.get_value("Account", {"company": company, "account_type": "Cash", "is_group": 0})
	doc = frappe.get_doc(
		{
			"doctype": "Journal Entry",
			"company": company,
			"posting_date": posting_date,
			"accounts": [row, {"account": cash, "credit_in_account_currency": amount}],
		}
	).insert(ignore_permissions=True)
	doc.submit()
	return doc


def excel_file(rows):
	from openpyxl import Workbook

	wb = Workbook()
	ws = wb.active
	for row in rows:
		ws.append(row)
	buffer = io.BytesIO()
	wb.save(buffer)
	return frappe.get_doc(
		{
			"doctype": "File",
			"file_name": f"pd_test_{frappe.generate_hash(length=6)}.xlsx",
			"content": buffer.getvalue(),
			"is_private": 1,
		}
	).insert(ignore_permissions=True)


def login_with_role(role, employee=None):
	user = f"pd.test.{role.replace(' ', '.').lower()}@example.com"
	if not frappe.db.exists("User", user):
		frappe.get_doc(
			{"doctype": "User", "email": user, "first_name": "PD Test", "send_welcome_email": 0}
		).insert(ignore_permissions=True)
		frappe.get_doc(
			{
				"doctype": "Has Role",
				"name": frappe.generate_hash(length=10),
				"parent": user,
				"parenttype": "User",
				"parentfield": "roles",
				"role": role,
			}
		).db_insert()
	if employee:
		frappe.get_doc(
			{
				"doctype": "User Permission",
				"user": user,
				"allow": "Employee",
				"for_value": employee,
				"apply_to_all_doctypes": 1,
			}
		).insert(ignore_permissions=True)
	frappe.clear_cache(user=user)
	frappe.set_user(user)


def _run_review_fixes(company, cc_a, ahmed, fahad, omar, salary_account, cancelled_je):
	month = 7  # no other check uses July

	# An employee sees only their own slip, through the page's endpoints too
	admin = frappe.session.user
	login_with_role("Employee", omar)
	try:
		check("Employee can't list the month", refused(lambda: api.get_month_slips(company, MONTH, YEAR)))
		check(
			"Employee can't open a colleague's slip",
			refused(lambda: api.get_slip(company, MONTH, YEAR, fahad)),
		)
		check(
			"Employee can't download the template",
			refused(lambda: api.download_template(company, MONTH, YEAR)),
		)
	finally:
		frappe.set_user(admin)
	login_with_role("HR User")
	try:
		check(
			"HR User still opens the month and a slip",
			api.get_month_slips(company, MONTH, YEAR) and api.get_slip(company, MONTH, YEAR, fahad)["slip"],
		)
	finally:
		frappe.set_user(admin)

	# A paid salary can't be cancelled
	posted = frappe.get_doc("Salary Slip", api.get_slip(company, MONTH, YEAR, fahad)["slip"]["name"])
	line = next(l for l in je_lines(posted.journal_entry) if l.party == fahad)
	payment = pay_by_bank(company, fahad, line.account, line.credit, "2026-08-31", posted.journal_entry)
	check(
		"Cancelling a paid salary is refused",
		refused(lambda: frappe.get_doc("Salary Slip", posted.name).cancel()),
	)
	payment.cancel()
	frappe.get_doc("Salary Slip", posted.name).cancel()
	check(
		"With the payment cancelled, the salary cancels",
		frappe.db.get_value("Salary Slip", posted.name, "docstatus") == 2,
	)

	# Amend, the way the desk copies the slip
	cancelled = frappe.get_doc("Salary Slip", posted.name)
	amended = frappe.copy_doc(cancelled)
	amended.amended_from = cancelled.name
	amended.docstatus = 0
	amended.insert()
	amended.submit()
	amended.reload()
	check(
		"A cancelled slip can be amended and posted again",
		amended.journal_entry
		and frappe.db.get_value("Journal Entry", amended.journal_entry, "docstatus") == 1,
		amended.journal_entry,
	)

	# Excel: the template leaves an Auto row's quantities blank, so the same file can be imported again
	slip = api.get_slip(company, month, YEAR, ahmed, create=1)["slip"]
	row = slip["cost_centers"][0]
	frappe.local.response = frappe._dict()
	api.download_template(company, month, YEAR)
	sheet = load_workbook(io.BytesIO(frappe.local.response.filecontent)).active
	exported = [cell.value for cell in sheet[2]]
	check(
		"The template leaves an Auto row's days and hours blank and records the month",
		exported[8] is None and exported[9] is None and exported[10] == f"{YEAR}-{month:02d}",
		exported,
	)
	exported[2], exported[4] = 10, 2  # overtime and absence typed in
	auto_file = excel_file([api.TEMPLATE_HEADER, exported])
	for attempt in ("first", "second"):
		result = api.import_month(company, month, YEAR, auto_file.file_url)
		doc = frappe.get_doc("Salary Slip", slip["name"])
		auto_row = doc.custom_cost_centers[0]
		check(
			f"Import keeps Auto and balances ({attempt} time)",
			result["updated"] == 1
			and auto_row.follow_salary_details == 1
			and close(auto_row.basic_days, 28)
			and close(auto_row.ot_hours, 10)
			and close(doc.custom_distribution_difference, 0),
			(result, auto_row.basic_days, auto_row.ot_hours, doc.custom_distribution_difference),
		)
	edited_row = [ahmed, "", 10, 0, 0, 0, "", row["cost_center"], 20, 10]
	api.import_month(company, month, YEAR, excel_file([api.TEMPLATE_HEADER, edited_row]).file_url)
	doc.reload()
	check(
		"Import with edited days turns Auto off",
		doc.custom_cost_centers[0].follow_salary_details == 0
		and close(doc.custom_cost_centers[0].basic_days, 20),
	)

	# Excel: text in a number cell is an error
	bad_row = [ahmed, "", "ten", 0, 0, 0, "", row["cost_center"], 20, 10]
	result = api.import_month(company, month, YEAR, excel_file([api.TEMPLATE_HEADER, bad_row]).file_url)
	doc.reload()
	check(
		"Import refuses text in a number cell and changes nothing",
		result["updated"] == 0 and len(result["errors"]) == 1 and close(doc.custom_ot_hours, 10),
		result,
	)

	# Nothing on the cost centers: can't be posted
	slip = api.get_slip(company, month, YEAR, ahmed)["slip"]
	slip = save(
		slip, rows=[{"cost_center": cc_a, "basic_days": 0, "ot_hours": 0, "follow_salary_details": 0}]
	)
	check(
		"A distribution of 0 days and 0 hours can't be posted",
		refused(lambda: api.post_employee(slip["name"])),
	)

	# A journal entry failure in the background reaches the user
	sent = []
	publish = lambda event, message=None, **kwargs: sent.append((event, message))  # noqa: E731
	slip = save(slip, rows=[{"cost_center": cc_a, "basic_days": 30, "ot_hours": 10}])
	frappe.db.savepoint("pd_background")
	with (
		patch.object(frappe.local.db, "commit"),
		patch.object(frappe.local.db, "rollback"),
		patch.object(frappe, "publish_realtime", publish),
		patch.object(frappe, "log_error"),
		patch(
			"payroll_distribution.accounting.get_component_account",
			side_effect=frappe.ValidationError("no account"),
		),
	):
		api.post_slips_in_background([slip["name"]], frappe.session.user)
	frappe.db.rollback(save_point="pd_background")
	frappe.local.message_log = []
	done = [m for e, m in sent if e == "payroll_distribution_done"]
	check("A failed background post tells the user", len(done) == 1 and done[0].get("error"), done)

	# Settings: payable account
	settings = frappe.get_doc("Payroll Distribution Settings")
	settings.payroll_payable_account = salary_account
	check("Settings refuse an expense account as payroll payable", refused(settings.validate))


def _run_second_review_fixes(company, cc_a, cc_b, cc_main, payable, ahmed, fahad, omar, khaled):
	from frappe.desk.query_report import run as run_report
	from frappe.desk.search import search_link

	from payroll_distribution.payroll_distribution.report.salary_cost_by_cost_center import (
		salary_cost_by_cost_center,
	)
	from payroll_distribution.payroll_distribution.report.salary_differences import salary_differences

	admin = frappe.session.user

	def draft(employee, month):
		return api.get_slip(company, month, YEAR, employee, create=1)["slip"]

	def post(employee, month):
		return api.post_employee(draft(employee, month)["name"])["slip"]

	def gosi(slip):
		return sum(l["amount"] for l in slip["deductions"] if l["salary_component"] == "GOSI")

	# 1. Posting the same slips again posts nothing, and a queued month can't be queued again
	names = [draft(ahmed, 1)["name"], draft(omar, 1)["name"]]
	first, second = api.post_slips(names), api.post_slips(names)
	check(
		"A second run over the same slips posts nothing",
		first["submitted"] == 2
		and len(first["journal_entries"]) == 1
		and second["submitted"] == 0
		and not second["journal_entries"],
		(first, second),
	)
	draft(fahad, 1)
	with (
		patch.object(api, "BACKGROUND_THRESHOLD", 0),
		patch("payroll_distribution.api.is_job_enqueued", return_value=True),
		patch.object(frappe, "enqueue") as enqueue,
	):
		check(
			"Post Month is refused while the month is still queued",
			refused(lambda: api.post_month(company, 1, YEAR)) and not enqueue.called,
		)

	# 2. Payments that don't name the journal entry count too, oldest salary first
	january_entry = frappe.db.get_value("Salary Slip", names[1], "journal_entry")
	january = next(l for l in je_lines(january_entry) if l.party == omar)
	february, march = post(omar, 2), post(omar, 3)
	pay_by_bank(company, omar, january.account, january.credit, "2026-02-05")
	check(
		"A bank entry for the oldest salary leaves the next one free to cancel",
		not refused(lambda: frappe.get_doc("Salary Slip", february["name"]).cancel()),
	)
	line = next(l for l in je_lines(february["journal_entry"]) if l.party == omar)
	pay_by_bank(company, omar, line.account, line.credit, "2026-03-05")
	check(
		"A salary paid by an ordinary bank entry can't be cancelled",
		refused(lambda: frappe.get_doc("Salary Slip", february["name"]).cancel()),
	)
	check(
		"The next month, still unpaid, can be cancelled",
		not refused(lambda: frappe.get_doc("Salary Slip", march["name"]).cancel()),
	)

	# 3. The journal entry can't go on its own
	check(
		"A salary's journal entry can't be cancelled on its own",
		refused(lambda: frappe.get_doc("Journal Entry", march["journal_entry"]).cancel()),
	)

	# 4. A Payroll Entry's slips are left to it
	payroll_entry = frappe.get_doc(
		{
			"doctype": "Payroll Entry",
			"company": company,
			"payroll_frequency": "Monthly",
			"start_date": "2026-04-01",
			"end_date": "2026-04-30",
			"posting_date": "2026-04-30",
			"currency": frappe.get_cached_value("Company", company, "default_currency"),
			"exchange_rate": 1,
			"payroll_payable_account": payable,
			"cost_center": cc_main,
		}
	)
	payroll_entry.flags.ignore_mandatory = True
	payroll_entry.insert(ignore_permissions=True)
	entry_slip = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": ahmed,
			"company": company,
			"start_date": "2026-04-01",
			"end_date": "2026-04-30",
			"posting_date": "2026-04-30",
			"payroll_frequency": "Monthly",
			"payroll_entry": payroll_entry.name,
		}
	).insert()
	result = api.post_month(company, 4, YEAR)
	check(
		"Post Month lists a Payroll Entry's slip and doesn't post it",
		result["submitted"] == 0 and not result["journal_entries"] and len(result["refused"]) == 1,
		result,
	)
	check("Post Employee refuses a Payroll Entry's slip", refused(lambda: api.post_employee(entry_slip.name)))
	frappe.db.delete("Salary Slip Cost Center", {"parent": entry_slip.name})
	check(
		"A Payroll Entry's slip can be submitted without cost centers",
		not refused(lambda: frappe.get_doc("Salary Slip", entry_slip.name).submit()),
	)

	# 5. A corrected nationality corrects the deduction, both ways
	slip = draft(fahad, 5)
	frappe.db.set_value("Employee", fahad, "custom_nationality", "India")
	corrected = save(slip)
	check(
		"Correcting a nationality removes the GOSI deduction",
		close(gosi(corrected), 0)
		and close(corrected["gosi_employer_amount"], 0)
		and close(corrected["net_pay"], 2000),
		(gosi(corrected), corrected["gosi_employer_amount"], corrected["net_pay"]),
	)
	frappe.db.set_value("Employee", fahad, "custom_nationality", "Saudi Arabia")
	restored = save(corrected)
	check(
		"Setting it back restores the GOSI deduction",
		close(gosi(restored), 195) and close(restored["gosi_employer_amount"], 235),
		(gosi(restored), restored["gosi_employer_amount"]),
	)

	# 6. An HR User with no other role can reach the companies and cost centers
	login_with_role("HR User")
	try:
		filters = json.dumps({"company": company, "is_group": 0, "disabled": 0})
		check(
			"HR User alone can search companies and cost centers",
			search_link("Company", "") and search_link("Cost Center", "", filters=filters),
		)
		report_filters = json.dumps({"company": company, "month": str(MONTH), "year": YEAR})
		check(
			"HR User alone can run Salary Cost by Cost Center",
			run_report("Salary Cost by Cost Center", filters=report_filters)["result"],
		)
	finally:
		frappe.set_user(admin)

	# 8. An even split is not a difference
	slip = save(
		draft(ahmed, 6),
		rows=[
			{"cost_center": cc_a, "basic_days": 10},
			{"cost_center": cc_b, "basic_days": 10},
			{"cost_center": cc_main, "basic_days": 10},
		],
	)
	check(
		"A three-way split adds up to the salary, with no difference",
		close(slip["distribution_total"], 2000)
		and close(sum(r["row_total"] for r in slip["cost_centers"]), 2000)
		and close(slip["distribution_difference"], 0)
		and close(slip["unexplained_difference"], 0),
		(slip["distribution_total"], slip["distribution_difference"]),
	)
	flagged = salary_differences.execute(
		frappe._dict(company=company, month="6", year=YEAR, only_unexplained=1)
	)[1]
	check("Rounding isn't reported as a difference", not flagged, flagged)
	with patch(
		"payroll_distribution.report_utils.get_settings",
		return_value=frappe._dict(difference_tolerance=0.01),
	):
		from payroll_distribution.report_utils import is_over_tolerance

		check(
			"A difference of exactly the tolerance is not flagged, one cent more is",
			not is_over_tolerance(0.01) and is_over_tolerance(0.02),
		)

	# 9. Shares that round to nothing
	check(
		"A share that rounds to nothing is left out",
		split_amount(0.17, [("a", 29.5), ("b", 0.5)], 2) == [("a", 0.17)],
	)
	shares = split_amount(0.05, [(i, 1) for i in range(9)], 2)
	check(
		"Splitting a small amount gives no negative share and adds up",
		all(share > 0 for _key, share in shares) and close(sum(share for _key, share in shares), 0.05),
		shares,
	)
	slip = save(
		draft(omar, 6),
		values={"custom_late_hours": 0.02},
		rows=[{"cost_center": cc_a, "basic_days": 29.5}, {"cost_center": cc_b, "basic_days": 0.5}],
	)
	check(
		"A very small deduction posts", api.post_employee(slip["name"])["slip"]["journal_entry"], slip["name"]
	)

	# 10. Nothing to post
	assignment = frappe.get_doc(
		{
			"doctype": "Salary Structure Assignment",
			"employee": khaled,
			"salary_structure": "PD Test Structure",
			"company": company,
			"from_date": "2026-01-01",
			"base": 0,
		}
	).insert(ignore_permissions=True)
	assignment.submit()
	posted = post(khaled, 6)
	check(
		"A slip with no pay posts with no journal entry",
		posted["docstatus"] == 1 and not posted["journal_entry"],
		(posted["docstatus"], posted["journal_entry"]),
	)
	per_employee = copy.copy(api.get_settings())
	per_employee.journal_entry_per = "Employee"
	with patch.object(api, "get_settings", lambda: per_employee):
		result = api.post_slips([draft(khaled, 5)["name"]])
	check(
		"Nor does it stop a month posted per employee",
		result["submitted"] == 1 and not result["journal_entries"],
		result,
	)

	# 11. Files that used to lose data
	header = api.TEMPLATE_HEADER
	row = [ahmed, "", 0, 0, 0, 0, "", cc_a, 20, 0]

	def import_rows(*rows):
		return api.import_month(company, 6, YEAR, excel_file(rows).file_url)

	check("A file with no header row is refused", refused(lambda: import_rows(row)))
	check("Another month's file is refused", refused(lambda: import_rows(header, [*row, "2026-05"])))
	damaged = frappe.get_doc(
		{
			"doctype": "File",
			"file_name": "pd_test_damaged.xlsx",
			"content": b"not a workbook",
			"is_private": 1,
		}
	).insert(ignore_permissions=True)
	check(
		"A damaged file is refused with a message",
		refused(lambda: api.import_month(company, 6, YEAR, damaged.file_url)),
	)
	result = import_rows(header, row, [None, None, None, None, None, None, None, cc_b, 10, 0])
	doc = frappe.get_doc("Salary Slip", draft(ahmed, 6)["name"])
	check(
		"A row with no employee continues the one above",
		result["updated"] == 1
		and [(r.cost_center, r.basic_days) for r in doc.custom_cost_centers] == [(cc_a, 20), (cc_b, 10)],
		result,
	)
	result = import_rows(header, row, [ahmed, "", 5, None, None, None, None, cc_b, 10, 0])
	doc.reload()
	check(
		"Inputs on an employee's second row are reported and nothing changes",
		result["updated"] == 0
		and len(result["errors"]) == 1
		and result["errors"][0]["error"].startswith("Row 3")
		and close(doc.custom_ot_hours, 0),
		result,
	)
	result = import_rows(header, [None, None, None, None, None, None, None, cc_a, 1, 0])
	check(
		"A first row with no employee is reported",
		result["errors"] and result["errors"][0]["row"] == 2,
		result,
	)

	# 12. The report shows what is in the ledger
	slip = save(
		draft(fahad, 3),
		values={"custom_ot_hours": 20},
		rows=[{"cost_center": cc_a, "basic_days": 10, "ot_hours": 15}],
	)
	posted = api.post_employee(slip["name"])["slip"]
	rows = salary_cost_by_cost_center.execute(
		frappe._dict(company=company, month="3", year=YEAR, posted_only=1)
	)[1]
	report = {r["cost_center"]: r for r in rows}
	entries = frappe.get_all(
		"Salary Slip", filters={"start_date": "2026-03-01", "docstatus": 1}, pluck="journal_entry"
	)
	ledger = dict(
		frappe.db.sql(
			"""select gl.cost_center, sum(gl.debit - gl.credit)
			from `tabGL Entry` gl join `tabAccount` a on a.name = gl.account
			where gl.voucher_no in %s and gl.is_cancelled = 0 and a.report_type = 'Profit and Loss'
			group by gl.cost_center""",
			[tuple(entries)],
		)
	)
	check(
		"Salary Cost by Cost Center shows what is in the ledger",
		ledger and all(close(report[cc]["posted_cost"], amount) for cc, amount in ledger.items()),
		(ledger, {cc: r["posted_cost"] for cc, r in report.items()}),
	)
	check(
		"Beside the distribution total, where they differ",
		close(report[cc_a]["total"], 791.67) and not close(report[cc_a]["posted_cost"], 791.67),
		report[cc_a],
	)

	# 14. Limited to some employees, Post Month posts those and leaves the rest
	draft(ahmed, 5), draft(omar, 5)
	login_with_role("HR Manager", ahmed)
	try:
		result = api.post_month(company, 5, YEAR)
	finally:
		frappe.set_user(admin)
	posted = {
		r.employee: r.docstatus
		for r in frappe.get_all(
			"Salary Slip", filters={"start_date": "2026-05-01"}, fields=["employee", "docstatus"]
		)
	}
	check(
		"A user limited to some employees posts only those",
		result["submitted"] == 1 and posted[ahmed] == 1 and posted[fahad] == 0 and posted[omar] == 0,
		(result, posted),
	)


DEMO_MONTH = 10


def make_demo(company):
	"""Leave an October 2026 month with drafts in place (committed), for trying the page by hand.

	It uses a different month from `run` (August and September) so the two don't collide."""
	abbr = frappe.get_cached_value("Company", company, "abbr")
	api.open_month(company, DEMO_MONTH, YEAR)
	ahmed, fahad = emp(company, "nonsaudi"), emp(company, "saudi")
	slip = api.get_slip(company, DEMO_MONTH, YEAR, fahad)["slip"]
	save(
		slip,
		values={"custom_ot_hours": 20},
		rows=[
			{"cost_center": f"PD Project A - {abbr}", "basic_days": 20, "ot_hours": 12},
			{"cost_center": f"PD Project B - {abbr}", "basic_days": 10, "ot_hours": 8},
		],
	)
	slip = api.get_slip(company, DEMO_MONTH, YEAR, ahmed)["slip"]
	save(
		slip,
		values={"custom_ot_hours": 0},
		rows=[{"cost_center": f"PD Project A - {abbr}", "basic_days": 30, "ot_hours": 20}],
	)
	frappe.db.commit()
	print("Demo month ready: October 2026")
