// Copyright (c) 2026, GoLive Solutions and contributors
// For license information, please see license.txt

frappe.pages["monthly-salary-entry"].on_page_load = function (wrapper) {
	const page = frappe.ui.make_app_page({
		parent: wrapper,
		title: __("Monthly Salary Entry"),
		single_column: true,
	});
	wrapper.salary_entry = new payroll_distribution.MonthlySalaryEntry(page);
};

frappe.pages["monthly-salary-entry"].on_page_show = function (wrapper) {
	wrapper.salary_entry && wrapper.salary_entry.context && wrapper.salary_entry.refresh_month();
};

frappe.provide("payroll_distribution");

// Also defined in payroll_distribution.bundle.js; repeated so the page works on its own.
payroll_distribution.month_options =
	payroll_distribution.month_options ||
	(() =>
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
		].map((label, i) => ({ value: String(i + 1), label: __(label) })));

payroll_distribution.MonthlySalaryEntry = class MonthlySalaryEntry {
	constructor(page) {
		this.page = page;
		this.payload = null;
		this.slips = [];
		this.dirty = false;
		this.cc_rows = [];
		this.add_style();
		this.make_filters();
		this.make_body();
		this.make_actions();
		this.setup_realtime();

		frappe.call("payroll_distribution.api.get_page_context").then((r) => {
			this.context = r.message;
			this.filters.company.set_value(this.context.company);
			this.filters.month.set_value(String(this.context.month));
			this.filters.year.set_value(this.context.year);
			this.toggle_permitted_actions();
			this.refresh_month();
		});
	}

	// ------------------------------------------------------------ layout

	make_filters() {
		this.filters = {};
		this.filters.company = this.page.add_field({
			fieldname: "company",
			label: __("Company"),
			fieldtype: "Link",
			options: "Company",
			reqd: 1,
			change: () => this.on_period_change(),
		});
		this.filters.month = this.page.add_field({
			fieldname: "month",
			label: __("Month"),
			fieldtype: "Select",
			options: payroll_distribution.month_options(),
			change: () => this.on_period_change(),
		});
		this.filters.year = this.page.add_field({
			fieldname: "year",
			label: __("Year"),
			fieldtype: "Int",
			change: () => this.on_period_change(),
		});
		this.filters.employee = this.page.add_field({
			fieldname: "employee",
			label: __("Employee"),
			fieldtype: "Link",
			options: "Employee",
			get_query: () => ({
				filters: { company: this.company(), status: ["!=", "Inactive"] },
			}),
			change: () => {
				const employee = this.filters.employee.get_value();
				if (employee && (!this.payload || this.current_employee() !== employee)) {
					this.load_employee(employee);
				}
			},
		});
	}

	make_body() {
		this.$body = $(`
			<div class="pd-entry">
				<div class="pd-month-bar"></div>
				<div class="pd-month-list hidden"></div>
				<div class="pd-placeholder text-muted">${__(
					"Choose a month and an employee, or use Next to go through the month's employees."
				)}</div>
				<div class="pd-no-slip hidden"></div>
				<div class="pd-slip hidden">
					<div class="pd-header"></div>
					<ul class="nav nav-tabs pd-tabs" role="tablist">
						<li class="nav-item"><a class="nav-link active" data-tab="salary" href="#">${__(
							"Salary Details"
						)}</a></li>
						<li class="nav-item"><a class="nav-link" data-tab="cost-centers" href="#">${__(
							"Cost Centers"
						)} <span class="pd-tab-flag"></span></a></li>
					</ul>
					<div class="pd-tab" data-tab="salary">
						<div class="row">
							<div class="col-md-6"><div class="pd-earnings"></div></div>
							<div class="col-md-6"><div class="pd-deductions"></div></div>
						</div>
						<div class="pd-section-title">${__("Monthly Inputs")}</div>
						<div class="pd-inputs"></div>
						<div class="pd-totals"></div>
					</div>
					<div class="pd-tab hidden" data-tab="cost-centers">
						<div class="pd-cc-grid"></div>
						<div class="pd-cc-actions"></div>
						<div class="pd-cc-summary"></div>
					</div>
				</div>
			</div>
		`).appendTo(this.page.main);

		this.$body.on("click", ".pd-tabs .nav-link", (e) => {
			e.preventDefault();
			this.show_tab($(e.currentTarget).attr("data-tab"));
		});
		this.$body.on("click", ".pd-month-bar .pd-toggle-list", (e) => {
			e.preventDefault();
			this.$body.find(".pd-month-list").toggleClass("hidden");
		});
		this.$body.on("click", ".pd-month-list [data-employee]", (e) => {
			e.preventDefault();
			this.go_to_employee($(e.currentTarget).attr("data-employee"));
		});

		this.make_inputs();
		this.make_cost_center_grid();
	}

	make_inputs() {
		const on_change = () => this.mark_dirty();
		this.inputs = new frappe.ui.FieldGroup({
			body: this.$body.find(".pd-inputs"),
			fields: [
				{
					fieldname: "custom_ot_hours",
					label: __("Overtime Hours"),
					fieldtype: "Float",
					precision: 2,
					change: on_change,
				},
				{
					fieldname: "custom_late_hours",
					label: __("Late Hours"),
					fieldtype: "Float",
					precision: 2,
					change: on_change,
				},
				{
					fieldname: "custom_absent_days",
					label: __("Absence Days"),
					fieldtype: "Float",
					precision: 2,
					change: on_change,
				},
				{ fieldtype: "Column Break" },
				{
					fieldname: "custom_other_deduction",
					label: __("Other Deduction"),
					fieldtype: "Currency",
					change: on_change,
				},
				{
					fieldname: "custom_other_deduction_remark",
					label: __("Other Deduction Remark"),
					fieldtype: "Small Text",
					change: on_change,
				},
			],
		});
		this.inputs.make();
	}

	make_cost_center_grid() {
		const me = this;
		const on_quantity_change = function () {
			// A row edited by hand stops following the salary details
			if (this.doc && ["basic_days", "ot_hours"].includes(this.df.fieldname)) {
				this.doc.follow_salary_details = 0;
			}
			me.mark_dirty();
			me.update_cost_center_preview();
		};
		this.cc_group = new frappe.ui.FieldGroup({
			body: this.$body.find(".pd-cc-grid"),
			fields: [
				{
					fieldname: "cost_centers",
					label: __("Cost Center Distribution"),
					fieldtype: "Table",
					cannot_add_rows: false,
					in_place_edit: true,
					data: [],
					get_data: () => this.cc_rows,
					fields: [
						{
							fieldname: "cost_center",
							label: __("Cost Center"),
							fieldtype: "Link",
							options: "Cost Center",
							in_list_view: 1,
							reqd: 1,
							columns: 3,
							get_query: () => ({
								filters: { company: this.company(), is_group: 0, disabled: 0 },
							}),
							onchange: on_quantity_change,
						},
						{
							fieldname: "basic_days",
							label: __("Days"),
							fieldtype: "Float",
							in_list_view: 1,
							columns: 1,
							onchange: on_quantity_change,
						},
						{
							fieldname: "basic_amount",
							label: __("Days Value"),
							fieldtype: "Currency",
							in_list_view: 1,
							read_only: 1,
							columns: 2,
						},
						{
							fieldname: "ot_hours",
							label: __("Overtime Hours"),
							fieldtype: "Float",
							in_list_view: 1,
							columns: 1,
							onchange: on_quantity_change,
						},
						{
							fieldname: "ot_amount",
							label: __("Overtime Value"),
							fieldtype: "Currency",
							in_list_view: 1,
							read_only: 1,
							columns: 2,
						},
						{
							fieldname: "follow_salary_details",
							label: __("Auto"),
							fieldtype: "Check",
							in_list_view: 1,
							columns: 1,
							onchange: function () {
								me.mark_dirty();
							},
						},
					],
				},
			],
		});
		this.cc_group.make();
		this.cc_field = this.cc_group.fields_dict.cost_centers;

		$(`<button class="btn btn-default btn-xs">${__("Assign Remaining")}</button>`)
			.appendTo(this.$body.find(".pd-cc-actions"))
			.on("click", () => this.assign_remaining());
		$(
			`<span class="text-muted small">${__(
				"Puts the days and overtime hours not yet distributed on the selected row, or on the last row."
			)}</span>`
		).appendTo(this.$body.find(".pd-cc-actions"));
	}

	make_actions() {
		this.page.set_primary_action(
			__("Save"),
			this.action(() => this.save()),
			"save"
		);
		frappe.ui.keys.add_shortcut({
			shortcut: "ctrl+s",
			action: () => this.save(),
			page: this.page,
			description: __("Save"),
			ignore_inputs: true,
		});

		this.page.add_inner_button(
			__("Previous"),
			this.action(() => this.step(-1))
		);
		this.page.add_inner_button(
			__("Next"),
			this.action(() => this.step(1))
		);

		const month = __("Month");
		this.btn_open_month = this.page.add_inner_button(
			__("Open Month"),
			this.action(() => this.open_month()),
			month
		);
		this.btn_post_month = this.page.add_inner_button(
			__("Post Month"),
			this.action(() => this.post_month()),
			month
		);
		this.page.add_inner_button(
			__("Download Excel Template"),
			this.action(() => this.download_template()),
			month
		);
		this.btn_import = this.page.add_inner_button(
			__("Import from Excel"),
			this.action(() => this.import_excel()),
			month
		);

		const employee = __("Employee");
		this.btn_post_employee = this.page.add_inner_button(
			__("Post Employee"),
			this.action(() => this.post_employee()),
			employee
		);
		this.btn_delete = this.page.add_inner_button(
			__("Delete Salary Slip"),
			this.action(() => this.delete_slip()),
			employee
		);
		this.page.add_inner_button(
			__("Print"),
			this.action(() => this.print_slip()),
			employee
		);
		this.page.add_inner_button(
			__("Open Salary Slip"),
			this.action(() => this.open_slip_form()),
			employee
		);

		const reports = __("Reports");
		[
			"Salary Differences",
			"Employees Without Salary",
			"Employees Not Distributed",
			"Salary Cost by Cost Center",
		].forEach((report) => {
			this.page.add_inner_button(
				__(report),
				this.action(() => this.open_report(report)),
				reports
			);
		});

		this.page.add_menu_item(__("Payroll Distribution Settings"), () =>
			frappe.set_route("Form", "Payroll Distribution Settings")
		);
	}

	// Toolbar handlers return frappe.call's jQuery promise. Frappe v15's page toolbar calls .finally()
	// on whatever a handler returns, which a jQuery promise lacks, so hand it a standard Promise.
	action(fn) {
		return () => {
			const result = fn();
			return result && result.then ? Promise.resolve(result).catch(() => {}) : result;
		};
	}

	toggle_permitted_actions() {
		const ctx = this.context || {};
		const toggle = (btn, show) => btn && btn.toggle(!!show);
		toggle(this.btn_open_month, ctx.can_create);
		toggle(this.btn_import, ctx.can_create);
		toggle(this.btn_post_month, ctx.can_submit);
		toggle(this.btn_post_employee, ctx.can_submit);
		toggle(this.btn_delete, ctx.can_delete);
	}

	setup_realtime() {
		frappe.realtime.on("payroll_distribution_done", (data) => {
			if (data.action === "post_month") {
				this.show_post_result(data);
			} else {
				this.show_open_result(data);
			}
			this.refresh_month();
		});
	}

	// ------------------------------------------------------------ state

	company() {
		return this.filters.company.get_value();
	}

	period() {
		return {
			company: this.company(),
			month: this.filters.month.get_value(),
			year: this.filters.year.get_value(),
		};
	}

	period_ready() {
		const p = this.period();
		return p.company && p.month && p.year;
	}

	current_employee() {
		return this.payload && this.payload.header && this.payload.header.name;
	}

	current_slip() {
		return this.payload && this.payload.slip;
	}

	is_editable() {
		const slip = this.current_slip();
		return slip && slip.docstatus === 0;
	}

	mark_dirty() {
		if (this.loading || !this.is_editable()) return;
		this.dirty = true;
		this.page.set_indicator(__("Not Saved"), "orange");
	}

	confirm_discard(proceed) {
		if (!this.dirty) return proceed();
		frappe.confirm(__("You have unsaved changes for this employee. Discard them?"), () => {
			this.dirty = false;
			proceed();
		});
	}

	on_period_change() {
		if (!this.context) return;
		this.confirm_discard(() => {
			this.refresh_month().then(() => {
				const employee = this.filters.employee.get_value();
				if (employee) this.load_employee(employee);
			});
		});
	}

	// ------------------------------------------------------------ month

	refresh_month() {
		if (!this.period_ready()) return Promise.resolve();
		return frappe.call("payroll_distribution.api.get_month_slips", this.period()).then((r) => {
			this.slips = r.message || [];
			this.render_month_bar();
		});
	}

	render_month_bar() {
		const tolerance = flt(this.context.difference_tolerance) || 0.01;
		const posted = this.slips.filter((s) => s.docstatus === 1).length;
		const unexplained = this.slips.filter(
			(s) => Math.abs(flt(s.custom_unexplained_difference)) >= tolerance
		).length;
		const label = this.slips.length
			? __("{0} salary slips this month: {1} posted, {2} with an unexplained difference.", [
					this.slips.length,
					posted,
					unexplained,
				])
			: __("No salary slips for this month yet. Use Month > Open Month to prepare them.");
		this.$body.find(".pd-month-bar").html(`
			<span>${label}</span>
			${this.slips.length ? `<a href="#" class="pd-toggle-list">${__("Show or hide the list")}</a>` : ""}
		`);

		const rows = this.slips
			.map((s) => {
				const flag =
					Math.abs(flt(s.custom_unexplained_difference)) >= tolerance
						? `<span class="indicator-pill red">${format_currency(
								s.custom_unexplained_difference
							)}</span>`
						: "";
				const status =
					s.docstatus === 1
						? `<span class="indicator-pill green">${__("Posted")}</span>`
						: `<span class="indicator-pill gray">${__("Draft")}</span>`;
				return `<tr>
					<td><a href="#" data-employee="${frappe.utils.escape_html(s.employee)}">${frappe.utils.escape_html(
						s.employee
					)}</a></td>
					<td>${frappe.utils.escape_html(s.employee_name || "")}</td>
					<td class="text-right">${format_currency(s.net_pay)}</td>
					<td>${status}</td>
					<td>${flag}</td>
				</tr>`;
			})
			.join("");
		this.$body.find(".pd-month-list").html(`
			<table class="table table-sm table-hover">
				<thead><tr>
					<th>${__("Employee")}</th><th>${__("Employee Name")}</th>
					<th class="text-right">${__("Net Salary")}</th><th>${__("Status")}</th>
					<th>${__("Unexplained Difference")}</th>
				</tr></thead>
				<tbody>${rows}</tbody>
			</table>
		`);
	}

	step(direction) {
		if (!this.slips.length) {
			frappe.show_alert({
				message: __("No salary slips for this month yet."),
				indicator: "orange",
			});
			return;
		}
		const index = this.slips.findIndex((s) => s.employee === this.current_employee());
		let next = index + direction;
		if (index === -1) next = direction > 0 ? 0 : this.slips.length - 1;
		if (next < 0 || next >= this.slips.length) {
			frappe.show_alert({
				message: __("No more employees in this month."),
				indicator: "blue",
			});
			return;
		}
		this.go_to_employee(this.slips[next].employee);
	}

	go_to_employee(employee) {
		this.confirm_discard(() => this.load_employee(employee));
	}

	// ------------------------------------------------------------ employee

	load_employee(employee, create = 0) {
		if (!this.period_ready()) {
			frappe.msgprint(__("Choose the company, month and year first."));
			return;
		}
		return frappe
			.call({
				method: "payroll_distribution.api.get_slip",
				args: { ...this.period(), employee, create },
				freeze: !!create,
			})
			.then((r) => this.render(r.message));
	}

	render(payload) {
		this.loading = true;
		this.payload = payload;
		this.dirty = false;
		if (payload.header && this.filters.employee.get_value() !== payload.header.name) {
			this.filters.employee.set_value(payload.header.name);
		}
		this.$body.find(".pd-placeholder").addClass("hidden");

		if (!payload.slip) {
			this.render_no_slip(payload.header);
			this.loading = false;
			return;
		}
		this.$body.find(".pd-no-slip").addClass("hidden");
		this.$body.find(".pd-slip").removeClass("hidden");

		const slip = payload.slip;
		this.render_header(payload.header, slip);
		this.render_lines(
			".pd-earnings",
			__("Earnings", null, "Payroll Distribution"),
			slip.earnings,
			slip.currency
		);
		this.render_lines(
			".pd-deductions",
			__("Deductions", null, "Payroll Distribution"),
			slip.deductions,
			slip.currency
		);
		this.values_set = this.inputs.set_values(slip.inputs);
		this.render_totals(slip);

		this.cc_rows = (slip.cost_centers || []).map((row) => ({ ...row }));
		this.cc_field.df.data = this.cc_rows;
		this.cc_field.grid.refresh();
		this.render_cc_summary();

		this.set_read_only(slip.docstatus !== 0);
		// Field change events fire asynchronously after set_values; ignore them before marking clean.
		Promise.resolve(this.values_set)
			.then(() => frappe.timeout(0.2))
			.then(() => {
				if (this.payload !== payload) return;
				this.loading = false;
				this.dirty = false;
				this.page.set_indicator(
					slip.docstatus === 1 ? __("Posted") : __("Draft"),
					slip.docstatus === 1 ? "green" : "gray"
				);
			});
	}

	render_no_slip(header) {
		this.$body.find(".pd-slip").addClass("hidden");
		const can_create = this.context && this.context.can_create;
		const $box = this.$body.find(".pd-no-slip").removeClass("hidden").html(`
			<p>${__("{0} has no salary slip for this month.", [
				`<b>${frappe.utils.escape_html(header.employee_name || header.name || "")}</b>`,
			])}</p>
			${
				can_create
					? `<button class="btn btn-primary btn-sm pd-create">${__(
							"Create Salary Slip"
						)}</button>`
					: ""
			}
		`);
		$box.find(".pd-create").on("click", () => this.load_employee(header.name, 1));
		this.page.clear_indicator();
	}

	render_header(header, slip) {
		const value = (v) =>
			frappe.utils.escape_html(v || "") || `<span class="text-muted">-</span>`;
		const items = [
			[__("Employee"), `${value(header.name)}`],
			[__("Employee Name"), value(header.employee_name)],
			[__("Department"), value(header.department)],
			[__("Designation"), value(header.designation)],
			[__("Branch"), value(header.branch)],
			[__("Staff Type"), value(header.custom_staff_type && __(header.custom_staff_type))],
			[
				__("Salary Payment"),
				value(
					[header.salary_mode && __(header.salary_mode), header.bank_name]
						.filter(Boolean)
						.join(" - ")
				),
			],
			[__("Sponsor (Kafeel)"), value(header.custom_sponsor)],
			[__("Nationality"), value(header.custom_nationality && __(header.custom_nationality))],
			[
				__("Outstanding Advance Balance"),
				format_currency(slip.advance_balance, slip.currency),
			],
		];
		const je = slip.journal_entry
			? `<div class="pd-je">${__("Journal Entry")}: <a href="${frappe.utils.get_form_link(
					"Journal Entry",
					slip.journal_entry
				)}">${frappe.utils.escape_html(slip.journal_entry)}</a></div>`
			: "";
		this.$body.find(".pd-header").html(`
			<div class="pd-header-grid">
				${items
					.map(
						([label, v]) =>
							`<div><div class="pd-label">${label}</div><div class="pd-value">${v}</div></div>`
					)
					.join("")}
			</div>
			${je}
		`);
	}

	render_lines(selector, title, lines, currency) {
		const rows = (lines || [])
			.map(
				(l) => `<tr>
				<td>${frappe.utils.escape_html(__(l.salary_component))}</td>
				<td class="text-right">${l.qty === null || l.qty === undefined ? "" : format_number(l.qty, null, 2)}</td>
				<td class="text-right">${l.rate === null || l.rate === undefined ? "" : format_number(l.rate, null, 2)}</td>
				<td class="text-right">${format_currency(l.amount, currency)}</td>
			</tr>`
			)
			.join("");
		this.$body.find(selector).html(`
			<div class="pd-section-title">${title}</div>
			<table class="table table-sm table-bordered pd-lines">
				<thead><tr>
					<th>${__("Component")}</th>
					<th class="text-right">${__("Quantity")}</th>
					<th class="text-right">${__("Rate")}</th>
					<th class="text-right">${__("Amount", null, "Payroll Distribution")}</th>
				</tr></thead>
				<tbody>${rows || `<tr><td colspan="4" class="text-muted">${__("None")}</td></tr>`}</tbody>
			</table>
		`);
	}

	render_totals(slip) {
		const c = slip.currency;
		this.$body.find(".pd-totals").html(`
			<div class="pd-total"><div class="pd-label">${__("Total Earnings")}</div><div>${format_currency(
				slip.gross_pay,
				c
			)}</div></div>
			<div class="pd-total"><div class="pd-label">${__("Total Deductions")}</div><div>${format_currency(
				slip.total_deduction,
				c
			)}</div></div>
			<div class="pd-total pd-net"><div class="pd-label">${__("Net Salary")}</div><div>${format_currency(
				slip.net_pay,
				c
			)}</div></div>
		`);
	}

	set_read_only(read_only) {
		Object.values(this.inputs.fields_dict).forEach((field) => {
			if (field.df.fieldtype === "Column Break") return;
			field.df.read_only = read_only ? 1 : 0;
			field.refresh();
		});
		this.cc_field.df.read_only = read_only ? 1 : 0;
		this.cc_field.df.cannot_add_rows = read_only;
		this.cc_field.df.cannot_delete_rows = read_only;
		this.cc_field.grid.refresh();
		this.$body.find(".pd-cc-actions").toggleClass("hidden", !!read_only);
		this.page.btn_primary.toggle(!read_only);
	}

	show_tab(tab) {
		this.$body.find(".pd-tabs .nav-link").removeClass("active");
		this.$body.find(`.pd-tabs .nav-link[data-tab="${tab}"]`).addClass("active");
		this.$body.find(".pd-tab").addClass("hidden");
		this.$body.find(`.pd-tab[data-tab="${tab}"]`).removeClass("hidden");
		if (tab === "cost-centers") this.cc_field.grid.refresh();
	}

	// ------------------------------------------------------------ cost centers

	grid_rows() {
		return (
			(this.cc_field.grid.get_data && this.cc_field.grid.get_data()) ||
			this.cc_field.df.data ||
			[]
		);
	}

	update_cost_center_preview() {
		const slip = this.current_slip();
		if (!slip) return;
		const precision = 2;
		this.grid_rows().forEach((row) => {
			row.basic_amount = flt(flt(row.basic_days) * slip.daily_rate, precision);
			row.ot_amount = flt(
				flt(row.ot_hours) * slip.hourly_rate * slip.overtime_multiplier,
				precision
			);
			row.row_total = flt(row.basic_amount + row.ot_amount, precision);
			const grid_row = this.cc_field.grid.grid_rows_by_docname[row.name];
			if (grid_row) {
				grid_row.refresh_field("basic_amount");
				grid_row.refresh_field("ot_amount");
			}
		});
		this.render_cc_summary(true);
	}

	render_cc_summary(preview = false) {
		const slip = this.current_slip();
		if (!slip) return;
		const rows = this.grid_rows();
		const c = slip.currency;
		const tolerance = flt(this.context && this.context.difference_tolerance) || 0.01;
		const days = rows.reduce((sum, r) => sum + flt(r.basic_days), 0);
		const hours = rows.reduce((sum, r) => sum + flt(r.ot_hours), 0);
		const total = rows.reduce((sum, r) => sum + flt(r.row_total), 0);
		const difference = preview ? flt(total - slip.net_pay, 2) : slip.distribution_difference;
		const unexplained = preview
			? flt(difference - (slip.distribution_difference - slip.unexplained_difference), 2)
			: slip.unexplained_difference;
		const cls = (v) =>
			Math.abs(flt(v)) >= tolerance ? "text-danger font-weight-bold" : "text-success";

		this.$body.find(".pd-cc-summary").html(`
			<div class="pd-summary-grid">
				<div><div class="pd-label">${__("Payable Days")}</div><div>${format_number(slip.payable_days, null, 2)}</div></div>
				<div><div class="pd-label">${__("Distributed Days")}</div><div>${format_number(days, null, 2)}</div></div>
				<div><div class="pd-label">${__("Overtime Hours Entered")}</div><div>${format_number(
					flt(slip.inputs.custom_ot_hours),
					null,
					2
				)}</div></div>
				<div><div class="pd-label">${__("Overtime Hours Distributed")}</div><div>${format_number(
					hours,
					null,
					2
				)}</div></div>
				<div><div class="pd-label">${__("Distribution Total")}</div><div>${format_currency(total, c)}</div></div>
				<div><div class="pd-label">${__("Net Salary")}</div><div>${format_currency(slip.net_pay, c)}</div></div>
				<div><div class="pd-label">${__("Difference")}</div><div class="${cls(difference)}">${format_currency(
					difference,
					c
				)}</div></div>
				<div><div class="pd-label">${__("Not Explained by Deductions")}</div><div class="${cls(
					unexplained
				)}">${format_currency(unexplained, c)}</div></div>
			</div>
			${
				preview
					? `<div class="text-muted small">${__(
							"Save to recalculate the salary and the difference."
						)}</div>`
					: ""
			}
		`);
		this.$body
			.find(".pd-tab-flag")
			.html(
				Math.abs(flt(unexplained)) >= tolerance
					? `<span class="indicator-pill red">${__("Difference")}</span>`
					: ""
			);
	}

	assign_remaining() {
		const slip = this.current_slip();
		if (!this.is_editable()) return;
		const rows = this.grid_rows();
		if (!rows.length) {
			frappe.msgprint(__("Add a cost center row first."));
			return;
		}
		const selected = this.cc_field.grid.get_selected_children();
		const target = (selected && selected[0]) || rows[rows.length - 1];
		const days = rows.reduce((sum, r) => sum + flt(r.basic_days), 0);
		const hours = rows.reduce((sum, r) => sum + flt(r.ot_hours), 0);
		const ot_entered = flt(this.inputs.get_value("custom_ot_hours"));
		target.basic_days = flt(
			flt(target.basic_days) + Math.max(flt(slip.payable_days) - days, 0),
			2
		);
		target.ot_hours = flt(flt(target.ot_hours) + Math.max(ot_entered - hours, 0), 2);
		target.follow_salary_details = 0;
		this.cc_field.grid.refresh();
		this.mark_dirty();
		this.update_cost_center_preview();
	}

	// ------------------------------------------------------------ actions

	save() {
		const slip = this.current_slip();
		if (!slip) return Promise.resolve();
		if (!this.is_editable()) return Promise.resolve();
		const rows = this.grid_rows()
			.filter((r) => r.cost_center)
			.map((r) => ({
				cost_center: r.cost_center,
				basic_days: flt(r.basic_days),
				ot_hours: flt(r.ot_hours),
				follow_salary_details: cint(r.follow_salary_details),
			}));
		return frappe
			.call({
				method: "payroll_distribution.api.save_slip",
				args: {
					name: slip.name,
					values: this.inputs.get_values(true),
					cost_centers: rows,
					modified: slip.modified,
				},
				freeze: true,
				freeze_message: __("Saving..."),
			})
			.then((r) => {
				this.render(r.message);
				frappe.show_alert({ message: __("Saved"), indicator: "green" });
				this.refresh_month();
			});
	}

	ensure_saved() {
		return this.dirty ? this.save() : Promise.resolve();
	}

	delete_slip() {
		const slip = this.current_slip();
		if (!slip) return;
		if (slip.docstatus !== 0) {
			frappe.msgprint(__("A posted salary slip can't be deleted."));
			return;
		}
		frappe.confirm(
			__("Delete the salary slip of {0} for this month?", [
				frappe.utils.escape_html(slip.employee_name),
			]),
			() => {
				frappe
					.call("payroll_distribution.api.delete_slip", { name: slip.name })
					.then(() => {
						this.dirty = false;
						frappe.show_alert({ message: __("Deleted"), indicator: "green" });
						this.refresh_month();
						this.load_employee(slip.employee);
					});
			}
		);
	}

	post_employee() {
		const slip = this.current_slip();
		if (!slip) return;
		if (slip.docstatus !== 0) {
			frappe.msgprint(__("This salary slip is already posted."));
			return;
		}
		frappe.confirm(
			__(
				"Post the salary of {0}? This creates the journal entry, and the slip can no longer be edited.",
				[frappe.utils.escape_html(slip.employee_name)]
			),
			() => {
				this.ensure_saved().then(() => {
					frappe
						.call({
							method: "payroll_distribution.api.post_employee",
							args: { name: this.current_slip().name },
							freeze: true,
							freeze_message: __("Posting..."),
						})
						.then((r) => {
							this.render(r.message);
							frappe.show_alert({ message: __("Posted"), indicator: "green" });
							this.refresh_month();
						});
				});
			}
		);
	}

	open_month() {
		if (!this.period_ready()) return;
		frappe
			.call({
				method: "payroll_distribution.api.open_month",
				args: this.period(),
				freeze: true,
				freeze_message: __("Preparing salary slips..."),
			})
			.then((r) => {
				this.show_open_result(r.message);
				this.refresh_month();
			});
	}

	show_open_result(result) {
		if (result.queued) {
			frappe.msgprint(
				__(
					"Preparing {0} salary slips in the background. You'll get a message when it's done.",
					[result.count]
				)
			);
			return;
		}
		let message = __("Created {0} draft salary slips.", [result.created]);
		if (result.failed && result.failed.length) {
			message += `<br><br>${__("These employees could not be prepared:")}<br>${this.error_table(
				result.failed
			)}`;
		}
		frappe.msgprint({
			title: __("Open Month"),
			message,
			indicator: result.failed?.length ? "orange" : "green",
		});
	}

	post_month() {
		if (!this.period_ready()) return;
		frappe.confirm(
			__(
				"Post every unposted salary slip of this month? Slips without a cost center distribution are skipped and listed."
			),
			() => {
				this.ensure_saved().then(() => {
					frappe
						.call({
							method: "payroll_distribution.api.post_month",
							args: this.period(),
							freeze: true,
							freeze_message: __("Posting the month..."),
						})
						.then((r) => {
							this.show_post_result(r.message);
							this.refresh_month();
							const employee = this.current_employee();
							if (employee) this.load_employee(employee);
						});
				});
			}
		);
	}

	show_post_result(result) {
		if (result.queued) {
			frappe.msgprint(
				__(
					"Posting {0} salary slips in the background. You'll get a message when it's done.",
					[result.count]
				)
			);
			return;
		}
		const entries = (result.journal_entries || [])
			.map(
				(je) =>
					`<a href="${frappe.utils.get_form_link("Journal Entry", je)}">${frappe.utils.escape_html(je)}</a>`
			)
			.join(", ");
		let message = __("Posted {0} salary slips.", [result.submitted]);
		if (entries) message += `<br>${__("Journal Entry")}: ${entries}`;
		if (result.refused && result.refused.length) {
			message += `<br><br>${__("These salary slips were not posted:")}<br>${this.error_table(result.refused)}`;
		}
		frappe.msgprint({
			title: __("Post Month"),
			message,
			indicator: result.refused?.length ? "orange" : "green",
		});
	}

	error_table(rows) {
		return `<table class="table table-sm table-bordered">
			<thead><tr><th>${__("Employee")}</th><th>${__("Error")}</th></tr></thead>
			<tbody>${rows
				.map(
					(r) => `<tr>
					<td>${frappe.utils.escape_html(r.employee_name || r.employee || "")}${
						r.row ? ` (${__("row {0}", [r.row])})` : ""
					}</td>
					<td>${frappe.utils.escape_html(r.error || "")}</td>
				</tr>`
				)
				.join("")}</tbody>
		</table>`;
	}

	download_template() {
		if (!this.period_ready()) return;
		const args = this.period();
		const query = Object.keys(args)
			.map((k) => `${k}=${encodeURIComponent(args[k])}`)
			.join("&");
		window.open(`/api/method/payroll_distribution.api.download_template?${query}`);
	}

	import_excel() {
		if (!this.period_ready()) return;
		new frappe.ui.FileUploader({
			restrictions: { allowed_file_types: [".xlsx"] },
			make_attachments_public: false,
			on_success: (file) => {
				frappe
					.call({
						method: "payroll_distribution.api.import_month",
						args: { ...this.period(), file_url: file.file_url },
						freeze: true,
						freeze_message: __("Importing..."),
					})
					.then((r) => {
						const result = r.message;
						let message = __("Updated {0} salary slips.", [result.updated]);
						if (result.errors && result.errors.length) {
							message += `<br><br>${__("These rows were not imported:")}<br>${this.error_table(
								result.errors
							)}`;
						}
						frappe.msgprint({
							title: __("Import from Excel"),
							message,
							indicator: result.errors?.length ? "orange" : "green",
						});
						this.refresh_month();
						const employee = this.current_employee();
						if (employee) this.load_employee(employee);
					});
			},
		});
	}

	print_slip() {
		const slip = this.current_slip();
		if (!slip) return;
		frappe.set_route("print", "Salary Slip", slip.name);
	}

	open_slip_form() {
		const slip = this.current_slip();
		if (!slip) return;
		frappe.set_route("Form", "Salary Slip", slip.name);
	}

	open_report(report) {
		frappe.route_options = this.period();
		frappe.set_route("query-report", report);
	}

	add_style() {
		if (document.getElementById("pd-entry-style")) return;
		$(`<style id="pd-entry-style">
			.pd-entry { padding: var(--padding-md) 0; }
			.pd-month-bar { display: flex; gap: var(--margin-md); align-items: center; flex-wrap: wrap;
				padding: var(--padding-sm) var(--padding-md); background: var(--subtle-fg); border-radius: var(--border-radius);
				margin-bottom: var(--margin-md); }
			.pd-month-list { margin-bottom: var(--margin-md); max-height: 320px; overflow: auto; }
			.pd-placeholder, .pd-no-slip { padding: var(--padding-lg); text-align: center; }
			.pd-header { border: 1px solid var(--border-color); border-radius: var(--border-radius);
				padding: var(--padding-md); margin-bottom: var(--margin-md); }
			.pd-header-grid, .pd-summary-grid { display: grid; grid-template-columns: repeat(auto-fill, minmax(180px, 1fr));
				gap: var(--margin-sm) var(--margin-md); }
			.pd-label { color: var(--text-muted); font-size: var(--text-sm); }
			.pd-value { font-weight: 500; }
			.pd-je { margin-top: var(--margin-sm); }
			.pd-tabs { margin-bottom: var(--margin-md); }
			.pd-tab > .row { margin-left: 0; margin-right: 0; }
			.pd-tab > .row > [class*="col-"] { padding-left: var(--padding-xs); padding-right: var(--padding-xs); }
			.pd-section-title { font-weight: 600; margin: var(--margin-sm) 0; }
			.pd-lines td, .pd-lines th { font-variant-numeric: tabular-nums; }
			.pd-totals { display: flex; gap: var(--margin-lg); justify-content: flex-end; flex-wrap: wrap;
				border-top: 1px solid var(--border-color); padding-top: var(--padding-sm); margin-top: var(--margin-sm); }
			.pd-total > div:last-child { font-size: var(--text-lg); font-variant-numeric: tabular-nums; }
			.pd-net > div:last-child { font-weight: 700; }
			.pd-cc-actions { display: flex; gap: var(--margin-sm); align-items: center; margin: var(--margin-sm) 0 var(--margin-md); }
			.pd-cc-summary { border: 1px solid var(--border-color); border-radius: var(--border-radius); padding: var(--padding-md); }
		</style>`).appendTo("head");
	}
};
