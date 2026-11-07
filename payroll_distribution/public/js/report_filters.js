// Copyright (c) 2026, GoLive Solutions and contributors
// For license information, please see license.txt

frappe.provide("payroll_distribution");

payroll_distribution.month_options = () =>
	[
		"January",
		"February",
		"March",
		"April",
		"May",
		"June",
		"July",
		"August",
		"September",
		"October",
		"November",
		"December",
	].map((label, i) => ({ value: String(i + 1), label: __(label) }));

payroll_distribution.report_filters = (extra = []) => [
	{
		fieldname: "company",
		label: __("Company"),
		fieldtype: "Link",
		options: "Company",
		reqd: 1,
		default: frappe.defaults.get_user_default("Company"),
	},
	{
		fieldname: "month",
		label: __("Month"),
		fieldtype: "Select",
		options: payroll_distribution.month_options(),
		reqd: 1,
		default: String(new Date().getMonth() + 1),
	},
	{
		fieldname: "year",
		label: __("Year"),
		fieldtype: "Int",
		reqd: 1,
		default: new Date().getFullYear(),
	},
	{
		fieldname: "department",
		label: __("Department"),
		fieldtype: "Link",
		options: "Department",
	},
	{
		fieldname: "staff_type",
		label: __("Staff Type"),
		fieldtype: "Select",
		options: [
			{ value: "", label: "" },
			{ value: "Administrative", label: __("Administrative") },
			{ value: "Worker", label: __("Worker") },
		],
	},
	...extra,
];
