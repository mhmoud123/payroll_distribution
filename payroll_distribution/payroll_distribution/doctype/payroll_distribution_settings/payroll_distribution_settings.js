// Copyright (c) 2026, GoLive Solutions and contributors
// For license information, please see license.txt

frappe.ui.form.on("Payroll Distribution Settings", {
	setup(frm) {
		frm.set_query("payroll_payable_account", () => ({
			filters: { root_type: "Liability", is_group: 0 },
		}));
	},

	refresh(frm) {
		frm.add_custom_button(__("Monthly Salary Entry"), () => {
			frappe.set_route("monthly-salary-entry");
		});
	},

	create_salary_components(frm) {
		frm.call("create_salary_components").then(() => frm.reload_doc());
	},
});
