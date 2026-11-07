app_name = "payroll_distribution"
app_title = "Payroll Distribution"
app_publisher = "GoLive Solutions"
app_description = "Monthly salary entry with per-employee cost center distribution for Frappe HRMS v16"
app_email = "mhmoud.soliman100@gmail.com"
app_license = "mit"

required_apps = ["erpnext", "hrms"]

# Installation
# ------------

after_install = "payroll_distribution.setup.install.after_install"
after_migrate = "payroll_distribution.setup.install.after_migrate"
before_uninstall = "payroll_distribution.setup.install.before_uninstall"

# Document Events
# ---------------

doc_events = {
	"Salary Slip": {
		"before_insert": "payroll_distribution.overrides.salary_slip.before_insert",
		"before_validate": "payroll_distribution.overrides.salary_slip.before_validate",
		"validate": "payroll_distribution.overrides.salary_slip.validate",
		"before_submit": "payroll_distribution.overrides.salary_slip.before_submit",
		"on_submit": "payroll_distribution.overrides.salary_slip.on_submit",
		"on_cancel": "payroll_distribution.overrides.salary_slip.on_cancel",
	},
}

# Fixtures
# --------
# Custom fields are created in code (setup/install.py) so they stay in sync on every migrate.

# Desk assets
# -----------

app_include_js = "payroll_distribution.bundle.js"
