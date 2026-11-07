// Copyright (c) 2026, GoLive Solutions and contributors
// For license information, please see license.txt

frappe.query_reports["Salary Differences"] = {
	filters: payroll_distribution.report_filters([
		{
			fieldname: "only_unexplained",
			label: __("Only Unexplained Differences"),
			fieldtype: "Check",
		},
	]),
	formatter(value, row, column, data, default_formatter) {
		value = default_formatter(value, row, column, data);
		if (column.fieldname === "unexplained" && data && Math.abs(data.unexplained || 0) >= 0.01) {
			value = `<span class="text-danger font-weight-bold">${value}</span>`;
		}
		return value;
	},
};
