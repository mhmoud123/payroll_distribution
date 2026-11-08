# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

"""Server side of the Monthly Salary Entry page."""

import json
import math

import frappe
from frappe import _
from frappe.utils import cint, flt, getdate
from frappe.utils.background_jobs import is_job_enqueued

from payroll_distribution.utils import get_period, get_settings

INPUT_FIELDS = (
	"custom_ot_hours",
	"custom_late_hours",
	"custom_absent_days",
	"custom_other_deduction",
	"custom_other_deduction_remark",
)
COST_CENTER_FIELDS = ("cost_center", "basic_days", "ot_hours", "follow_salary_details")
BACKGROUND_THRESHOLD = 40
PAGE_ROLES = ("HR User", "HR Manager", "System Manager")


# ---------------------------------------------------------------- reading


def check_page_access():
	"""Every Employee can read Salary Slip (User Permissions limit them to their own), so reading the
	doctype is not enough for these endpoints, which return other people's salaries."""
	frappe.only_for(PAGE_ROLES)
	frappe.has_permission("Salary Slip", "read", throw=True)


@frappe.whitelist()
def get_page_context():
	check_page_access()
	settings = get_settings()
	company = frappe.defaults.get_user_default("Company") or frappe.db.get_single_value(
		"Global Defaults", "default_company"
	)
	today = getdate()
	return {
		"company": company,
		"month": today.month,
		"year": today.year,
		"days_per_month": settings.days_per_month,
		"hours_per_day": settings.hours_per_day,
		"overtime_multiplier": settings.overtime_multiplier,
		"difference_tolerance": settings.difference_tolerance,
		"can_create": frappe.has_permission("Salary Slip", "create"),
		"can_submit": frappe.has_permission("Salary Slip", "submit"),
		"can_delete": frappe.has_permission("Salary Slip", "delete"),
	}


@frappe.whitelist()
def get_month_slips(company, month, year):
	check_page_access()
	start, end = get_period(month, year)
	return frappe.get_list(
		"Salary Slip",
		filters={"company": company, "start_date": start, "end_date": end, "docstatus": ("<", 2)},
		fields=[
			"name",
			"employee",
			"employee_name",
			"docstatus",
			"net_pay",
			"custom_distribution_difference",
			"custom_unexplained_difference",
		],
		order_by="employee asc",
	)


@frappe.whitelist()
def get_slip(company, month, year, employee, create=0):
	check_page_access()
	start, end = get_period(month, year)
	name = find_slip(employee, company, start, end)
	if name:
		doc = frappe.get_doc("Salary Slip", name)
		doc.check_permission("read")
		return slip_payload(doc)
	frappe.has_permission("Employee", "read", doc=employee, throw=True)
	if not cint(create):
		return {"slip": None, "header": get_employee_header(employee)}
	frappe.has_permission("Salary Slip", "create", throw=True)
	return slip_payload(new_slip(employee, company, start, end))


# ---------------------------------------------------------------- editing


@frappe.whitelist()
def save_slip(name, values=None, cost_centers=None, modified=None):
	doc = frappe.get_doc("Salary Slip", name)
	doc.check_permission("write")
	if doc.docstatus != 0:
		frappe.throw(
			_("Salary Slip {0} is already posted. Cancel and amend it to make changes.").format(name)
		)
	if modified and str(doc.modified) != str(modified):
		frappe.throw(
			_(
				"Someone else changed this salary slip after you opened it. Reload it and enter your changes again."
			),
			title=_("Changed by Someone Else"),
		)
	apply_inputs(doc, parse_json(values) or {})
	rows = parse_json(cost_centers)
	if rows is not None:
		apply_cost_centers(doc, rows)
	doc.save()
	return slip_payload(doc)


@frappe.whitelist()
def delete_slip(name):
	doc = frappe.get_doc("Salary Slip", name)
	if doc.docstatus != 0:
		frappe.throw(_("Only unposted salary slips can be deleted."))
	frappe.delete_doc("Salary Slip", name)
	return True


@frappe.whitelist()
def open_month(company, month, year):
	"""Create a draft slip for every eligible employee who doesn't have one this month."""
	frappe.has_permission("Salary Slip", "create", throw=True)
	start, end = get_period(month, year)
	existing = set(
		frappe.get_all(
			"Salary Slip",
			filters={"company": company, "start_date": start, "end_date": end, "docstatus": ("<", 2)},
			pluck="employee",
		)
	)
	employees = [e for e in get_eligible_employees(company, start, end) if e not in existing]
	if not employees:
		return {"created": 0, "failed": [], "queued": False}
	if len(employees) > BACKGROUND_THRESHOLD:
		frappe.enqueue(
			"payroll_distribution.api.create_slips",
			queue="long",
			timeout=3600,
			employees=employees,
			company=company,
			start=start,
			end=end,
			notify_user=frappe.session.user,
		)
		return {"created": 0, "failed": [], "queued": True, "count": len(employees)}
	return create_slips(employees, company, start, end)


def create_slips(employees, company, start, end, notify_user=None):
	created, failed = 0, []
	for employee in employees:
		frappe.db.savepoint("pd_open_month")
		try:
			new_slip(employee, company, start, end)
			created += 1
		except Exception as e:
			frappe.db.rollback(save_point="pd_open_month")
			failed.append({"employee": employee, "error": last_error_message(e)})
	result = {"created": created, "failed": failed, "queued": False}
	if notify_user:
		frappe.db.commit()  # nosemgrep: background job
		frappe.publish_realtime(
			"payroll_distribution_done", {"action": "open_month", **result}, user=notify_user
		)
	return result


# ---------------------------------------------------------------- posting


@frappe.whitelist()
def post_employee(name):
	doc = frappe.get_doc("Salary Slip", name)
	doc.check_permission("submit")
	if doc.docstatus != 0:
		frappe.throw(_("Salary Slip {0} is already posted.").format(name))
	validate_not_from_payroll_entry(doc)
	doc.submit()  # on_submit creates the journal entry
	doc.reload()
	return slip_payload(doc)


@frappe.whitelist()
def post_month(company, month, year):
	frappe.has_permission("Salary Slip", "submit", throw=True)
	start, end = get_period(month, year)
	names = frappe.get_list(
		"Salary Slip",
		filters={"company": company, "start_date": start, "end_date": end, "docstatus": 0},
		pluck="name",
		order_by="employee asc",
	)
	if not names:
		frappe.throw(_("There are no unposted salary slips for this month."))
	if len(names) > BACKGROUND_THRESHOLD:
		job_id = f"pd_post_month::{company}::{start}"
		if is_job_enqueued(job_id):
			frappe.throw(_("This month is already being posted. You'll get a message when it's done."))
		frappe.enqueue(
			"payroll_distribution.api.post_slips_in_background",
			queue="long",
			timeout=3600,
			job_id=job_id,
			deduplicate=True,
			names=names,
			notify_user=frappe.session.user,
		)
		return {"queued": True, "count": len(names)}
	return post_slips(names)


def post_slips(names):
	"""Submit each slip in its own savepoint, then create the journal entry for those that went through.

	If the journal entry itself fails (for example a missing account), the error is raised and the
	whole request rolls back, so no slip is left posted without its entry.
	"""
	submitted, refused = [], []
	frappe.flags.payroll_distribution_batch = True
	try:
		for name in names:
			doc = frappe.get_doc("Salary Slip", name)
			if doc.docstatus != 0:
				continue  # posted after this list was made: a queued job can run after another one
			frappe.db.savepoint("pd_post_month")
			try:
				validate_not_from_payroll_entry(doc)
				doc.submit()
				submitted.append(doc)
			except frappe.ValidationError as e:
				frappe.db.rollback(save_point="pd_post_month")
				refused.append(
					{
						"name": name,
						"employee": doc.employee,
						"employee_name": doc.employee_name,
						"error": last_error_message(e),
					}
				)
	finally:
		frappe.flags.payroll_distribution_batch = False

	from payroll_distribution.accounting import make_journal_entry

	if get_settings().journal_entry_per == "Employee":
		entries = [make_journal_entry([doc]) for doc in submitted]
	else:
		entries = [make_journal_entry(submitted)]

	return {
		"submitted": len(submitted),
		"refused": refused,
		"journal_entries": [je.name for je in entries if je],
		"queued": False,
	}


def validate_not_from_payroll_entry(doc):
	if doc.payroll_entry:
		frappe.throw(
			_("Salary Slip {0} belongs to Payroll Entry {1}. Post it from there.").format(
				doc.name, doc.payroll_entry
			)
		)


def post_slips_in_background(names, notify_user):
	"""Post the slips and always tell the user the outcome; a failure posts nothing."""
	try:
		result = post_slips(names)
	except Exception as e:
		error = last_error_message(e)
		frappe.db.rollback()
		frappe.log_error(title="Post Month failed")
		result = {"submitted": 0, "refused": [], "journal_entries": [], "queued": False, "error": error}
	frappe.db.commit()  # nosemgrep: background job
	frappe.publish_realtime("payroll_distribution_done", {"action": "post_month", **result}, user=notify_user)


# ---------------------------------------------------------------- Excel

TEMPLATE_HEADER = [
	"Employee / الموظف",
	"Employee Name / الاسم",
	"Overtime Hours / ساعات الإضافي",
	"Late Hours / ساعات التأخير",
	"Absence Days / أيام الغياب",
	"Other Deduction / خصومات أخرى",
	"Other Deduction Remark / سبب الخصم",
	"Cost Center / مركز التكلفة",
	"Days (blank = Auto) / الأيام (فارغ = تلقائي)",
	"Cost Center Overtime Hours (blank = Auto) / ساعات الإضافي للمركز (فارغ = تلقائي)",
	"Month / الشهر",
]
MONTH_COLUMN = len(TEMPLATE_HEADER) - 1


@frappe.whitelist(methods=["GET"])
def download_template(company, month, year):
	"""Excel file of the month's draft slips: one row per cost center; inputs on each employee's first row."""
	check_page_access()
	from frappe.utils.xlsxutils import build_xlsx_response

	start, end = get_period(month, year)
	period = template_period(month, year)
	data = [TEMPLATE_HEADER]
	for slip in frappe.get_list(
		"Salary Slip",
		filters={"company": company, "start_date": start, "end_date": end, "docstatus": 0},
		pluck="name",
		order_by="employee asc",
	):
		doc = frappe.get_doc("Salary Slip", slip)
		rows = doc.get("custom_cost_centers") or [frappe._dict()]
		for i, row in enumerate(rows):
			inputs = (
				[
					flt(doc.custom_ot_hours),
					flt(doc.custom_late_hours),
					flt(doc.custom_absent_days),
					flt(doc.custom_other_deduction),
					doc.custom_other_deduction_remark or "",
				]
				if i == 0
				else ["", "", "", "", ""]
			)
			follows = row.get("follow_salary_details")
			data.append(
				[
					doc.employee,
					doc.employee_name,
					*inputs,
					row.get("cost_center") or "",
					None if follows else row.get("basic_days"),
					None if follows else row.get("ot_hours"),
					period,
				]
			)
	build_xlsx_response(data, f"salary-entry-{period}")


@frappe.whitelist()
def import_month(company, month, year, file_url):
	"""Apply an Excel sheet in the template layout. Each employee is saved on its own; errors are listed per employee."""
	frappe.has_permission("Salary Slip", "write", throw=True)
	start, end = get_period(month, year)
	grouped, errors = group_excel_rows(read_template_rows(file_url, month, year))

	updated = 0
	for employee, entry in grouped.items():
		frappe.db.savepoint("pd_import")
		try:
			if entry["second_inputs_row"]:
				frappe.throw(
					_("Row {0}: enter the monthly inputs on the employee's first row only.").format(
						entry["second_inputs_row"]
					)
				)
			name = find_slip(employee, company, start, end)
			doc = frappe.get_doc("Salary Slip", name) if name else new_slip(employee, company, start, end)
			if doc.docstatus != 0:
				frappe.throw(_("The salary slip is already posted."))
			doc.check_permission("write")
			if entry["inputs"] is not None:
				apply_inputs(doc, read_excel_inputs(*entry["inputs"]))
			if entry["cost_centers"]:
				apply_cost_centers(doc, read_excel_cost_centers(entry["cost_centers"]))
			doc.save()
			updated += 1
		except Exception as e:
			frappe.db.rollback(save_point="pd_import")
			errors.append({"employee": employee, "row": entry["row"], "error": last_error_message(e)})
	return {"updated": updated, "errors": errors}


def read_template_rows(file_url, month, year):
	"""The data rows of an uploaded sheet, once it is known to be this month's template."""
	from frappe.utils.xlsxutils import read_xlsx_file_from_attached_file

	try:
		rows = read_xlsx_file_from_attached_file(file_url=file_url) or []
	except Exception:
		frappe.throw(_("This file could not be read. Upload the Excel template as an .xlsx file."))
	header = [cell_text(value) for value in (rows[0] if rows else [])[: len(TEMPLATE_HEADER)]]
	if header != TEMPLATE_HEADER:
		frappe.throw(_("This file is not in the template layout. Use Month > Download Excel Template."))

	period = template_period(month, year)
	other_months = {cell_text(row[MONTH_COLUMN]) for row in rows[1:] if len(row) > MONTH_COLUMN} - {
		"",
		period,
	}
	if other_months:
		frappe.throw(
			_("This file is for {0}, but the page is on {1}.").format(", ".join(sorted(other_months)), period)
		)
	return rows[1:]


def group_excel_rows(rows):
	"""The sheet's rows per employee, and the rows that belong to nobody. A row with neither an employee
	code nor a name continues the employee above it."""
	grouped, errors, last_employee = {}, [], ""
	for excel_row, values in enumerate(rows, start=2):
		values = list(values) + [None] * (len(TEMPLATE_HEADER) - len(values))
		if not any(cell_text(value) for value in values[:MONTH_COLUMN]):
			continue
		employee = cell_text(values[0]) or ("" if cell_text(values[1]) else last_employee)
		if not employee:
			errors.append({"employee": "", "row": excel_row, "error": _("This row has no employee code.")})
			continue
		last_employee = employee
		entry = grouped.setdefault(
			employee, {"row": excel_row, "inputs": None, "second_inputs_row": None, "cost_centers": []}
		)
		if any(cell_text(value) for value in values[2:7]):
			if entry["inputs"] is None:
				entry["inputs"] = (excel_row, values)
			elif values[2:7] != entry["inputs"][1][2:7] and not entry["second_inputs_row"]:
				entry["second_inputs_row"] = excel_row
		if cell_text(values[7]):
			entry["cost_centers"].append((excel_row, values))
	return grouped, errors


def read_excel_inputs(excel_row, values):
	return {
		"custom_ot_hours": excel_number(values[2], "Overtime Hours", excel_row),
		"custom_late_hours": excel_number(values[3], "Late Hours", excel_row),
		"custom_absent_days": excel_number(values[4], "Absence Days", excel_row),
		"custom_other_deduction": excel_number(values[5], "Other Deduction", excel_row),
		"custom_other_deduction_remark": values[6] or "",
	}


def read_excel_cost_centers(rows):
	"""A single row with its days and hours blank follows the salary details: the template leaves Auto rows blank."""
	follows = len(rows) == 1 and not cell_text(rows[0][1][8]) and not cell_text(rows[0][1][9])
	return [
		{
			"cost_center": cell_text(values[7]),
			"basic_days": excel_number(values[8], "Days", excel_row),
			"ot_hours": excel_number(values[9], "Overtime Hours", excel_row),
			"follow_salary_details": 1 if follows else 0,
		}
		for excel_row, values in rows
	]


def excel_number(value, label, excel_row):
	if value is None or value == "":
		return 0.0
	try:
		number = float(str(value).strip().replace(",", ""))
	except ValueError:
		number = math.nan
	if not math.isfinite(number):
		frappe.throw(_("Row {0}: {1} must be a number. Found: {2}").format(excel_row, _(label), value))
	return number


def cell_text(value):
	return "" if value is None else str(value).strip()


def template_period(month, year):
	return f"{cint(year)}-{cint(month):02d}"


# ---------------------------------------------------------------- helpers


def find_slip(employee, company, start, end):
	return frappe.db.get_value(
		"Salary Slip",
		{
			"employee": employee,
			"company": company,
			"start_date": start,
			"end_date": end,
			"docstatus": ("<", 2),
		},
		"name",
	)


def new_slip(employee, company, start, end):
	if not frappe.db.exists("Employee", employee):
		frappe.throw(_("Employee {0} was not found.").format(employee))
	employee_company = frappe.db.get_value("Employee", employee, "company")
	if employee_company != company:
		frappe.throw(_("Employee {0} belongs to company {1}.").format(employee, employee_company))
	# The employee goes in when the document is built: HRMS v15 sets the slip's naming series
	# (Sal Slip/<employee>/#####) in the constructor, so a later doc.update() would name it .../None/...
	doc = frappe.get_doc(
		{
			"doctype": "Salary Slip",
			"employee": employee,
			"company": company,
			"start_date": start,
			"end_date": end,
			"posting_date": end,
			"payroll_frequency": "Monthly",
		}
	)
	doc.insert()
	return doc


def get_eligible_employees(company, start, end):
	"""Same rule Payroll Entry uses: active in the period, with a submitted Salary Structure Assignment."""
	Employee = frappe.qb.DocType("Employee")
	Assignment = frappe.qb.DocType("Salary Structure Assignment")
	rows = (
		frappe.qb.from_(Employee)
		.join(Assignment)
		.on(Employee.name == Assignment.employee)
		.select(Employee.name)
		.distinct()
		.where(
			(Employee.company == company)
			& (Employee.status != "Inactive")
			& ((Employee.date_of_joining <= end) | Employee.date_of_joining.isnull())
			& ((Employee.relieving_date >= start) | Employee.relieving_date.isnull())
			& (Assignment.docstatus == 1)
			& (Assignment.from_date <= end)
		)
		.orderby(Employee.name)
	).run(pluck=True)
	return rows


def apply_inputs(doc, values):
	for field in INPUT_FIELDS:
		if field in values:
			value = values.get(field)
			doc.set(field, value if field == "custom_other_deduction_remark" else flt(value))


def apply_cost_centers(doc, rows):
	doc.set("custom_cost_centers", [])
	for row in rows:
		if not row.get("cost_center"):
			continue
		doc.append(
			"custom_cost_centers",
			{
				"cost_center": row.get("cost_center"),
				"basic_days": flt(row.get("basic_days")),
				"ot_hours": flt(row.get("ot_hours")),
				"follow_salary_details": cint(row.get("follow_salary_details")),
			},
		)


def get_employee_header(employee):
	fields = [
		"name",
		"employee_name",
		"company",
		"branch",
		"department",
		"designation",
		"custom_staff_type",
		"salary_mode",
		"bank_name",
		"custom_sponsor",
		"custom_nationality",
		"date_of_joining",
		"relieving_date",
	]
	return frappe.db.get_value("Employee", employee, fields, as_dict=True) or {}


def slip_payload(doc):
	settings = get_settings()
	multiplier = flt(doc.custom_ot_multiplier) or 1
	quantities = {
		settings.overtime_component: (doc.custom_ot_hours, flt(doc.custom_hourly_rate) * multiplier),
		settings.lateness_component: (doc.custom_late_hours, doc.custom_hourly_rate),
		settings.absence_component: (doc.custom_absent_days, doc.custom_daily_rate),
	}

	def lines(parentfield):
		out = []
		for d in doc.get(parentfield) or []:
			qty, rate = quantities.get(d.salary_component, (None, None))
			out.append(
				{
					"salary_component": d.salary_component,
					"abbr": d.abbr,
					"qty": flt(qty) if qty is not None else None,
					"rate": flt(rate, 2) if rate is not None else None,
					"amount": flt(d.amount),
				}
			)
		return out

	return {
		"slip": {
			"name": doc.name,
			"docstatus": doc.docstatus,
			"status": doc.status,
			"modified": str(doc.modified),
			"employee": doc.employee,
			"employee_name": doc.employee_name,
			"company": doc.company,
			"currency": doc.currency,
			"start_date": str(doc.start_date),
			"end_date": str(doc.end_date),
			"journal_entry": doc.journal_entry,
			"earnings": lines("earnings"),
			"deductions": lines("deductions"),
			"gosi_employer_amount": flt(doc.custom_gosi_employer_amount),
			"gross_pay": flt(doc.gross_pay),
			"total_deduction": flt(doc.total_deduction),
			"net_pay": flt(doc.net_pay),
			"rounded_total": flt(doc.rounded_total),
			"inputs": {field: doc.get(field) for field in INPUT_FIELDS},
			"advance_balance": flt(doc.custom_advance_balance),
			"daily_rate": flt(doc.custom_daily_rate),
			"hourly_rate": flt(doc.custom_hourly_rate),
			"overtime_multiplier": multiplier,
			"days_basis": flt(doc.custom_days_basis),
			"payable_days": flt(doc.custom_payable_days),
			"cost_centers": [
				{
					field: r.get(field)
					for field in (*COST_CENTER_FIELDS, "basic_amount", "ot_amount", "row_total")
				}
				for r in doc.get("custom_cost_centers") or []
			],
			"distributed_days": flt(doc.custom_distributed_days),
			"distributed_ot_hours": flt(doc.custom_distributed_ot_hours),
			"distribution_total": flt(doc.custom_distribution_total),
			"distribution_difference": flt(doc.custom_distribution_difference),
			"unexplained_difference": flt(doc.custom_unexplained_difference),
		},
		"header": get_employee_header(doc.employee),
	}


def parse_json(value):
	if value is None or value == "":
		return None
	if isinstance(value, str):
		return json.loads(value)
	return value


def last_error_message(exc):
	"""Readable text of the last message Frappe logged for a failed operation, removing it from the popup queue."""
	from frappe.utils import strip_html

	message = ""
	log = getattr(frappe.local, "message_log", None) or []
	if log:
		last = log.pop()
		if isinstance(last, str):
			try:
				last = json.loads(last)
			except ValueError:
				last = {"message": last}
		message = last.get("message") if isinstance(last, dict) else str(last)
	return strip_html(str(message or exc or "")).strip() or exc.__class__.__name__
