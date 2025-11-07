// Tints & Audio — Manager MVP
// Phase 2-3: Client-side data, CRUD, tables, search, KPIs, settings, export/import/reset, toasts

(function () {
  const $ = (sel, ctx = document) => ctx.querySelector(sel);
  const $$ = (sel, ctx = document) => Array.from(ctx.querySelectorAll(sel));

  // ----- Storage & State -----
  const LS = {
    customers: 'tints_customers',
    appointments: 'tints_appointments',
    sales: 'tints_sales',
    settings: 'tints_settings',
  };

  const state = {
    customers: [],
    appointments: [],
    sales: [],
    settings: {},
    // transient editing ids
    editing: {
      customerId: null,
      appointmentId: null,
      saleId: null,
    },
  };

  const moneyFmt = new Intl.NumberFormat(undefined, { style: 'currency', currency: guessCurrency() });

  function guessCurrency() {
    // Default to USD; attempt Intl to guess from locale
    try {
      const region = new Intl.NumberFormat().resolvedOptions().locale;
      // naive mapping
      if (/en-GB|GB|UK/i.test(region)) return 'GBP';
      if (/en-AU|AU/i.test(region)) return 'AUD';
      if (/en-CA|CA/i.test(region)) return 'CAD';
      if (/de|fr|es|it|nl|fi|pt|eu/i.test(region)) return 'EUR';
      return 'USD';
    } catch { return 'USD'; }
  }

  function loadArray(key) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return [];
      const parsed = JSON.parse(raw);
      return Array.isArray(parsed) ? parsed : [];
    } catch { return []; }
  }
  function loadObject(key) {
    try {
      const raw = localStorage.getItem(key);
      if (!raw) return {};
      const parsed = JSON.parse(raw);
      return parsed && typeof parsed === 'object' && !Array.isArray(parsed) ? parsed : {};
    } catch { return {}; }
  }
  function save(key, value) {
    try { localStorage.setItem(key, JSON.stringify(value)); } catch {}
  }
  function remove(key) {
    try { localStorage.removeItem(key); } catch {}
  }
  function uid(prefix = 'id') {
    return `${prefix}_${Date.now().toString(36)}_${Math.random().toString(36).slice(2, 8)}`;
  }
  function toNumber(v) {
    const n = typeof v === 'number' ? v : parseFloat(String(v).replace(/[^0-9.-]/g, ''));
    return Number.isFinite(n) ? n : 0;
  }
  function isTodayOrFuture(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr + 'T00:00:00');
    const today = new Date();
    today.setHours(0,0,0,0);
    return d >= today;
  }
  function thisMonth(dateStr) {
    if (!dateStr) return false;
    const d = new Date(dateStr + 'T00:00:00');
    const now = new Date();
    return d.getUTCFullYear() === now.getUTCFullYear() && d.getUTCMonth() === now.getUTCMonth();
  }

  // ----- UI Helpers -----
  function setYear() {
    const yearEl = $("#year");
    if (yearEl) yearEl.textContent = String(new Date().getFullYear());
  }

  function initTabs() {
    const tabs = $$(".tab");
    const pages = $$(".page");
    tabs.forEach((btn) => {
      btn.addEventListener("click", () => {
        tabs.forEach((b) => b.classList.remove("active"));
        btn.classList.add("active");
        const target = btn.dataset.target;
        pages.forEach((p) => p.classList.toggle("active", p.id === target));
      });
    });
  }

  function badge(text) {
    const t = String(text || '').toLowerCase();
    let cls = 'badge';
    if (t === 'completed') cls += ' success';
    if (t === 'cancelled') cls += ' warn';
    return `<span class="${cls}">${escapeHtml(text)}</span>`;
  }

  function escapeHtml(str) {
    return String(str ?? '')
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#039;');
  }

  function emptyRow(cols, text = 'No records') {
    return `<tr><td colspan="${cols}" class="muted">${text}</td></tr>`;
  }

  // ----- Toasts -----
  function toast(msg, type = 'info') {
    const container = $('#toast-container');
    if (!container) return alert(msg);
    const el = document.createElement('div');
    el.className = `toast ${type}`;
    el.textContent = msg;
    container.appendChild(el);
    requestAnimationFrame(() => el.classList.add('show'));
    setTimeout(() => {
      el.classList.remove('show');
      setTimeout(() => el.remove(), 250);
    }, 2500);
  }

  // ----- Renderers -----
  function renderCustomers() {
    const tbody = $("#customers-table tbody");
    const q = $("#customer-search").value.trim().toLowerCase();
    const rows = state.customers
      .filter(c => !q || [c.name, c.phone, c.email, c.vehicle].some(v => String(v||'').toLowerCase().includes(q)))
      .sort((a, b) => (a.name||'').localeCompare(b.name||''))
      .map(c => `
        <tr data-id="${c.id}">
          <td>${escapeHtml(c.name)}</td>
          <td>${escapeHtml(c.phone)}</td>
          <td>${escapeHtml(c.email)}</td>
          <td>${escapeHtml(c.vehicle)}</td>
          <td>
            <div class="row-actions">
              <button class="btn" data-action="edit-customer">Edit</button>
              <button class="btn danger" data-action="delete-customer">Delete</button>
            </div>
          </td>
        </tr>`)
      .join('');
    tbody.innerHTML = rows || emptyRow(5, 'No customers yet');
    updateKpis();
  }

  function renderAppointments() {
    const tbody = $("#appointments-table tbody");
    const q = $("#appointment-search").value.trim().toLowerCase();
    const rows = state.appointments
      .filter(a => !q || [a.date, a.time, a.customer, a.vehicle, a.service, a.status].some(v => String(v||'').toLowerCase().includes(q)))
      .sort((a, b) => String(a.date||'').localeCompare(String(b.date||'')) || String(a.time||'').localeCompare(String(b.time||'')))
      .map(a => `
        <tr data-id="${a.id}">
          <td>${escapeHtml(a.date)}</td>
          <td>${escapeHtml(a.time)}</td>
          <td>${escapeHtml(a.customer)}</td>
          <td>${escapeHtml(a.vehicle)}</td>
          <td>${escapeHtml(a.service)}</td>
          <td>${moneyFmt.format(toNumber(a.price))}</td>
          <td>${badge(a.status)}</td>
          <td>
            <div class="row-actions">
              <button class="btn" data-action="edit-appointment">Edit</button>
              <button class="btn danger" data-action="delete-appointment">Delete</button>
            </div>
          </td>
        </tr>`)
      .join('');
    tbody.innerHTML = rows || emptyRow(8, 'No appointments yet');
    updateKpis();
  }

  function renderSales() {
    const tbody = $("#sales-table tbody");
    const q = $("#sales-search").value.trim().toLowerCase();
    const rows = state.sales
      .filter(s => !q || [s.date, s.customer, s.item, s.payment, s.notes].some(v => String(v||'').toLowerCase().includes(q)))
      .sort((a, b) => String(b.date||'').localeCompare(String(a.date||'')))
      .map(s => `
        <tr data-id="${s.id}">
          <td>${escapeHtml(s.date)}</td>
          <td>${escapeHtml(s.customer)}</td>
          <td>${escapeHtml(s.item)}</td>
          <td>${moneyFmt.format(toNumber(s.amount))}</td>
          <td>${escapeHtml(s.payment)}</td>
          <td>
            <div class="row-actions">
              <button class="btn" data-action="edit-sale">Edit</button>
              <button class="btn danger" data-action="delete-sale">Delete</button>
            </div>
          </td>
        </tr>`)
      .join('');
    tbody.innerHTML = rows || emptyRow(6, 'No sales yet');
    updateKpis();
  }

  function updateKpis() {
    const kpiCustomers = $("#kpi-customers");
    const kpiAppts = $("#kpi-appointments");
    const kpiRevenue = $("#kpi-revenue");
    if (kpiCustomers) kpiCustomers.textContent = String(state.customers.length);
    if (kpiAppts) {
      const count = state.appointments.filter(a => isTodayOrFuture(a.date) && String(a.status).toLowerCase() !== 'cancelled').length;
      kpiAppts.textContent = String(count);
    }
    if (kpiRevenue) {
      const total = state.sales.filter(s => thisMonth(s.date)).reduce((sum, s) => sum + toNumber(s.amount), 0);
      kpiRevenue.textContent = moneyFmt.format(total);
    }
  }

  // ----- Event Handlers: Customers -----
  function onCustomerSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const customer = {
      name: String(formData.get('name') || '').trim(),
      phone: String(formData.get('phone') || '').trim(),
      email: String(formData.get('email') || '').trim(),
      vehicle: String(formData.get('vehicle') || '').trim(),
      notes: String(formData.get('notes') || '').trim(),
    };
    if (!customer.name) {
      form.querySelector('[name="name"]').focus();
      toast('Customer name is required', 'warn');
      return;
    }
    if (state.editing.customerId) {
      const idx = state.customers.findIndex(c => c.id === state.editing.customerId);
      if (idx !== -1) {
        state.customers[idx] = { ...state.customers[idx], ...customer };
        toast('Customer updated', 'success');
      }
    } else {
      state.customers.push({ id: uid('c'), ...customer });
      toast('Customer added', 'success');
    }
    state.editing.customerId = null;
    btn.textContent = 'Save Customer';
    save(LS.customers, state.customers);
    form.reset();
    renderCustomers();
  }

  function onCustomerTableClick(e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.getAttribute('data-id');
    const action = btn.getAttribute('data-action');
    if (action === 'edit-customer') {
      const c = state.customers.find(x => x.id === id);
      if (!c) return;
      const form = $("#customer-form");
      form.name.value = c.name || '';
      form.phone.value = c.phone || '';
      form.email.value = c.email || '';
      form.vehicle.value = c.vehicle || '';
      form.notes.value = c.notes || '';
      state.editing.customerId = id;
      form.querySelector('button[type="submit"]').textContent = 'Update Customer';
      $('[data-target="customers"]').click();
    } else if (action === 'delete-customer') {
      if (!confirm('Delete this customer?')) return;
      state.customers = state.customers.filter(c => c.id !== id);
      save(LS.customers, state.customers);
      renderCustomers();
      toast('Customer deleted', 'info');
    }
  }

  // ----- Event Handlers: Appointments -----
  function onAppointmentSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const appt = {
      date: String(formData.get('date') || ''),
      time: String(formData.get('time') || ''),
      customer: String(formData.get('customer') || '').trim(),
      vehicle: String(formData.get('vehicle') || '').trim(),
      service: String(formData.get('service') || '').trim(),
      price: toNumber(formData.get('price') || 0),
      status: String(formData.get('status') || 'Scheduled'),
    };
    if (!appt.date || !appt.time || !appt.customer) {
      if (!appt.date) form.querySelector('[name="date"]').focus();
      else if (!appt.time) form.querySelector('[name="time"]').focus();
      else form.querySelector('[name="customer"]').focus();
      toast('Please fill required appointment fields', 'warn');
      return;
    }
    if (state.editing.appointmentId) {
      const idx = state.appointments.findIndex(a => a.id === state.editing.appointmentId);
      if (idx !== -1) { state.appointments[idx] = { ...state.appointments[idx], ...appt }; toast('Appointment updated', 'success'); }
    } else {
      state.appointments.push({ id: uid('a'), ...appt });
      toast('Appointment added', 'success');
    }
    state.editing.appointmentId = null;
    btn.textContent = 'Save Appointment';
    save(LS.appointments, state.appointments);
    form.reset();
    renderAppointments();
  }

  function onAppointmentTableClick(e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.getAttribute('data-id');
    const action = btn.getAttribute('data-action');
    if (action === 'edit-appointment') {
      const a = state.appointments.find(x => x.id === id);
      if (!a) return;
      const form = $("#appointment-form");
      form.date.value = a.date || '';
      form.time.value = a.time || '';
      form.customer.value = a.customer || '';
      form.vehicle.value = a.vehicle || '';
      form.service.value = a.service || '';
      form.price.value = a.price != null ? a.price : '';
      form.status.value = a.status || 'Scheduled';
      state.editing.appointmentId = id;
      form.querySelector('button[type="submit"]').textContent = 'Update Appointment';
      $('[data-target="appointments"]').click();
    } else if (action === 'delete-appointment') {
      if (!confirm('Delete this appointment?')) return;
      state.appointments = state.appointments.filter(a => a.id !== id);
      save(LS.appointments, state.appointments);
      renderAppointments();
      toast('Appointment deleted', 'info');
    }
  }

  // ----- Event Handlers: Sales -----
  function onSaleSubmit(e) {
    e.preventDefault();
    const form = e.currentTarget;
    const btn = form.querySelector('button[type="submit"]');
    const formData = new FormData(form);
    const sale = {
      date: String(formData.get('date') || ''),
      customer: String(formData.get('customer') || '').trim(),
      item: String(formData.get('item') || '').trim(),
      amount: toNumber(formData.get('amount') || 0),
      payment: String(formData.get('payment') || ''),
      notes: String(formData.get('notes') || '').trim(),
    };
    if (!sale.date || !sale.item || !Number.isFinite(sale.amount)) {
      if (!sale.date) form.querySelector('[name="date"]').focus();
      else if (!sale.item) form.querySelector('[name="item"]').focus();
      else form.querySelector('[name="amount"]').focus();
      toast('Please fill required sale fields', 'warn');
      return;
    }
    if (state.editing.saleId) {
      const idx = state.sales.findIndex(s => s.id === state.editing.saleId);
      if (idx !== -1) { state.sales[idx] = { ...state.sales[idx], ...sale }; toast('Sale updated', 'success'); }
    } else {
      state.sales.push({ id: uid('s'), ...sale });
      toast('Sale recorded', 'success');
    }
    state.editing.saleId = null;
    btn.textContent = 'Save Sale';
    save(LS.sales, state.sales);
    form.reset();
    renderSales();
  }

  function onSalesTableClick(e) {
    const btn = e.target.closest('button[data-action]');
    if (!btn) return;
    const tr = e.target.closest('tr[data-id]');
    if (!tr) return;
    const id = tr.getAttribute('data-id');
    const action = btn.getAttribute('data-action');
    if (action === 'edit-sale') {
      const s = state.sales.find(x => x.id === id);
      if (!s) return;
      const form = $("#sale-form");
      form.date.value = s.date || '';
      form.customer.value = s.customer || '';
      form.item.value = s.item || '';
      form.amount.value = s.amount != null ? s.amount : '';
      form.payment.value = s.payment || 'Cash';
      form.notes.value = s.notes || '';
      state.editing.saleId = id;
      form.querySelector('button[type="submit"]').textContent = 'Update Sale';
      $('[data-target="sales"]').click();
    } else if (action === 'delete-sale') {
      if (!confirm('Delete this sale?')) return;
      state.sales = state.sales.filter(s => s.id !== id);
      save(LS.sales, state.sales);
      renderSales();
      toast('Sale deleted', 'info');
    }
  }

  // ----- Settings -----
  function applySettingsToForm() {
    const form = $('#settings-form');
    const s = state.settings || {};
    if (!form) return;
    form.businessName.value = s.businessName || '';
    form.taxRate.value = s.taxRate != null ? s.taxRate : '';
    form.address.value = s.address || '';
    form.shopPhone.value = s.shopPhone || '';
    form.shopEmail.value = s.shopEmail || '';
  }

  function readSettingsFromForm() {
    const form = $('#settings-form');
    return {
      businessName: String(form.businessName.value || '').trim(),
      taxRate: Number(form.taxRate.value || '') || 0,
      address: String(form.address.value || '').trim(),
      shopPhone: String(form.shopPhone.value || '').trim(),
      shopEmail: String(form.shopEmail.value || '').trim(),
    };
  }

  function onSettingsSubmit(e) {
    e.preventDefault();
    const s = readSettingsFromForm();
    state.settings = s;
    save(LS.settings, s);
    toast('Settings saved', 'success');
  }

  // ----- Data Management (Export/Import/Reset) -----
  function bindDataManagement() {
    const exportBtn = $('#export-data');
    const importBtn = $('#import-data');
    const importFile = $('#import-file');
    const resetBtn = $('#reset-data');

    exportBtn.addEventListener('click', () => {
      const payload = {
        meta: {
          name: 'tints-manager-backup',
          version: 1,
          exportedAt: new Date().toISOString(),
        },
        customers: state.customers,
        appointments: state.appointments,
        sales: state.sales,
        settings: state.settings,
      };
      const blob = new Blob([JSON.stringify(payload, null, 2)], { type: 'application/json' });
      const url = URL.createObjectURL(blob);
      const a = document.createElement('a');
      const ts = new Date().toISOString().replace(/[:.]/g, '-');
      a.href = url;
      a.download = `tints-data-${ts}.json`;
      document.body.appendChild(a);
      a.click();
      a.remove();
      URL.revokeObjectURL(url);
      toast('Data exported', 'success');
    });

    importBtn.addEventListener('click', () => importFile.click());

    importFile.addEventListener('change', async (e) => {
      const file = e.target.files && e.target.files[0];
      if (!file) return;
      try {
        const text = await file.text();
        const data = JSON.parse(text);
        if (!data || typeof data !== 'object') throw new Error('Invalid JSON');
        if (!confirm('Importing will overwrite existing data. Continue?')) return;
        const customers = Array.isArray(data.customers) ? data.customers : [];
        const appointments = Array.isArray(data.appointments) ? data.appointments : [];
        const sales = Array.isArray(data.sales) ? data.sales : [];
        const settings = data.settings && typeof data.settings === 'object' ? data.settings : {};

        state.customers = customers;
        state.appointments = appointments;
        state.sales = sales;
        state.settings = settings;

        save(LS.customers, customers);
        save(LS.appointments, appointments);
        save(LS.sales, sales);
        save(LS.settings, settings);

        applySettingsToForm();
        renderCustomers();
        renderAppointments();
        renderSales();
        toast('Data imported', 'success');
      } catch (err) {
        console.error(err);
        toast('Failed to import JSON', 'danger');
      } finally {
        e.target.value = '';
      }
    });

    resetBtn.addEventListener('click', () => {
      if (!confirm('Reset ALL data? This cannot be undone.')) return;
      state.customers = [];
      state.appointments = [];
      state.sales = [];
      state.settings = {};
      remove(LS.customers);
      remove(LS.appointments);
      remove(LS.sales);
      remove(LS.settings);
      applySettingsToForm();
      renderCustomers();
      renderAppointments();
      renderSales();
      toast('All data reset', 'warn');
    });
  }

  // ----- Initialization -----
  function bindFormsAndTables() {
    // Customers
    const customerForm = $("#customer-form");
    customerForm.addEventListener('submit', onCustomerSubmit);
    customerForm.addEventListener('reset', () => {
      state.editing.customerId = null;
      customerForm.querySelector('button[type="submit"]').textContent = 'Save Customer';
    });
    $("#customers-table").addEventListener('click', onCustomerTableClick);
    $("#customer-search").addEventListener('input', renderCustomers);

    // Appointments
    const apptForm = $("#appointment-form");
    apptForm.addEventListener('submit', onAppointmentSubmit);
    apptForm.addEventListener('reset', () => {
      state.editing.appointmentId = null;
      apptForm.querySelector('button[type="submit"]').textContent = 'Save Appointment';
    });
    $("#appointments-table").addEventListener('click', onAppointmentTableClick);
    $("#appointment-search").addEventListener('input', renderAppointments);

    // Sales
    const saleForm = $("#sale-form");
    saleForm.addEventListener('submit', onSaleSubmit);
    saleForm.addEventListener('reset', () => {
      state.editing.saleId = null;
      saleForm.querySelector('button[type="submit"]').textContent = 'Save Sale';
    });
    $("#sales-table").addEventListener('click', onSalesTableClick);
    $("#sales-search").addEventListener('input', renderSales);

    // Settings
    const settingsForm = $('#settings-form');
    settingsForm.addEventListener('submit', onSettingsSubmit);
  }

  function loadState() {
    state.customers = loadArray(LS.customers);
    state.appointments = loadArray(LS.appointments);
    state.sales = loadArray(LS.sales);
    state.settings = loadObject(LS.settings);
  }

  function initialRender() {
    applySettingsToForm();
    renderCustomers();
    renderAppointments();
    renderSales();
    updateKpis();
  }

  document.addEventListener('DOMContentLoaded', () => {
    initTabs();
    setYear();
    loadState();
    bindFormsAndTables();
    bindDataManagement();
    initialRender();
  });
})();
