// Copyright (c) 2026, GoLive Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Salary Cost by Cost Center"] = {
	filters: payroll_distribution.report_filters([
		{
			fieldname: "posted_only",
			label: __("Posted Salaries Only"),
			fieldtype: "Check",
		},
	]),
};
