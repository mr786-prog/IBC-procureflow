import { apiRequest, fetchCurrentUser, showToast } from "/static/shared/js/api.js?v=lr-workflow-20261126";
import { formatDate, formatDateTime, formatCurrency, renderStatusBadge } from "/static/shared/js/format.js";

function exportButtons(entity) {
  return `<span style="display:inline-flex;gap:6px;margin-right:8px;"><a class="btn btn-secondary btn-sm" href="/api/v1/exports/${entity}?format=xlsx" download>Excel</a><a class="btn btn-secondary btn-sm" href="/api/v1/exports/${entity}?format=pdf" download>PDF</a></span>`;
}

let currentUser = null;
let currentPage = "dashboard";

function escapeHtml(value) {
  return String(value ?? "").replace(/[&<>"']/g, char => ({
    "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;", "'": "&#39;"
  })[char]);
}

function jsArg(value) {
  return JSON.stringify(String(value ?? ""))
    .replace(/&/g, "&amp;").replace(/</g, "\\u003c").replace(/>/g, "\\u003e")
    .replace(/"/g, "&quot;");
}

window.closeModal = function() {
  document.getElementById("app-modal").classList.remove("active");
};

window.openModal = function(title, contentHtml) {
  document.getElementById("modal-title").textContent = title;
  document.getElementById("modal-content").innerHTML = contentHtml;
  document.getElementById("app-modal").classList.add("active");
};

document.addEventListener("DOMContentLoaded", async () => {
  currentUser = await fetchCurrentUser();
  if (!currentUser) return;

  document.getElementById("header-emp-name").textContent = currentUser.full_name;
  document.getElementById("header-emp-dept").textContent = `${currentUser.department_name || 'Plant'} (${currentUser.role})`;
  document.querySelectorAll('.nav-item[data-page="enquiries"], .nav-item[data-page="pos"]').forEach(el => { if (!currentUser.can_procure && currentUser.role !== "manager") el.hidden = true; });
  document.querySelector('.nav-item[data-page="grns"]').hidden = !currentUser.can_receive && currentUser.role !== "manager";
  document.querySelector('.nav-item[data-page="lrs"]').hidden = !currentUser.can_receive && currentUser.role !== "manager";

  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
      item.classList.add("active");
      currentPage = item.dataset.page;
      renderCurrentPage();
    });
  });

  document.getElementById("btn-logout").addEventListener("click", async () => {
    await apiRequest("/api/v1/auth/logout", { method: "POST" });
    window.location.href = "/login";
  });

  renderCurrentPage();
});

async function renderCurrentPage() {
  const container = document.getElementById("page-container");
  const title = document.getElementById("current-page-title");
  container.innerHTML = `<div style="padding:20px; color:#64748b;">Loading ${currentPage}...</div>`;

  try {
    switch (currentPage) {
      case "dashboard":
        title.textContent = "My Dashboard";
        await renderDashboard(container);
        break;
      case "new-requisition":
        title.textContent = "Raise New Purchase Requisition";
        await renderNewRequisition(container);
        break;
      case "my-requisitions":
        title.textContent = "My Requisitions History";
        await renderMyRequisitions(container);
        break;
      case "enquiries":
        title.textContent = "Enquiries & Quotes";
        await renderEmployeeEnquiries(container);
        break;
      case "pos":
        title.textContent = "Purchase Orders";
        await renderEmployeePOs(container);
        break;
      case "grns":
        title.textContent = "Goods Receipt Notes";
        await renderEmployeeGRNs(container);
        break;
      case "lrs":
        title.textContent = "Lorry Receipts & Transport Payments";
        await renderEmployeeLRs(container);
        break;
      case "catalogue":
        title.textContent = "Item Catalogue";
        await renderCatalogue(container);
        break;
      case "notifications":
        title.textContent = "My Notifications";
        await renderNotifications(container);
        break;
      case "profile":
        title.textContent = "My Profile & Permissions";
        await renderProfile(container);
        break;
    }
    const exportEntity = { "my-requisitions": "requisitions", enquiries: "enquiries", pos: "purchase-orders", grns: "grns", lrs: "lrs", catalogue: "items" }[currentPage];
    const pageHeader = container.querySelector(".page-header");
    if (exportEntity && pageHeader) pageHeader.insertAdjacentHTML("afterbegin", `<div>${exportButtons(exportEntity)}</div>`);
  } catch (err) {
    container.innerHTML = `<div class="card" style="color:var(--color-danger);">Error: ${escapeHtml(err.message)}</div>`;
  }
}

async function renderEmployeeEnquiries(container) {
  const enquiries = await apiRequest("/api/v1/enquiries");
  container.innerHTML = `<div class="page-header"><div></div><button class="btn btn-primary" onclick="openEmployeeEnquiryModal()">+ Create Enquiry</button></div>
    <div class="card"><div class="table-responsive"><table class="table"><thead><tr><th>Number</th><th>Subject</th><th>Vendors</th><th>Status</th><th>Action</th></tr></thead><tbody>
    ${enquiries.map(e => `<tr><td>${escapeHtml(e.number)}</td><td>${escapeHtml(e.subject)}</td><td>${e.vendor_count}</td><td>${renderStatusBadge(e.status)}</td><td>${e.status==='awarded'?`<button class="btn btn-primary btn-sm" onclick="employeeConvertEnquiryToPO('${e.id}', ${jsArg(e.number)})">Convert to PO</button>`:''}</td></tr>`).join("") || '<tr><td colspan="5">No enquiries yet.</td></tr>'}
    </tbody></table></div></div>`;
}

window.employeeConvertEnquiryToPO = async function(enquiryId, number) {
  if(!confirm(`Convert awarded enquiry ${number} into a PO draft and submit it for approval?`)) return;
  try { const result=await apiRequest(`/api/v1/enquiries/${enquiryId}/convert-to-po`,{method:'POST',body:JSON.stringify({})}); await apiRequest(`/api/v1/purchase-orders/${result.id}/submit`,{method:'POST'}); showToast(`PO ${result.number} submitted for manager approval.`, 'success'); navigateTo('pos'); } catch(_) {}
};

window.openEmployeeEnquiryModal = async function(sourcePRId = "") {
  const [prs, vendors] = await Promise.all([apiRequest("/api/v1/requisitions"), apiRequest("/api/v1/vendors")]);
  const openPRs=prs.filter(p=>['approved','in_progress'].includes(p.status));
  openModal("Create Enquiry", `<form id="emp-enq-form"><div class="form-group"><label class="form-label">Approved Requisition (optional)</label><select id="emp-enq-pr" class="form-select"><option value="">Create a manual enquiry</option>${openPRs.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.number)} — ${escapeHtml(p.purpose)}</option>`).join("")}</select></div>
    <div class="form-group"><label class="form-label">Subject *</label><input id="emp-enq-subject" class="form-input" required placeholder="Request for quotation"></div>
    <div class="form-grid-3"><div class="form-group"><label class="form-label">Manual item name</label><input id="emp-enq-item" class="form-input" placeholder="Required only without a PR"></div><div class="form-group"><label class="form-label">Quantity</label><input id="emp-enq-qty" type="number" min="0.01" step="0.01" class="form-input" value="1"></div><div class="form-group"><label class="form-label">Unit</label><input id="emp-enq-unit" class="form-input" value="Nos"></div></div>
    <div class="form-group"><label class="form-label">Invite Vendors *</label><div style="max-height:180px;overflow:auto">${vendors.map(v => `<label style="display:block;margin:6px 0"><input type="checkbox" name="emp-enq-vendor" value="${escapeHtml(v.id)}"> ${escapeHtml(v.name)}</label>`).join("")}</div></div>
    <div style="display:flex;justify-content:flex-end;gap:8px"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Create Enquiry</button></div></form>`);
  document.getElementById("emp-enq-pr").value=sourcePRId;
  document.getElementById("emp-enq-form").addEventListener("submit", async e => { e.preventDefault();
    const vendor_ids = [...document.querySelectorAll('input[name="emp-enq-vendor"]:checked')].map(x => x.value);
    if (!vendor_ids.length) return showToast("Select at least one vendor.", "error");
    const requisition_id=document.getElementById("emp-enq-pr").value;
    const item_name=document.getElementById("emp-enq-item").value.trim();
    if(!requisition_id&&!item_name)return showToast("Enter a manual item or select an approved requisition.","error");
    try { const result = await apiRequest("/api/v1/enquiries", {method:"POST", body:JSON.stringify({requisition_id:requisition_id||null, subject:document.getElementById("emp-enq-subject").value, vendor_ids, lines:requisition_id?[]:[{item_name,quantity:Number(document.getElementById("emp-enq-qty").value),unit:document.getElementById("emp-enq-unit").value}]})});
      showToast(`Enquiry ${result.number} created.`, "success"); closeModal(); renderCurrentPage(); } catch (_) {}
  });
};

async function renderEmployeePOs(container) {
  const pos = await apiRequest("/api/v1/purchase-orders");
  window.employeeLRPOs=pos.filter(p=>['approved','sent','part_received','received','closed'].includes(p.status));
  container.innerHTML = `<div class="page-header"><div></div><button class="btn btn-primary" onclick="openEmployeePOModal()">+ Create Purchase Order</button></div><div class="card"><div class="table-responsive"><table class="table"><thead><tr><th>Number</th><th>Vendor</th><th>Date</th><th>Status</th><th>Action</th></tr></thead><tbody>
    ${pos.map(p => `<tr><td>${escapeHtml(p.number)}</td><td>${escapeHtml(p.vendor_name)}</td><td>${formatDate(p.order_date)}</td><td>${renderStatusBadge(p.status)}</td><td>${p.status === "draft" ? `<button class="btn btn-primary btn-sm" onclick="submitEmployeePO('${escapeHtml(p.id)}')">Submit</button>` : ""}${['approved','sent','part_received','received','closed'].includes(p.status)?` <button class="btn btn-secondary btn-sm" onclick="openEmployeeLRModal('${escapeHtml(p.id)}')">Attach LR</button>`:''}</td></tr>`).join("") || '<tr><td colspan="5">No purchase orders yet.</td></tr>'}</tbody></table></div></div>`;
}

window.openEmployeePOModal = async function() {
  const [vendors, items] = await Promise.all([apiRequest("/api/v1/vendors"), apiRequest("/api/v1/items")]);
  openModal("Create Purchase Order", `<form id="emp-po-form"><div class="form-grid-2"><div class="form-group"><label class="form-label">Vendor *</label><select id="emp-po-vendor" class="form-select" required><option value="">Select vendor</option>${vendors.map(v => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join("")}</select></div>
    <div class="form-group"><label class="form-label">Expected Delivery</label><input type="date" id="emp-po-date" class="form-input"></div></div>
    <div class="form-group"><label class="form-label">Item *</label><select id="emp-po-item" class="form-select" required>${items.map(i => `<option value="${escapeHtml(i.id)}" data-name="${escapeHtml(i.name)}" data-part="${escapeHtml(i.part_number||"")}" data-unit="${escapeHtml(i.unit)}">${escapeHtml(i.name)}</option>`).join("")}</select></div>
    <div class="form-grid-3"><div class="form-group"><label class="form-label">Quantity</label><input id="emp-po-qty" type="number" min="0.01" step="0.01" value="1" class="form-input" required></div><div class="form-group"><label class="form-label">Unit Rate</label><input id="emp-po-rate" type="number" min="0" step="0.01" value="0" class="form-input" required></div><div class="form-group"><label class="form-label">Tax %</label><input id="emp-po-tax" type="number" min="0" max="100" value="18" class="form-input" required></div></div>
    <div class="form-group"><label class="form-label">Approval</label><label style="margin-right:16px"><input type="radio" name="emp-po-approval" value="true" checked> Manager approval required</label><label><input type="radio" name="emp-po-approval" value="false"> Manager approval not required</label></div>
    <div style="display:flex;justify-content:flex-end;gap:8px"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Create PO</button></div></form>`);
  document.getElementById("emp-po-form").addEventListener("submit", async e => {e.preventDefault(); const item=document.getElementById("emp-po-item").selectedOptions[0];
    try { const result=await apiRequest("/api/v1/purchase-orders",{method:"POST",body:JSON.stringify({vendor_id:document.getElementById("emp-po-vendor").value,delivery_date:document.getElementById("emp-po-date").value||null,approval_required:document.querySelector('input[name="emp-po-approval"]:checked').value==="true",lines:[{item_id:item.value,item_name:item.dataset.name,part_number:item.dataset.part,quantity:Number(document.getElementById("emp-po-qty").value),unit:item.dataset.unit,rate:Number(document.getElementById("emp-po-rate").value),tax_percent:Number(document.getElementById("emp-po-tax").value)}]})});
      await apiRequest(`/api/v1/purchase-orders/${result.id}/submit`,{method:"POST"}); showToast(`PO ${result.number} submitted.`,"success");closeModal();renderCurrentPage();} catch (_) {}
  });
};
window.submitEmployeePO = async id => { try {await apiRequest(`/api/v1/purchase-orders/${id}/submit`,{method:"POST"});showToast("Purchase order submitted.","success");renderCurrentPage();}catch(_){} };

async function renderEmployeeGRNs(container) {
  const [grns,lrs]=await Promise.all([apiRequest("/api/v1/grns"),apiRequest("/api/v1/lrs")]);
  container.innerHTML=`<div class="page-header"><button class="btn btn-secondary" onclick="navigateTo('lrs')">Manage LRs & payments</button><button class="btn btn-primary" onclick="openEmployeeGRNModal()">+ Post GRN</button></div><div class="card"><div class="table-responsive"><table class="table"><thead><tr><th>GRN</th><th>PO</th><th>Vendor</th><th>Date</th><th>Status</th><th>Transport LRs</th></tr></thead><tbody>${grns.map(g=>{const attached=lrs.filter(lr=>lr.purchase_order_id===g.purchase_order_id);return `<tr><td>${escapeHtml(g.number)}</td><td>${escapeHtml(g.po_number)}</td><td>${escapeHtml(g.vendor_name)}</td><td>${formatDate(g.received_date)}</td><td>${renderStatusBadge(g.status)}</td><td>${attached.map(lr=>`<div><strong>${escapeHtml(lr.number)}</strong>${lr.document_filename?` · <a href="/api/v1/lrs/${lr.id}/document">Document</a>`:''}</div>`).join('')||'<span style="color:#64748b;">None attached</span>'}<br><button class="btn btn-secondary btn-sm" onclick="openEmployeeLRModal('${g.purchase_order_id}')">+ Attach LR</button></td></tr>`}).join("")||'<tr><td colspan="6">No goods receipts yet.</td></tr>'}</tbody></table></div></div>`;
}

async function renderEmployeeLRs(container) {
  const [lrs,pos]=await Promise.all([apiRequest('/api/v1/lrs'),apiRequest('/api/v1/purchase-orders')]);
  window.employeeLRPOs=pos.filter(p=>['approved','sent','part_received','received','closed'].includes(p.status));
  container.innerHTML=`<div class="page-header"><p style="color:#64748b;">Attach the transport receipt after pickup. Track follow-up contacts and transport payments here.</p><button class="btn btn-primary" onclick="openEmployeeLRModal()">+ Attach LR to PO</button></div>
    <div class="card"><div class="table-responsive"><table class="table"><thead><tr><th>LR / Date</th><th>PO</th><th>Vendor</th><th>Transporter / Contact</th><th>Payment</th><th>Document / Action</th></tr></thead><tbody>
    ${lrs.map(lr=>`<tr><td><strong>${escapeHtml(lr.number)}</strong><br>${formatDate(lr.lr_date)}</td><td>${escapeHtml(lr.po_number)}</td><td>${escapeHtml(lr.vendor_name)}</td><td>${escapeHtml(lr.transporter_name||'—')}<br>${escapeHtml(lr.transporter_contact||'—')}</td><td>${escapeHtml(lr.payment_status)} · ${formatCurrency(lr.payment_amount||0)}${lr.payment_date?`<br>${formatDate(lr.payment_date)}`:''}</td><td>${lr.document_filename?`<a href="/api/v1/lrs/${lr.id}/document" class="btn btn-secondary btn-sm">View LR</a>`:''}<button class="btn btn-secondary btn-sm" onclick="editEmployeeLR('${lr.id}',${jsArg(lr.payment_status)},${Number(lr.payment_amount||0)},${jsArg(lr.payment_date||'')},${jsArg(lr.payment_reference||'')},${jsArg(lr.transporter_name||'')},${jsArg(lr.transporter_contact||'')})">Update</button></td></tr>`).join('')||'<tr><td colspan="6" style="text-align:center;color:#64748b;">No LRs attached to POs yet.</td></tr>'}
    </tbody></table></div></div>`;
}

window.openEmployeeLRModal=function(poId='') {
  const pos=window.employeeLRPOs||[];
  if(!pos.length){showToast('No authorized POs are available for LR attachment.','info');return;}
  openModal('Attach Transport LR',`<form id="employee-lr-form"><div class="form-group"><label class="form-label">Purchase Order *</label><select id="elr-po" class="form-select" required><option value="">Select PO</option>${pos.map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.number)} — ${escapeHtml(p.vendor_name)}</option>`).join('')}</select></div>
    <div class="form-grid-2"><div class="form-group"><label class="form-label">LR Number *</label><input id="elr-number" class="form-input" required maxlength="80"></div><div class="form-group"><label class="form-label">LR Date</label><input id="elr-date" type="date" class="form-input"></div></div>
    <div class="form-grid-2"><div class="form-group"><label class="form-label">Transporter</label><input id="elr-transporter" class="form-input"></div><div class="form-group"><label class="form-label">Transporter Phone / Email</label><input id="elr-contact" class="form-input"></div></div>
    <div class="form-group"><label class="form-label">Transport Document (PDF/JPG/PNG, max 10 MB)</label><input id="elr-file" type="file" class="form-input" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"></div>
    <div class="form-group"><label class="form-label">Notes</label><textarea id="elr-notes" class="form-input"></textarea></div><div style="display:flex;justify-content:flex-end;gap:8px"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Save LR</button></div></form>`);
  if(poId)document.getElementById('elr-po').value=poId;
  document.getElementById('employee-lr-form').addEventListener('submit',async e=>{e.preventDefault();try{const lr=await apiRequest('/api/v1/lrs',{method:'POST',body:JSON.stringify({purchase_order_id:document.getElementById('elr-po').value,number:document.getElementById('elr-number').value,lr_date:document.getElementById('elr-date').value||null,transporter_name:document.getElementById('elr-transporter').value||null,transporter_contact:document.getElementById('elr-contact').value||null,notes:document.getElementById('elr-notes').value||null})});const file=document.getElementById('elr-file').files[0];if(file){const form=new FormData();form.append('file',file);await apiRequest(`/api/v1/lrs/${lr.id}/document`,{method:'POST',body:form});}showToast('LR attached to PO.','success');closeModal();renderCurrentPage();}catch(_){}});
};

window.editEmployeeLR=function(id,paymentStatus,amount,date,reference,transporter,contact){
  openModal('Update LR payment and transporter details',`<form id="employee-lr-edit"><div class="form-grid-2"><div class="form-group"><label class="form-label">Payment Status</label><select id="elr-status" class="form-select"><option value="unpaid">Unpaid</option><option value="partial">Partial</option><option value="paid">Paid</option></select></div><div class="form-group"><label class="form-label">Amount Paid</label><input id="elr-amount" type="number" min="0" step="0.01" value="${amount}" class="form-input"></div></div><div class="form-grid-2"><div class="form-group"><label class="form-label">Payment Date</label><input id="elr-payment-date" type="date" value="${escapeHtml(date)}" class="form-input"></div><div class="form-group"><label class="form-label">Payment Reference</label><input id="elr-reference" value="${escapeHtml(reference)}" class="form-input"></div></div><div class="form-grid-2"><div class="form-group"><label class="form-label">Transporter</label><input id="elr-edit-transporter" value="${escapeHtml(transporter)}" class="form-input"></div><div class="form-group"><label class="form-label">Transporter Contact</label><input id="elr-edit-contact" value="${escapeHtml(contact)}" class="form-input"></div></div><div style="display:flex;justify-content:flex-end;gap:8px"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Save</button></div></form>`);
  document.getElementById('elr-status').value=paymentStatus;
  document.getElementById('employee-lr-edit').addEventListener('submit',async e=>{e.preventDefault();try{await apiRequest(`/api/v1/lrs/${id}`,{method:'PATCH',body:JSON.stringify({payment_status:document.getElementById('elr-status').value,payment_amount:Number(document.getElementById('elr-amount').value||0),payment_date:document.getElementById('elr-payment-date').value||null,payment_reference:document.getElementById('elr-reference').value||null,transporter_name:document.getElementById('elr-edit-transporter').value||null,transporter_contact:document.getElementById('elr-edit-contact').value||null})});showToast('LR updated.','success');closeModal();renderCurrentPage();}catch(_){}});
};
window.openEmployeeGRNModal=async function(){
  const pos=await apiRequest("/api/v1/purchase-orders");const eligible=pos.filter(p=>["approved","sent","part_received"].includes(p.status));
  openModal("Post Goods Receipt",`<form id="emp-grn-form"><div class="form-group"><label class="form-label">Purchase Order *</label><select id="emp-grn-po" class="form-select" required onchange="loadEmployeeGRNLines(this.value)"><option value="">Select PO</option>${eligible.map(p=>`<option value="${escapeHtml(p.id)}">${escapeHtml(p.number)} — ${escapeHtml(p.vendor_name)}</option>`).join("")}</select></div><div class="form-group"><label class="form-label">Received Date *</label><input id="emp-grn-date" type="date" class="form-input" required value="${new Date().toISOString().slice(0,10)}"></div><div id="emp-grn-lines" class="form-group">Select a PO to enter received quantities.</div><div style="display:flex;justify-content:flex-end;gap:8px"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Post GRN</button></div></form>`);
  document.getElementById("emp-grn-form").addEventListener("submit",async e=>{e.preventDefault();try{const purchase_order_id=document.getElementById("emp-grn-po").value;const lines=[...document.querySelectorAll(".emp-grn-line")].map(row=>{const received_qty=Number(row.querySelector("input").value)||0;return {po_line_id:row.dataset.lineId,received_qty,accepted_qty:received_qty,rejected_qty:0};}).filter(line=>line.received_qty>0);if(!lines.length)return showToast("Enter at least one received quantity.","error");const res=await apiRequest("/api/v1/grns",{method:"POST",body:JSON.stringify({purchase_order_id,received_date:document.getElementById("emp-grn-date").value,lines})});showToast(`GRN ${res.number} posted.`,"success");closeModal();renderCurrentPage();}catch(_){}});
};
window.loadEmployeeGRNLines=async function(poId){if(!poId)return;const po=await apiRequest(`/api/v1/purchase-orders/${poId}`);document.getElementById("emp-grn-lines").innerHTML=po.lines.map(line=>`<label class="emp-grn-line" data-line-id="${escapeHtml(line.id)}" style="display:grid;grid-template-columns:2fr 1fr 1fr;gap:8px;align-items:center;margin:8px 0"><span>${escapeHtml(line.item_name_snapshot)} (ordered ${line.quantity} ${escapeHtml(line.unit)}, received ${line.received_qty})</span><span>Received qty</span><input type="number" min="0" max="${line.quantity-line.received_qty}" step="0.01" value="0" class="form-input"></label>`).join("");};

// 1. DASHBOARD
async function renderDashboard(container) {
  const myPRs = await apiRequest("/api/v1/requisitions");
  const notifs = await apiRequest("/api/v1/notifications");

  const unreadNotifs = notifs.filter(n => !n.read_at).length;
  const badge = document.getElementById("badge-emp-notif");
  if (unreadNotifs > 0) {
    badge.textContent = unreadNotifs;
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }

  const openPRs = myPRs.filter(p => ["submitted", "approved", "in_progress"].includes(p.status)).length;
  const draftPRs = myPRs.filter(p => p.status === "draft").length;
  const rejectedPRs = myPRs.filter(p => p.status === "rejected").length;

  container.innerHTML = `
    <div class="metrics-grid">
      <div class="metric-card">
        <span class="metric-label">Open Active PRs</span>
        <span class="metric-value" style="color:var(--color-primary);">${openPRs}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Drafts (Unsubmitted)</span>
        <span class="metric-value" style="color:#64748b;">${draftPRs}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Needs Action (Rejected)</span>
        <span class="metric-value" style="color:#dc2626;">${rejectedPRs}</span>
      </div>
    </div>

    <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
      <h3>Recent Requisitions</h3>
      <button class="btn btn-primary" onclick="navigateTo('new-requisition')">+ New Requisition</button>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>PR Number</th>
              <th>Required By</th>
              <th>Purpose</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${myPRs.length === 0 ? '<tr><td colspan="5" style="text-align:center; color:#64748b;">No requisitions raised yet. Click "+ New Requisition" to create one.</td></tr>' : myPRs.slice(0, 6).map(r => `
              <tr>
                <td><strong>${escapeHtml(r.number)}</strong></td>
                <td>${formatDate(r.required_by)}</td>
                <td>${escapeHtml(r.purpose)}</td>
                <td>${renderStatusBadge(r.status)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewMyPRDetail(${jsArg(r.id)})">View Details</button>
                  ${r.status === 'draft' ? `<button class="btn btn-primary btn-sm" onclick="submitDraftPR(${jsArg(r.id)})">Submit</button>` : ''}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 2. NEW REQUISITION FORM
async function renderNewRequisition(container) {
  const depts = await apiRequest("/api/v1/departments");
  const items = await apiRequest("/api/v1/items");
  window.employeeCatalogItems = items;

  container.innerHTML = `
    <div class="card">
      <form id="new-pr-form">
        <div class="form-grid-3">
          <div class="form-group">
            <label class="form-label">Department *</label>
            <select id="pr-dept" class="form-select" required>
              ${depts.map(d => `<option value="${escapeHtml(d.id)}" ${d.id === currentUser.department_id ? 'selected' : ''}>${escapeHtml(d.name)}</option>`).join("")}
            </select>
          </div>
          <div class="form-group">
            <label class="form-label">Required By Date *</label>
            <input type="date" id="pr-required-by" class="form-input" required>
          </div>
          <div class="form-group">
            <label class="form-label">Priority</label>
            <select id="pr-priority" class="form-select">
              <option value="normal">Normal</option>
              <option value="urgent">Urgent</option>
              <option value="low">Low</option>
            </select>
          </div>
        </div>

        <div class="form-group">
          <label class="form-label">Approval</label>
          <label style="margin-right:16px"><input type="radio" name="pr-approval" value="true" checked> Manager approval required</label>
          <label><input type="radio" name="pr-approval" value="false"> Manager approval not required</label>
        </div>

        <div class="form-group">
          <label class="form-label">Business Purpose / Reason *</label>
          <input type="text" id="pr-purpose" class="form-input" placeholder="e.g. Die repair for Header Machine #4 / Plant Maintenance" required>
        </div>

        <div style="display:flex; justify-content:space-between; align-items:center; margin: 20px 0 10px;">
          <h4 style="font-size:14px; text-transform:uppercase; color:#0f172a;">Requested Items</h4>
          <div style="display:flex; gap:8px;">
            <button type="button" class="btn btn-secondary btn-sm" onclick="addEmpPRLineRow()">+ Add Item from Catalogue</button>
            <button type="button" class="btn btn-secondary btn-sm" onclick="addCustomEmpPRLineRow()">+ Request Custom Item</button>
          </div>
        </div>

        <div id="pr-lines-container"></div>

        <div style="display:flex; justify-content:flex-end; gap:10px; margin-top:24px; border-top:1px solid #e2e8f0; padding-top:16px;">
          <button type="button" class="btn btn-secondary" onclick="savePR(false)">Save as Draft</button>
      <button type="button" class="btn btn-primary" onclick="savePR(true)">Submit Requisition</button>
        </div>
      </form>
    </div>
  `;

  // Set default date to 7 days ahead
  const nextWeek = new Date();
  nextWeek.setDate(nextWeek.getDate() + 7);
  document.getElementById("pr-required-by").value = nextWeek.toISOString().split("T")[0];

  addEmpPRLineRow();
}

window.addEmpPRLineRow = function() {
  const container = document.getElementById("pr-lines-container");
  const rowId = "pr-row-" + Date.now();
  const div = document.createElement("div");
  div.className = "requisition-item-row";
  div.id = rowId;

  div.innerHTML = `
    <div>
      <label class="form-label" style="font-size:10px;">Catalogue Item</label>
      <select class="form-select emp-pr-item" onchange="onEmpPRItemSelect(this, '${rowId}')">
        <option value="">-- Choose Item from Catalogue --</option>
        ${(window.employeeCatalogItems || []).map(i => `<option value="${escapeHtml(i.id)}" data-name="${escapeHtml(i.name)}" data-part="${escapeHtml(i.part_number||'')}" data-unit="${escapeHtml(i.unit)}">${escapeHtml(i.name)} (SKU: ${escapeHtml(i.sku)})</option>`).join("")}
      </select>
      <input type="text" class="form-input emp-pr-name" style="margin-top:4px;" placeholder="Item name" required>
      <input type="hidden" class="emp-pr-part" value="">
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Quantity *</label>
      <input type="number" class="form-input emp-pr-qty" value="50" min="1" step="0.1" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Unit *</label>
      <input type="text" class="form-input emp-pr-unit" value="Nos" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Line Notes / Specs</label>
      <input type="text" class="form-input emp-pr-notes" placeholder="e.g. Zinc coated">
    </div>
    <div>
      <button type="button" class="btn btn-danger btn-sm" onclick="document.getElementById('${rowId}').remove()">×</button>
    </div>
  `;
  container.appendChild(div);
};

window.addCustomEmpPRLineRow = function() {
  const container = document.getElementById("pr-lines-container");
  const rowId = "pr-row-" + Date.now();
  const div = document.createElement("div");
  div.className = "requisition-item-row";
  div.id = rowId;

  div.innerHTML = `
    <div>
      <label class="form-label" style="font-size:10px; color:var(--color-primary);">✨ Custom Non-Catalogue Item</label>
      <input type="text" class="form-input emp-pr-name" placeholder="Describe item or special tool needed" required>
      <input type="hidden" class="emp-pr-item" value="">
      <input type="hidden" class="emp-pr-part" value="">
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Quantity *</label>
      <input type="number" class="form-input emp-pr-qty" value="10" min="1" step="0.1" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Unit *</label>
      <input type="text" class="form-input emp-pr-unit" value="Nos" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Specifications</label>
      <input type="text" class="form-input emp-pr-notes" placeholder="Brand, dimensions, grade">
    </div>
    <div>
      <button type="button" class="btn btn-danger btn-sm" onclick="document.getElementById('${rowId}').remove()">×</button>
    </div>
  `;
  container.appendChild(div);
};

window.onEmpPRItemSelect = function(selectEl, rowId) {
  const row = document.getElementById(rowId);
  const selected = selectEl.options[selectEl.selectedIndex];
  if (selected.value) {
    row.querySelector(".emp-pr-name").value = selected.dataset.name;
    row.querySelector(".emp-pr-part").value = selected.dataset.part;
    row.querySelector(".emp-pr-unit").value = selected.dataset.unit;
  }
};

window.savePR = async function(submitImmediately) {
  const department_id = document.getElementById("pr-dept").value;
  const required_by = document.getElementById("pr-required-by").value;
  const priority = document.getElementById("pr-priority").value;
  const purpose = document.getElementById("pr-purpose").value;
  const approval_required = document.querySelector('input[name="pr-approval"]:checked').value === "true";

  if (!purpose.trim()) {
    showToast("Please enter a purpose for this requisition", "error");
    return;
  }

  const rows = document.querySelectorAll(".requisition-item-row");
  const lines = [];
  rows.forEach(r => {
    const item_id = r.querySelector(".emp-pr-item").value || null;
    const item_name = r.querySelector(".emp-pr-name").value;
    const part_number = r.querySelector(".emp-pr-part").value || null;
    const quantity = parseFloat(r.querySelector(".emp-pr-qty").value) || 0;
    const unit = r.querySelector(".emp-pr-unit").value;
    const notes = r.querySelector(".emp-pr-notes").value;

    if (item_name && quantity > 0) {
      lines.push({ item_id, item_name, part_number, quantity, unit, notes });
    }
  });

  if (lines.length === 0) {
    showToast("Please add at least one line item", "error");
    return;
  }

  try {
    const res = await apiRequest("/api/v1/requisitions", {
      method: "POST",
      body: JSON.stringify({ department_id, required_by, priority, purpose, approval_required, lines })
    });

    if (submitImmediately) {
      await apiRequest(`/api/v1/requisitions/${res.id}/submit`, { method: "POST" });
      showToast(approval_required ? `Requisition ${res.number} submitted for manager approval.` : `Requisition ${res.number} submitted and approved.`, "success");
    } else {
      showToast(`Requisition ${res.number} saved as draft`, "success");
    }

    navigateTo("my-requisitions");
  } catch (err) {}
};

// 3. MY REQUISITIONS
async function renderMyRequisitions(container) {
  const prs = await apiRequest("/api/v1/requisitions");

  container.innerHTML = `
    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>PR Number</th>
              <th>Required By</th>
              <th>Purpose</th>
              <th>Status</th>
              <th>Line Items</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${prs.length === 0 ? '<tr><td colspan="6" style="text-align:center; color:#64748b;">No requisitions found.</td></tr>' : prs.map(r => `
              <tr>
                <td><strong>${escapeHtml(r.number)}</strong></td>
                <td>${formatDate(r.required_by)}</td>
                <td>${escapeHtml(r.purpose)}</td>
                <td>${renderStatusBadge(r.status)}</td>
                <td>${r.line_count} item(s)</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewMyPRDetail(${jsArg(r.id)})">View</button>
                  ${currentUser.can_procure && ['approved','in_progress'].includes(r.status) ? `<button class="btn btn-primary btn-sm" onclick="openEmployeeEnquiryModal(${jsArg(r.id)})">→ Create Enquiry</button>` : ''}
                  ${r.status === 'draft' ? `<button class="btn btn-primary btn-sm" onclick="submitDraftPR(${jsArg(r.id)})">Submit</button>` : ''}
                  ${['draft', 'submitted'].includes(r.status) ? `<button class="btn btn-danger btn-sm" onclick="cancelMyPR(${jsArg(r.id)})">Cancel</button>` : ''}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

window.submitDraftPR = async function(prId) {
  try {
    await apiRequest(`/api/v1/requisitions/${prId}/submit`, { method: "POST" });
    showToast("PR submitted for manager approval", "success");
    renderCurrentPage();
  } catch (err) {}
};

window.cancelMyPR = async function(prId) {
  if (confirm("Are you sure you want to cancel this requisition?")) {
    try {
      await apiRequest(`/api/v1/requisitions/${prId}/cancel`, {
        method: "POST",
        body: JSON.stringify({ note: "Cancelled by requester" })
      });
      showToast("Requisition cancelled", "info");
      renderCurrentPage();
    } catch (err) {}
  }
};

window.viewMyPRDetail = async function(prId) {
  const pr = await apiRequest(`/api/v1/requisitions/${prId}`);
  openModal(`Requisition ${pr.number}`, `
    <div style="margin-bottom:16px;">
      <div style="display:flex; justify-content:space-between; margin-bottom:6px;">
        <div><strong>Status:</strong> ${renderStatusBadge(pr.status)}</div>
        <div><strong>Required By:</strong> ${formatDate(pr.required_by)}</div>
      </div>
      <div><strong>Purpose:</strong> ${escapeHtml(pr.purpose)}</div>
      ${pr.decision_note ? `<div style="margin-top:6px; padding:8px; background:#fee2e2; border-radius:4px; color:#b91c1c;"><strong>Manager Note:</strong> ${escapeHtml(pr.decision_note)}</div>` : ''}
    </div>

    <h4>Line Items</h4>
    <div class="table-responsive" style="margin-top:8px;">
      <table class="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Part #</th>
            <th>Quantity</th>
            <th>Unit</th>
            <th>Procurement Status</th>
          </tr>
        </thead>
        <tbody>
          ${pr.lines.map(l => `
            <tr>
              <td><strong>${escapeHtml(l.item_name_snapshot)}</strong></td>
              <td>${l.part_number || '—'}</td>
              <td>${l.quantity}</td>
              <td>${escapeHtml(l.unit)}</td>
              <td>${renderStatusBadge(l.procure_status)}</td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `);
};

// 4. ITEM CATALOGUE
async function renderCatalogue(container) {
  const items = await apiRequest("/api/v1/items");

  container.innerHTML = `
    <div class="page-header">
      <input type="text" class="form-input" style="max-width:320px;" placeholder="Search SKU, name, part #..." oninput="filterCatalogue(this.value)">
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table" id="emp-catalogue-table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Item Name</th>
              <th>Part Number</th>
              <th>Description</th>
              <th>Unit</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(i => `
              <tr>
                <td><code>${escapeHtml(i.sku)}</code></td>
                <td><strong>${escapeHtml(i.name)}</strong></td>
                <td>${i.part_number || '—'}</td>
                <td style="color:#64748b;">${escapeHtml(i.description || '—')}</td>
                <td>${escapeHtml(i.unit)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

window.filterCatalogue = function(term) {
  const lower = term.toLowerCase();
  document.querySelectorAll("#emp-catalogue-table tbody tr").forEach(row => {
    row.style.display = row.textContent.toLowerCase().includes(lower) ? "" : "none";
  });
};

// 5. NOTIFICATIONS
async function renderNotifications(container) {
  const notifs = await apiRequest("/api/v1/notifications");

  container.innerHTML = `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:16px;">
        <h3>In-App Notifications</h3>
        <button class="btn btn-secondary btn-sm" onclick="markAllNotificationsRead()">Mark all as read</button>
      </div>

      <div style="display:flex; flex-direction:column; gap:10px;">
        ${notifs.length === 0 ? '<p style="color:#64748b;">No notifications yet.</p>' : notifs.map(n => `
          <div style="padding:12px; border-radius:6px; border:1px solid ${n.read_at ? '#e2e8f0' : '#bae6fd'}; background:${n.read_at ? '#fff' : '#f0f9ff'};">
            <div style="display:flex; justify-content:space-between; margin-bottom:4px;">
              <strong style="color:#0f172a;">${escapeHtml(n.title)}</strong>
              <small style="color:#64748b;">${formatDateTime(n.created_at)}</small>
            </div>
            <div style="color:#475569; font-size:0.875rem;">${escapeHtml(n.body)}</div>
          </div>
        `).join("")}
      </div>
    </div>
  `;
}

window.markAllNotificationsRead = async function() {
  await apiRequest("/api/v1/notifications/read-all", { method: "POST" });
  showToast("All notifications marked as read", "success");
  renderCurrentPage();
};

// 6. PROFILE
async function renderProfile(container) {
  container.innerHTML = `
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px; max-width:960px;">
      <div class="card">
        <h3 style="margin-bottom:14px;">👤 My Profile Details</h3>
        <form id="emp-profile-form">
          <div class="form-group">
            <label class="form-label">Full Name</label>
            <input type="text" id="emp_full_name" class="form-input" value="${escapeHtml(currentUser.full_name || '')}" required>
          </div>
          <div class="form-group">
            <label class="form-label">Work Email</label>
            <input type="email" class="form-input" value="${escapeHtml(currentUser.email || '')}" disabled style="background:#f1f5f9; cursor:not-allowed;">
          </div>
          <div class="form-group">
            <label class="form-label">Phone Number</label>
            <input type="text" id="emp_phone" class="form-input" placeholder="+91 98200 00000" value="${escapeHtml(currentUser.phone || '')}">
          </div>
          <div class="form-group">
            <label class="form-label">Department / Access</label>
            <div style="font-size:12px; color:#475569; padding:8px; background:#f8fafc; border-radius:6px; border:1px solid #e2e8f0;">
              <div>Department: <strong>${escapeHtml(currentUser.department_name || 'N/A')}</strong></div>
              <div>Procurement Access: <strong>${currentUser.can_procure ? '✔ Enabled' : '✘ Off'}</strong></div>
              <div>Receiving Access: <strong>${currentUser.can_receive ? '✔ Enabled' : '✘ Off'}</strong></div>
            </div>
          </div>
          <button type="submit" class="btn btn-primary">Save Profile Details</button>
        </form>
      </div>

      <div class="card">
        <h3 style="margin-bottom:14px;">🔒 Change Account Password</h3>
        <form id="emp-password-form">
          <div class="form-group">
            <label class="form-label">Current Password</label>
            <input type="password" id="emp_current_password" class="form-input" placeholder="••••••••" autocomplete="current-password" required>
          </div>
          <div class="form-group">
            <label class="form-label">New Password</label>
            <input type="password" id="emp_new_password" class="form-input" placeholder="At least 8 characters" autocomplete="new-password" minlength="8" required>
          </div>
          <div class="form-group">
            <label class="form-label">Confirm New Password</label>
            <input type="password" id="emp_confirm_password" class="form-input" placeholder="Repeat new password" autocomplete="new-password" minlength="8" required>
          </div>
          <button type="submit" class="btn btn-primary">Update Password</button>
        </form>
      </div>
    </div>
  `;

  document.getElementById("emp-profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const full_name = document.getElementById("emp_full_name").value.trim();
    const phone = document.getElementById("emp_phone").value.trim();
    try {
      await apiRequest("/api/v1/auth/me", {
        method: "PATCH",
        body: JSON.stringify({ full_name, phone })
      });
      currentUser.full_name = full_name;
      currentUser.phone = phone;
      document.getElementById("header-user-name").textContent = full_name;
      showToast("Profile details updated successfully", "success");
    } catch (_) {}
  });

  document.getElementById("emp-password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const current_password = document.getElementById("emp_current_password").value;
    const new_password = document.getElementById("emp_new_password").value;
    const confirm_password = document.getElementById("emp_confirm_password").value;

    if (new_password !== confirm_password) {
      showToast("New passwords do not match", "error");
      return;
    }
    try {
      await apiRequest("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password, new_password })
      });
      document.getElementById("emp-password-form").reset();
      showToast("Password changed successfully", "success");
    } catch (_) {}
  });
}

window.navigateTo = function(pageName) {
  document.querySelectorAll(".nav-item").forEach(i => {
    if (i.dataset.page === pageName) i.classList.add("active");
    else i.classList.remove("active");
  });
  currentPage = pageName;
  renderCurrentPage();
};

