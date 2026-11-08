# Copyright (c) 2026, GoLive Solutions and contributors
# For license information, please see license.txt

import frappe
from frappe import _
from frappe.model.document import Document
from frappe.utils import flt


class PayrollDistributionSettings(Document):
	def validate(self):
		if (self.days_per_month or 0) <= 0:
			frappe.throw(_("Days per Month must be greater than zero."))
		if flt(self.hours_per_day) <= 0:
			frappe.throw(_("Hours per Day must be greater than zero."))
		if flt(self.overtime_multiplier) <= 0:
			self.overtime_multiplier = 1
		for field in ("gosi_employee_rate", "gosi_employer_rate"):
			if not 0 <= flt(self.get(field)) <= 100:
				frappe.throw(_("{0} must be between 0 and 100.").format(_(self.meta.get_label(field))))
		self.validate_payable_account()

	def validate_payable_account(self):
		if not self.payroll_payable_account:
			return
		root_type, is_group = frappe.db.get_value(
			"Account", self.payroll_payable_account, ["root_type", "is_group"]
		)
		if root_type != "Liability" or is_group:
			frappe.throw(
				_("Payroll Payable Account {0} must be a liability account that is not a group.").format(
					frappe.bold(self.payroll_payable_account)
				)
			)

	@frappe.whitelist()
	def create_salary_components(self):
		frappe.only_for(("System Manager", "HR Manager"))
		from payroll_distribution.setup.install import create_salary_components

		created = create_salary_components()
		if created:
			frappe.msgprint(
				_(
					"Created salary components: {0}. Add their accounts for each company before posting."
				).format(", ".join(created))
			)
		else:
			frappe.msgprint(_("All salary components already exist."))
		return created
