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

import io

import frappe
from frappe.utils import flt

from payroll_distribution import api
from payroll_distribution.accounting import make_journal_entry

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
	# HRMS v16 wants the employee advance account to be Receivable (the standard chart makes it Payable)
	advance_account = frappe.get_cached_value("Company", company, "default_employee_advance_account")
	if advance_account:
		frappe.db.set_value("Account", advance_account, "account_type", "Receivable")
	salary = f"Salary - {abbr}"

	accounts = {
		"Inclusive Salary": (salary, None),
		"Overtime": (salary, None),
		"Absence": (salary, None),
		"Lateness": (salary, None),
		"Other Deduction": (salary, None),
		"Advance Deduction": (f"Employee Advances - {abbr}", None),
		"GOSI": (gosi_payable, None),
		"GOSI Employer": (salary, gosi_payable),
	}
	for component, (account, liability) in accounts.items():
		doc = frappe.get_doc("Salary Component", component)
		doc.set("accounts", [r for r in doc.accounts if r.company != company])
		doc.append("accounts", {"company": company, "account": account, "liability_account": liability})
		doc.save(ignore_permissions=True)

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
	# HRMS v16 looks holidays up through Holiday List Assignment
	if not frappe.db.exists(
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
		structure.append("employer_contributions", structure_row("GOSI Employer"))
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
	gosi_er = next(
		(
			l["amount"]
			for l in fahad_slip["employer_contributions"]
			if l["salary_component"] == "GOSI Employer"
		),
		0,
	)
	ahmed_gosi = next((l["amount"] for l in slip["deductions"] if l["salary_component"] == "GOSI"), 0)
	check(
		"T-11 GOSI 195.00 for the Saudi employee, 0 for the other",
		close(gosi, 195) and close(ahmed_gosi, 0),
		(gosi, ahmed_gosi),
	)
	check("T-11 GOSI employer 235.00", close(gosi_er, 235), gosi_er)

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
