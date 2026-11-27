import { apiRequest, fetchCurrentUser, showToast } from "/static/shared/js/api.js?v=lr-workflow-20261126";
import { formatCurrency, formatDate, formatDateTime, renderStatusBadge } from "/static/shared/js/format.js";

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

// Initialize
document.addEventListener("DOMContentLoaded", async () => {
  currentUser = await fetchCurrentUser();
  if (!currentUser) return;
  
  if (currentUser.role !== "manager") {
    window.location.href = "/app/employee/";
    return;
  }

  document.getElementById("header-user-name").textContent = currentUser.full_name;
  document.getElementById("header-user-dept").textContent = `${currentUser.department_name || 'Admin'} (Manager)`;

  const profileHeader = document.querySelector(".user-profile-menu");
  if (profileHeader) {
    profileHeader.style.cursor = "pointer";
    profileHeader.title = "Click to view profile & settings";
    profileHeader.addEventListener("click", (e) => {
      if (e.target.id !== "btn-logout") {
        window.navigateTo("settings");
      }
    });
  }

  // Navigation Setup
  document.querySelectorAll(".nav-item").forEach(item => {
    item.addEventListener("click", () => {
      document.querySelectorAll(".nav-item").forEach(i => i.classList.remove("active"));
      item.classList.add("active");
      currentPage = item.dataset.page;
      renderCurrentPage();
    });
  });

  // Logout
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
        title.textContent = "Dashboard Overview";
        await renderDashboard(container);
        break;
      case "approvals":
        title.textContent = "Approvals Inbox";
        await renderApprovals(container);
        break;
      case "requisitions":
        title.textContent = "Purchase Requisitions (PR)";
        await renderRequisitions(container);
        break;
      case "enquiries":
        title.textContent = "Enquiries & Multi-Vendor Quotes";
        await renderEnquiries(container);
        break;
      case "pos":
        title.textContent = "Purchase Orders (PO)";
        await renderPOs(container);
        break;
      case "grns":
        title.textContent = "Goods Receipt Notes (GRN)";
        await renderGRNs(container);
        break;
      case "items":
        title.textContent = "Items Master";
        await renderItems(container);
        break;
      case "vendors":
        title.textContent = "Vendors Master";
        await renderVendors(container);
        break;
      case "reports":
        title.textContent = "Reports & Analytics";
        await renderReports(container);
        break;
      case "lrs":
        title.textContent = "Lorry Receipts & Transport Payments";
        await renderLRs(container);
        break;
      case "team":
        title.textContent = "Team & Access Control";
        await renderTeam(container);
        break;
      case "settings":
        title.textContent = "System Settings";
        await renderSettings(container);
        break;
      case "audit":
        title.textContent = "Audit Trail";
        await renderAudit(container);
        break;
    }
    const exportEntity = { requisitions: "requisitions", enquiries: "enquiries", pos: "purchase-orders", grns: "grns", items: "items", vendors: "vendors", lrs: "lrs", team: "users", audit: "audit" }[currentPage];
    const pageHeader = container.querySelector(".page-header");
    if (exportEntity && pageHeader && !pageHeader.innerHTML.includes("/api/v1/exports/")) pageHeader.insertAdjacentHTML("afterbegin", `<div>${exportButtons(exportEntity)}</div>`);
  } catch (err) {
    container.innerHTML = `<div class="card" style="color:var(--color-danger);">Error loading page: ${escapeHtml(err.message)}</div>`;
  }
}

// 1. DASHBOARD
async function renderDashboard(container) {
  const summary = await apiRequest("/api/v1/reports/summary");
  const prs = await apiRequest("/api/v1/requisitions?status=submitted");
  const pos = await apiRequest("/api/v1/purchase-orders?status=pending_approval");

  const totalPending = (summary.prs_to_approve || 0) + (summary.pos_to_approve || 0);
  const badge = document.getElementById("badge-inbox-count");
  if (totalPending > 0) {
    badge.textContent = totalPending;
    badge.style.display = "inline-flex";
  } else {
    badge.style.display = "none";
  }

  container.innerHTML = `
    <div class="metrics-grid">
      <div class="metric-card">
        <span class="metric-label">PRs Awaiting Approval</span>
        <span class="metric-value" style="color:#d97706;">${summary.prs_to_approve}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">POs Awaiting Approval</span>
        <span class="metric-value" style="color:var(--color-primary);">${summary.pos_to_approve}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Open Active POs</span>
        <span class="metric-value" style="color:#2563eb;">${summary.open_pos}</span>
      </div>
      <div class="metric-card">
        <span class="metric-label">Total Spend Commited</span>
        <span class="metric-value" style="color:#059669;">${formatCurrency(summary.total_spend)}</span>
      </div>
    </div>

    <div style="display:grid; grid-template-columns: 2fr 1fr; gap:20px;">
      <div class="card">
        <h3 style="margin-bottom:12px;">Immediate Action Required</h3>
        ${(prs.length === 0 && pos.length === 0) ? `
          <div style="padding:20px; text-align:center; color:#64748b;">
            All approval queues are clear! 🎉
          </div>
        ` : `
          <div class="table-responsive">
            <table class="table">
              <thead>
                <tr>
                  <th>Type</th>
                  <th>Document #</th>
                  <th>Details</th>
                  <th>Action</th>
                </tr>
              </thead>
              <tbody>
                ${prs.map(pr => `
                  <tr>
                    <td><span class="badge badge-submitted">PR</span></td>
                    <td><strong>${escapeHtml(pr.number)}</strong></td>
                    <td>${escapeHtml(pr.purpose)} (${escapeHtml(pr.requester_name)})</td>
                    <td>
                      <button class="btn btn-primary btn-sm" onclick="approvePRModal(${jsArg(pr.id)}, ${jsArg(pr.number)})">Approve</button>
                      <button class="btn btn-danger btn-sm" onclick="rejectPRModal(${jsArg(pr.id)}, ${jsArg(pr.number)})">Reject</button>
                    </td>
                  </tr>
                `).join("")}
                ${pos.map(po => `
                  <tr>
                    <td><span class="badge badge-pending_approval">PO</span></td>
                    <td><strong>${escapeHtml(po.number)}</strong></td>
                    <td>${escapeHtml(po.vendor_name)} — ${formatCurrency(po.grand_total)}</td>
                    <td>
                      <button class="btn btn-primary btn-sm" onclick="approvePOModal(${jsArg(po.id)}, ${jsArg(po.number)})">Approve</button>
                    </td>
                  </tr>
                `).join("")}
              </tbody>
            </table>
          </div>
        `}
      </div>

      <div class="card">
        <h3 style="margin-bottom:12px;">Recent Audit Activity</h3>
        <div style="display:flex; flex-direction:column; gap:10px; font-size:0.8rem;">
          ${summary.recent_activity.map(act => `
            <div style="border-bottom:1px solid #f1f5f9; padding-bottom:6px;">
              <div style="font-weight:600; color:#0f172a;">${escapeHtml(act.action)}</div>
              <div style="color:#64748b;">By: ${escapeHtml(act.actor_email)} · ${formatDateTime(act.occurred_at)}</div>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;
}

// 2. APPROVALS INBOX
async function renderApprovals(container) {
  const prs = await apiRequest("/api/v1/requisitions?status=submitted");
  const pos = await apiRequest("/api/v1/purchase-orders?status=pending_approval");

  container.innerHTML = `
    <div class="card">
      <h3 style="margin-bottom:16px;">Requisitions Awaiting Your Approval</h3>
      ${prs.length === 0 ? '<p style="color:#64748b;">No requisitions pending approval.</p>' : `
        <div class="table-responsive">
          <table class="table">
            <thead>
              <tr>
                <th>PR Number</th>
                <th>Requester</th>
                <th>Department</th>
                <th>Required By</th>
                <th>Purpose</th>
                <th>Lines</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              ${prs.map(r => `
                <tr>
                  <td><strong>${escapeHtml(r.number)}</strong></td>
                  <td>${escapeHtml(r.requester_name)}</td>
                  <td>${escapeHtml(r.department_name)}</td>
                  <td>${formatDate(r.required_by)}</td>
                  <td>${escapeHtml(r.purpose)}</td>
                  <td>${r.line_count} item(s)</td>
                  <td>
                    <button class="btn btn-secondary btn-sm" onclick="viewPRDetail('${r.id}')">View</button>
                    <button class="btn btn-primary btn-sm" onclick="approvePRModal(${jsArg(r.id)}, ${jsArg(r.number)})">Approve</button>
                    <button class="btn btn-danger btn-sm" onclick="rejectPRModal(${jsArg(r.id)}, ${jsArg(r.number)})">Reject</button>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `}
    </div>

    <div class="card">
      <h3 style="margin-bottom:16px;">Purchase Orders Awaiting Your Approval</h3>
      ${pos.length === 0 ? '<p style="color:#64748b;">No POs pending approval.</p>' : `
        <div class="table-responsive">
          <table class="table">
            <thead>
              <tr>
                <th>PO Number</th>
                <th>Vendor</th>
                <th>Created By</th>
                <th>Order Date</th>
                <th>Grand Total</th>
                <th>Actions</th>
              </tr>
            </thead>
            <tbody>
              ${pos.map(p => `
                <tr>
                  <td><strong>${escapeHtml(p.number)}</strong> (Rev ${p.revision})</td>
                  <td>${escapeHtml(p.vendor_name)}</td>
                  <td>${escapeHtml(p.creator_name)}</td>
                  <td>${formatDate(p.order_date)}</td>
                  <td><strong>${formatCurrency(p.grand_total)}</strong></td>
                  <td>
                    <button class="btn btn-secondary btn-sm" onclick="viewPODetail('${p.id}')">View</button>
                    <button class="btn btn-primary btn-sm" onclick="approvePOModal(${jsArg(p.id)}, ${jsArg(p.number)})">Approve</button>
                    <button class="btn btn-danger btn-sm" onclick="cancelPOModal(${jsArg(p.id)}, ${jsArg(p.number)})">Reject</button>
                  </td>
                </tr>
              `).join("")}
            </tbody>
          </table>
        </div>
      `}
    </div>
  `;
}

// 3. REQUISITIONS
async function renderRequisitions(container) {
  const prs = await apiRequest("/api/v1/requisitions");

  container.innerHTML = `
    <div class="page-header">
      <div>${exportButtons('requisitions')}</div>
      <div style="display:flex; gap:10px;">
        <!-- PRs are created by Employees, but managers can review all -->
      </div>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>PR Number</th>
              <th>Department</th>
              <th>Requester</th>
              <th>Required By</th>
              <th>Priority</th>
              <th>Status</th>
              <th>Lines</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${prs.map(r => `
              <tr>
                <td><strong>${escapeHtml(r.number)}</strong></td>
                <td>${escapeHtml(r.department_name)}</td>
                <td>${escapeHtml(r.requester_name)}</td>
                <td>${formatDate(r.required_by)}</td>
                <td><span style="text-transform:capitalize; font-weight:600;">${escapeHtml(r.priority)}</span></td>
                <td>${renderStatusBadge(r.status)}</td>
                <td>${r.line_count} items</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewPRDetail('${r.id}')">View</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 4. ENQUIRIES (RFQ)
async function renderEnquiries(container) {
  const enquiries = await apiRequest("/api/v1/enquiries");

  container.innerHTML = `
    <div class="page-header">
      <div>${exportButtons('enquiries')}</div>
      <span style="color:#64748b;">Employees create enquiries · Managers review and award</span>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>Enquiry #</th>
              <th>Subject</th>
              <th>Invited Vendors</th>
              <th>Awarded To</th>
              <th>Status</th>
              <th>Created By</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${enquiries.length === 0 ? '<tr><td colspan="7" style="text-align:center; color:#64748b;">No enquiries yet.</td></tr>' : enquiries.map(e => `
              <tr>
                <td><strong>${escapeHtml(e.number)}</strong></td>
                <td>${escapeHtml(e.subject)}</td>
                <td>${e.vendor_count} vendor(s)</td>
                <td>${e.awarded_vendor_name ? `<strong>${escapeHtml(e.awarded_vendor_name)}</strong>` : '—'}</td>
                <td>${renderStatusBadge(e.status)}</td>
                <td>${escapeHtml(e.creator_name)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewEnquiryDetail('${e.id}')">View Quotes & Matrix</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

window.createEnquiryFromPRModal = async function(prId) {
  const [pr, vendors] = await Promise.all([apiRequest(`/api/v1/requisitions/${prId}`), apiRequest('/api/v1/vendors')]);
  if (!vendors.length) { showToast('Add a vendor before creating an enquiry.', 'error'); return; }
  openModal(`Create Enquiry from ${pr.number}`, `<form id="pr-enquiry-form">
    <div class="form-group"><label class="form-label">Enquiry Subject *</label><input id="enq-subject" class="form-input" required value="RFQ for ${escapeHtml(pr.number)}"></div>
    <div class="form-group"><label class="form-label">Invite Vendors *</label><select id="enq-vendors" class="form-select" multiple required size="${Math.min(vendors.length,5)}">${vendors.map(v=>`<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)}</option>`).join('')}</select><small>Use Ctrl/Cmd to select multiple vendors.</small></div>
    <div style="margin:12px 0;"><strong>Requisition lines</strong><ul>${pr.lines.filter(l=>l.procure_status==='open').map(l=>`<li>${escapeHtml(l.item_name_snapshot)} — ${l.quantity} ${escapeHtml(l.unit)}</li>`).join('') || '<li>No open requisition lines remain.</li>'}</ul></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Create Enquiry</button></div></form>`);
  document.getElementById('pr-enquiry-form').addEventListener('submit', async e=>{e.preventDefault(); const vendor_ids=[...document.getElementById('enq-vendors').selectedOptions].map(o=>o.value); if(!vendor_ids.length){showToast('Select at least one vendor.','error');return;} try {const result=await apiRequest('/api/v1/enquiries',{method:'POST',body:JSON.stringify({subject:document.getElementById('enq-subject').value,requisition_id:prId,vendor_ids})});showToast(`Enquiry ${result.number} created.`, 'success');closeModal();currentPage='enquiries';document.querySelectorAll('.nav-item').forEach(i=>i.classList.toggle('active',i.dataset.page==='enquiries'));renderCurrentPage();}catch(err){}});
};

window.viewPRDetail = async function(prId) {
  const pr = await apiRequest(`/api/v1/requisitions/${prId}`);
  openModal(`Requisition ${pr.number}`, `<p><strong>${escapeHtml(pr.purpose)}</strong> · ${escapeHtml(pr.department_name || '')}</p><p>Status: ${renderStatusBadge(pr.status)} · Required by ${formatDate(pr.required_by)}</p><div class="table-responsive"><table class="table"><thead><tr><th>Item</th><th>Quantity</th><th>Unit</th><th>Procurement</th></tr></thead><tbody>${pr.lines.map(line=>`<tr><td>${escapeHtml(line.item_name_snapshot)}</td><td>${line.quantity}</td><td>${escapeHtml(line.unit)}</td><td>${escapeHtml(line.procure_status)}</td></tr>`).join('')}</tbody></table></div>`);
};

window.convertEnquiryToPO = async function(enquiryId, number) {
  openModal(`Convert ${number} to Purchase Order`, `<form id="convert-enq-po-form"><p>The awarded vendor and its quoted line rates will be copied into a PO draft.</p><div class="form-group"><label class="form-label">Expected Delivery Date</label><input id="enq-po-delivery" type="date" class="form-input"></div><div style="display:flex;justify-content:flex-end;gap:8px;"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Create PO Draft</button></div></form>`);
  document.getElementById('convert-enq-po-form').addEventListener('submit',async e=>{e.preventDefault();try{const result=await apiRequest(`/api/v1/enquiries/${enquiryId}/convert-to-po`,{method:'POST',body:JSON.stringify({delivery_date:document.getElementById('enq-po-delivery').value||null})});showToast(`PO ${result.number} created as draft.`, 'success');closeModal();currentPage='pos';document.querySelectorAll('.nav-item').forEach(i=>i.classList.toggle('active',i.dataset.page==='pos'));renderCurrentPage();}catch(err){}});
};

window.viewEnquiryDetail = async function(enquiryId) {
  const enq = await apiRequest(`/api/v1/enquiries/${enquiryId}`);
  const lineNames = Object.fromEntries(enq.lines.map(l=>[l.id,l.item_name_snapshot]));
  openModal(`Enquiry ${enq.number}`, `<p><strong>${escapeHtml(enq.subject)}</strong> · ${renderStatusBadge(enq.status)}</p>
    <div class="table-responsive"><table class="table"><thead><tr><th>Vendor</th><th>Quoted lines</th><th>Quote total</th><th>Action</th></tr></thead><tbody>
    ${enq.vendors.map(v=>{const total=v.quotes.reduce((sum,q)=>sum+(Number(q.quoted_rate||0)*Number(enq.lines.find(l=>l.id===q.enquiry_line_id)?.quantity||0)),0);return `<tr><td><strong>${escapeHtml(v.vendor_name)}</strong></td><td>${v.quotes.map(q=>`${escapeHtml(lineNames[q.enquiry_line_id]||'Item')}: ${formatCurrency(q.quoted_rate)}`).join('<br>')||'No quote recorded'}</td><td>${formatCurrency(total)}</td><td>${enq.status!=='converted'?`<button class="btn btn-primary btn-sm" onclick="awardEnquiryVendor('${enquiryId}', ${jsArg(v.vendor_id)})">Award</button>`:''}</td></tr>`}).join('')}
    </tbody></table></div>`);
};
window.awardEnquiryVendor = async function(enquiryId, vendorId) {
  try { await apiRequest(`/api/v1/enquiries/${enquiryId}/award`,{method:'POST',body:JSON.stringify({vendor_id:vendorId})});showToast('Vendor awarded to enquiry.','success');closeModal();renderCurrentPage();} catch(err){}
};

// 5. PURCHASE ORDERS
async function renderPOs(container) {
  const [pos,lrs] = await Promise.all([apiRequest("/api/v1/purchase-orders"),apiRequest("/api/v1/lrs")]);

  container.innerHTML = `
    <div class="page-header">
      <div>${exportButtons('purchase-orders')}</div>
      <span style="color:#64748b;">Employees create and submit POs · Managers approve and monitor</span>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>PO Number</th>
              <th>Vendor</th>
              <th>Order Date</th>
              <th>Delivery Date</th>
              <th>Subtotal</th>
              <th>Grand Total</th>
              <th>Transport LRs</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${pos.length === 0 ? '<tr><td colspan="9" style="text-align:center; color:#64748b;">No purchase orders yet.</td></tr>' : pos.map(p => `
              <tr>
                <td><strong>${escapeHtml(p.number)}</strong> (Rev ${p.revision})</td>
                <td><strong>${escapeHtml(p.vendor_name)}</strong></td>
                <td>${formatDate(p.order_date)}</td>
                <td>${formatDate(p.delivery_date)}</td>
                <td>${formatCurrency(p.subtotal)}</td>
                <td><strong>${formatCurrency(p.grand_total)}</strong></td>
                <td>${lrs.filter(lr=>lr.purchase_order_id===p.id).map(lr=>`<div>${escapeHtml(lr.number)} · ${escapeHtml(lr.payment_status)}${lr.document_filename?` · <a href="/api/v1/lrs/${lr.id}/document">LR document</a>`:''}</div>`).join('')||'—'}</td>
                <td>${renderStatusBadge(p.status)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewPODetail('${p.id}')">View</button>
                  <a href="/api/v1/purchase-orders/${p.id}/print" target="_blank" class="btn btn-secondary btn-sm">🖨️ Letterhead</a>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 6. GOODS RECEIPT (GRN)
async function renderGRNs(container) {
  const [grns, lrs] = await Promise.all([apiRequest("/api/v1/grns"), apiRequest("/api/v1/lrs")]);

  container.innerHTML = `
    <div class="page-header">
      <div style="display:flex;gap:8px;"><button class="btn btn-secondary" onclick="document.querySelector('.nav-item[data-page=\'lrs\']').click()">Manage LRs</button></div>
      <span style="color:#64748b;">Employees post GRNs · Managers monitor receipts and attached LRs</span>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>GRN Number</th>
              <th>PO Number</th>
              <th>Vendor</th>
              <th>Received Date</th>
              <th>Transporter / LR</th>
              <th>Attached transport docs</th>
              <th>Received By</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${grns.length === 0 ? '<tr><td colspan="9" style="text-align:center; color:#64748b;">No goods receipts posted yet.</td></tr>' : grns.map(g => `
              <tr>
                <td><strong>${escapeHtml(g.number)}</strong></td>
                <td>${escapeHtml(g.po_number)}</td>
                <td>${escapeHtml(g.vendor_name)}</td>
                <td>${formatDate(g.received_date)}</td>
                <td>${escapeHtml(g.transporter || '—')} / ${escapeHtml(g.lr_number || '—')}</td>
                <td>${lrs.filter(lr=>lr.purchase_order_id===g.purchase_order_id).map(lr=>`<div><strong>${escapeHtml(lr.number)}</strong> · ${escapeHtml(lr.transporter_name||'Transporter pending')}${lr.document_filename?` · <a href="/api/v1/lrs/${lr.id}/document">Document</a>`:''}</div>`).join('') || '<span style="color:#64748b;">No LR attached</span>'}</td>
                <td>${escapeHtml(g.receiver_name)}</td>
                <td>${renderStatusBadge(g.status)}</td>
                <td>
                  <button class="btn btn-secondary btn-sm" onclick="viewGRNDetail('${g.id}')">View</button>
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 7. ITEMS MASTER
async function renderItems(container) {
  const items = await apiRequest("/api/v1/items");

  container.innerHTML = `
    <div class="page-header">
      <div style="display:flex;align-items:center;gap:8px;"><input type="text" id="items-search" class="form-input" style="max-width:300px;" placeholder="Search SKU, name, part #..." oninput="searchItems(this.value)">${exportButtons('items')}</div>
      <button class="btn btn-primary" onclick="openAddItemModal()">+ Add New Item</button>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table" id="items-table">
          <thead>
            <tr>
              <th>SKU</th>
              <th>Item Name</th>
              <th>Part Number</th>
              <th>HSN</th>
              <th>Unit</th>
              <th>Default Rate</th>
              <th>GST %</th>
            </tr>
          </thead>
          <tbody>
            ${items.map(it => `
              <tr>
                <td><code>${escapeHtml(it.sku)}</code></td>
                <td><strong>${escapeHtml(it.name)}</strong><br><small style="color:#64748b;">${escapeHtml(it.description || '')}</small></td>
                <td>${escapeHtml(it.part_number || '—')}</td>
                <td>${escapeHtml(it.hsn_code || '7318')}</td>
                <td>${escapeHtml(it.unit)}</td>
                <td>${formatCurrency(it.default_rate)}</td>
                <td>${it.default_tax_percent}%</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 8. VENDORS MASTER
async function renderVendors(container) {
  const vendors = await apiRequest("/api/v1/vendors");

  container.innerHTML = `
    <div class="page-header">
      <div>${exportButtons('vendors')}</div>
      <button class="btn btn-primary" onclick="openAddVendorModal()">+ Add New Vendor</button>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>Vendor Name</th>
              <th>Contact Person</th>
              <th>Email</th>
              <th>Phone</th>
              <th>GSTIN</th>
              <th>State Code</th>
              <th>Payment Terms</th>
            </tr>
          </thead>
          <tbody>
            ${vendors.map(v => `
              <tr>
                <td><strong>${escapeHtml(v.name)}</strong></td>
                <td>${escapeHtml(v.contact_person || '—')}</td>
                <td>${escapeHtml(v.email || '—')}</td>
                <td>${escapeHtml(v.phone || '—')}</td>
                <td><code>${escapeHtml(v.gstin || '—')}</code></td>
                <td>${escapeHtml(v.state_code || '27')}</td>
                <td>${escapeHtml(v.default_payment_terms || '30 Days Net')}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 9. REPORTS
async function renderReports(container) {
  const pending = await apiRequest("/api/v1/reports/pending-deliveries");

  container.innerHTML = `
    <div class="card">
      <div style="display:flex; justify-content:space-between; align-items:center; margin-bottom:14px;">
        <h3>Pending Material Deliveries (Open PO Lines)</h3>
        <div style="display:flex;gap:8px;flex-wrap:wrap;">
          <a href="/api/v1/reports/pending-deliveries?format=csv" class="btn btn-secondary btn-sm" download>Export CSV</a>
          <a href="/api/v1/reports/pending-deliveries?format=xlsx" class="btn btn-secondary btn-sm" download>Export Excel</a>
          <a href="/api/v1/reports/pending-deliveries?format=pdf" class="btn btn-secondary btn-sm" download>Export PDF</a>
        </div>
      </div>
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>PO Number</th>
              <th>Vendor</th>
              <th>Item Description</th>
              <th>Ordered Qty</th>
              <th>Received Qty</th>
              <th>Pending Qty</th>
              <th>Delivery Date</th>
            </tr>
          </thead>
          <tbody>
            ${pending.length === 0 ? '<tr><td colspan="7" style="text-align:center; color:#64748b;">No pending deliveries.</td></tr>' : pending.map(p => `
              <tr>
                <td><strong>${escapeHtml(p.po_number)}</strong></td>
                <td>${escapeHtml(p.vendor_name)}</td>
                <td>${escapeHtml(p.item_name_snapshot)}</td>
                <td>${p.ordered_qty} ${escapeHtml(p.unit)}</td>
                <td>${p.received_qty} ${escapeHtml(p.unit)}</td>
                <td style="color:#b91c1c; font-weight:700;">${p.pending_qty} ${escapeHtml(p.unit)}</td>
                <td>${formatDate(p.delivery_date)}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// LORRY RECEIPTS: each LR is linked to its PO and inherits the PO vendor.
async function renderLRs(container) {
  const [lrs, pos] = await Promise.all([
    apiRequest("/api/v1/lrs"),
    apiRequest("/api/v1/purchase-orders")
  ]);
  const eligible = pos.filter(po => ['approved','sent','part_received','received','closed'].includes(po.status));
  container.innerHTML = `
    <div class="page-header"><p style="color:#64748b;">Employees attach and update LRs · Managers monitor PO/vendor links, contacts, documents and payments.</p></div>
    <div class="card"><div class="table-responsive"><table class="table"><thead><tr>
      <th>LR Number / Date</th><th>Purchase Order</th><th>Vendor</th><th>Transporter / Contact</th><th>Payment</th><th>Actions</th>
    </tr></thead><tbody>
      ${lrs.length ? lrs.map(lr => `<tr>
        <td><strong>${escapeHtml(lr.number)}</strong><br>${formatDate(lr.lr_date)}</td>
        <td><strong>${escapeHtml(lr.po_number)}</strong></td><td>${escapeHtml(lr.vendor_name)}</td>
        <td>${escapeHtml(lr.transporter_name || '—')}<br>${escapeHtml(lr.transporter_contact || '—')}</td>
        <td><span style="text-transform:capitalize;">${escapeHtml(lr.payment_status)}</span><br>${formatCurrency(lr.payment_amount || 0)}${lr.payment_date ? ` · ${formatDate(lr.payment_date)}` : ''}</td>
        <td>${lr.document_filename ? `<a class="btn btn-secondary btn-sm" href="/api/v1/lrs/${lr.id}/document">Open LR</a>` : ''}</td>
      </tr>`).join('') : '<tr><td colspan="6" style="text-align:center;color:#64748b;">No LRs recorded. Attach an LR to an authorized PO to start tracking.</td></tr>'}
    </tbody></table></div></div>`;
  window.eligibleLRPOs = eligible;
}

window.openNewLRModal = function() {
  const pos = window.eligibleLRPOs || [];
  if (!pos.length) { showToast('There are no authorized POs available for LR attachment.', 'info'); return; }
  openModal('Attach Transport LR to Purchase Order', `<form id="new-lr-form">
    <div class="form-group"><label class="form-label">Purchase Order *</label><select id="lr-po" class="form-select" required><option value="">Select PO</option>${pos.map(po => `<option value="${escapeHtml(po.id)}">${escapeHtml(po.number)} — ${escapeHtml(po.vendor_name)}</option>`).join('')}</select></div>
    <div class="form-grid-2"><div class="form-group"><label class="form-label">LR Number *</label><input id="lr-number" class="form-input" required maxlength="80"></div><div class="form-group"><label class="form-label">LR Date</label><input id="lr-date" type="date" class="form-input"></div></div>
    <div class="form-grid-2"><div class="form-group"><label class="form-label">Transporter</label><input id="lr-transporter" class="form-input"></div><div class="form-group"><label class="form-label">Transporter Contact</label><input id="lr-contact" class="form-input" placeholder="Phone / email"></div></div>
    <div class="form-group"><label class="form-label">Transport Document (PDF/JPEG/PNG, max 10 MB)</label><input id="lr-file" type="file" class="form-input" accept=".pdf,.jpg,.jpeg,.png,application/pdf,image/jpeg,image/png"></div>
    <div class="form-group"><label class="form-label">Notes</label><textarea id="lr-notes" class="form-input" rows="2"></textarea></div>
    <div style="display:flex;justify-content:flex-end;gap:8px;"><button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button><button class="btn btn-primary">Attach LR</button></div></form>`);
  document.getElementById('new-lr-form').addEventListener('submit', async e => {
    e.preventDefault();
    try { const lr=await apiRequest('/api/v1/lrs', {method:'POST', body:JSON.stringify({purchase_order_id:document.getElementById('lr-po').value,number:document.getElementById('lr-number').value,lr_date:document.getElementById('lr-date').value || null,transporter_name:document.getElementById('lr-transporter').value || null,transporter_contact:document.getElementById('lr-contact').value || null,notes:document.getElementById('lr-notes').value || null})}); const file=document.getElementById('lr-file').files[0]; if(file){const data=new FormData();data.append('file',file);await apiRequest(`/api/v1/lrs/${lr.id}/document`,{method:'POST',body:data});} showToast('LR attached to PO.', 'success'); closeModal(); renderCurrentPage(); } catch(err) {}
  });
};

// 10. TEAM & ACCESS
async function renderTeam(container) {
  const users = await apiRequest("/api/v1/users");

  container.innerHTML = `
    <div class="page-header">
      <div></div>
      <button class="btn btn-primary" onclick="openInviteUserModal()">+ Add New User</button>
    </div>

    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>Full Name</th>
              <th>Email</th>
              <th>Role</th>
              <th>Department</th>
              <th>Procure Flag</th>
              <th>Receive Flag</th>
              <th>Status</th>
              <th>Actions</th>
            </tr>
          </thead>
          <tbody>
            ${users.map(u => `
              <tr>
                <td><strong>${escapeHtml(u.full_name)}</strong></td>
                <td>${escapeHtml(u.email)}</td>
                <td><span style="font-weight:600; text-transform:capitalize;">${escapeHtml(u.role)}</span></td>
                <td>${escapeHtml(u.department_name || '—')}</td>
                <td>${u.can_procure ? '✔ Yes' : '—'}</td>
                <td>${u.can_receive ? '✔ Yes' : '—'}</td>
                <td>${renderStatusBadge(u.status)}</td>
                <td>
                  ${u.status === 'active' ? `
                    <button class="btn btn-danger btn-sm" onclick="deactivateUserAction(${jsArg(u.id)}, ${jsArg(u.full_name)})">Deactivate</button>
                  ` : `
                    <button class="btn btn-primary btn-sm" onclick="reactivateUserAction(${jsArg(u.id)})">Reactivate</button>
                  `}
                </td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// 11. SETTINGS
async function renderSettings(container) {
  const settings = await apiRequest("/api/v1/settings");
  const depts = await apiRequest("/api/v1/departments");

  container.innerHTML = `
    <!-- Top Row: My Profile & Password Change -->
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px; margin-bottom:20px;">
      <div class="card">
        <h3 style="margin-bottom:14px;">👤 My Profile Details</h3>
        <form id="profile-form">
          <div class="form-group">
            <label class="form-label">Full Name</label>
            <input type="text" id="my_full_name" class="form-input" value="${escapeHtml(currentUser.full_name || '')}" placeholder="Your Full Name" required>
          </div>
          <div class="form-group">
            <label class="form-label">Work Email</label>
            <input type="email" class="form-input" value="${escapeHtml(currentUser.email || '')}" disabled style="background:#f1f5f9; cursor:not-allowed;">
            <small style="color:#64748b;">Primary system login email.</small>
          </div>
          <div class="form-group">
            <label class="form-label">Phone Number</label>
            <input type="text" id="my_phone" class="form-input" placeholder="+91 98200 00000" value="${escapeHtml(currentUser.phone || '')}">
          </div>
          <div class="form-group">
            <label class="form-label">Assigned Department / Role</label>
            <input type="text" class="form-input" value="${escapeHtml(currentUser.department_name || 'Administration')} — Manager" disabled style="background:#f1f5f9; cursor:not-allowed;">
          </div>
          <button type="submit" class="btn btn-primary">Save Profile Details</button>
        </form>
      </div>

      <div class="card">
        <h3 style="margin-bottom:14px;">🔒 Change Account Password</h3>
        <form id="password-form">
          <div class="form-group">
            <label class="form-label">Current Password</label>
            <input type="password" id="current_password" class="form-input" placeholder="••••••••" autocomplete="current-password" required>
            <small style="color:#64748b;">Enter your current initial/bootstrap password.</small>
          </div>
          <div class="form-group">
            <label class="form-label">New Password</label>
            <input type="password" id="new_password" class="form-input" placeholder="At least 8 characters" autocomplete="new-password" minlength="8" required>
          </div>
          <div class="form-group">
            <label class="form-label">Confirm New Password</label>
            <input type="password" id="confirm_password" class="form-input" placeholder="Confirm new password" autocomplete="new-password" minlength="8" required>
          </div>
          <button type="submit" class="btn btn-primary">Update Password</button>
        </form>
      </div>
    </div>

    <!-- Bottom Row: Company Settings & Departments -->
    <div style="display:grid; grid-template-columns:1fr 1fr; gap:20px;">
      <div class="card">
        <h3 style="margin-bottom:14px;">⚙️ Procurement Rules & Company Details</h3>
        <form id="settings-form">
          <div class="form-group">
            <label class="form-label">GRN Over-receipt Tolerance (%)</label>
            <input type="number" id="grn_tolerance_percent" class="form-input" value="${escapeHtml(settings.grn_tolerance_percent || 0.0)}" step="0.1" min="0" max="100">
            <small style="color:#64748b;">Default 0.0%: Strictly prevents receiving more than PO quantity.</small>
          </div>
          <div class="form-group">
            <label class="form-label">Company Name on Documents</label>
            <input type="text" id="company_name" class="form-input" value="${escapeHtml(settings.company_name || 'Industrial Bolt Centre')}">
          </div>
          <div class="form-group">
            <label class="form-label">Company GSTIN</label>
            <input type="text" id="company_gstin" class="form-input" value="${escapeHtml(settings.company_gstin || '27AAACI9999M1Z5')}">
          </div>
          <button type="submit" class="btn btn-secondary">Save Company Settings</button>
        </form>
      </div>

      <div class="card">
        <h3 style="margin-bottom:14px;">🏢 Active Departments</h3>
        <div style="display:flex; flex-direction:column; gap:8px;">
          ${depts.map(d => `
            <div style="display:flex; justify-content:space-between; padding:8px 12px; background:#f8fafc; border-radius:6px; border:1px solid #e2e8f0;">
              <strong>${escapeHtml(d.name)}</strong>
              <code>${escapeHtml(d.code)}</code>
            </div>
          `).join("")}
        </div>
      </div>
    </div>
  `;

  document.getElementById("profile-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const full_name = document.getElementById("my_full_name").value.trim();
    const phone = document.getElementById("my_phone").value.trim();
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

  document.getElementById("password-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const current_password = document.getElementById("current_password").value;
    const new_password = document.getElementById("new_password").value;
    const confirm_password = document.getElementById("confirm_password").value;

    if (new_password !== confirm_password) {
      showToast("New passwords do not match", "error");
      return;
    }
    try {
      await apiRequest("/api/v1/auth/change-password", {
        method: "POST",
        body: JSON.stringify({ current_password, new_password })
      });
      document.getElementById("password-form").reset();
      showToast("Password changed successfully", "success");
    } catch (_) {}
  });

  document.getElementById("settings-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const payload = {
      grn_tolerance_percent: document.getElementById("grn_tolerance_percent").value,
      company_name: document.getElementById("company_name").value,
      company_gstin: document.getElementById("company_gstin").value,
    };
    await apiRequest("/api/v1/settings", {
      method: "POST",
      body: JSON.stringify(payload)
    });
    showToast("Settings updated successfully", "success");
  });
}

// 12. AUDIT TRAIL
async function renderAudit(container) {
  const logs = await apiRequest("/api/v1/audit?limit=100");

  container.innerHTML = `
    <div class="card">
      <div class="table-responsive">
        <table class="table">
          <thead>
            <tr>
              <th>Timestamp</th>
              <th>Actor</th>
              <th>Action</th>
              <th>Entity</th>
              <th>Entity ID</th>
              <th>IP</th>
            </tr>
          </thead>
          <tbody>
            ${logs.map(l => `
              <tr>
                <td>${formatDateTime(l.occurred_at)}</td>
                <td><strong>${escapeHtml(l.actor_email || 'System')}</strong></td>
                <td><code>${escapeHtml(l.action)}</code></td>
                <td>${escapeHtml(l.entity_type)}</td>
                <td><code>${escapeHtml(l.entity_id)}</code></td>
                <td>${escapeHtml(l.ip || '—')}</td>
              </tr>
            `).join("")}
          </tbody>
        </table>
      </div>
    </div>
  `;
}

// --- Modals and Actions ---

window.viewPRDetail = async function(prId) {
  const pr = await apiRequest(`/api/v1/requisitions/${prId}`);
  openModal(`Requisition ${pr.number}`, `
    <div style="margin-bottom:16px;">
      <div style="display:flex; justify-content:space-between;">
        <div><strong>Department:</strong> ${escapeHtml(pr.department_name)}</div>
        <div><strong>Status:</strong> ${renderStatusBadge(pr.status)}</div>
      </div>
      <div style="margin-top:6px;"><strong>Requester:</strong> ${escapeHtml(pr.requester_name)}</div>
      <div style="margin-top:6px;"><strong>Required By:</strong> ${formatDate(pr.required_by)}</div>
      <div style="margin-top:6px;"><strong>Purpose:</strong> ${escapeHtml(pr.purpose)}</div>
      ${pr.decision_note ? `<div style="margin-top:6px; color:#b91c1c;"><strong>Decision Note:</strong> ${escapeHtml(pr.decision_note)}</div>` : ''}
    </div>

    <h4>Line Items</h4>
    <div class="table-responsive" style="margin-top:8px;">
      <table class="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Part #</th>
            <th>Qty</th>
            <th>Unit</th>
            <th>Procure Status</th>
          </tr>
        </thead>
        <tbody>
          ${pr.lines.map(l => `
            <tr>
              <td><strong>${escapeHtml(l.item_name_snapshot)}</strong></td>
              <td>${escapeHtml(l.part_number || '—')}</td>
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

window.approvePRModal = function(prId, prNumber) {
  openModal(`Approve PR ${prNumber}`, `
    <p>Are you sure you want to approve this purchase requisition?</p>
    <div class="form-group" style="margin-top:14px;">
      <label class="form-label">Approval Note (Optional)</label>
      <input type="text" id="pr-approve-note" class="form-input" placeholder="Approved for RFQ / procurement">
    </div>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="confirmApprovePR('${prId}')">Approve PR</button>
    </div>
  `);
};

window.confirmApprovePR = async function(prId) {
  const note = document.getElementById("pr-approve-note").value;
  try {
    await apiRequest(`/api/v1/requisitions/${prId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
    showToast("Requisition approved!", "success");
    closeModal();
    renderCurrentPage();
  } catch (err) {
    // Toast handled
  }
};

window.rejectPRModal = function(prId, prNumber) {
  openModal(`Reject PR ${prNumber}`, `
    <p>Please provide a mandatory reason for rejecting this requisition:</p>
    <div class="form-group" style="margin-top:14px;">
      <label class="form-label">Rejection Reason *</label>
      <textarea id="pr-reject-note" class="form-textarea" rows="3" required placeholder="Specify why this requisition cannot be approved..."></textarea>
    </div>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-danger" onclick="confirmRejectPR('${prId}')">Reject PR</button>
    </div>
  `);
};

window.confirmRejectPR = async function(prId) {
  const note = document.getElementById("pr-reject-note").value;
  if (!note.trim()) {
    showToast("Rejection reason is required", "error");
    return;
  }
  try {
    await apiRequest(`/api/v1/requisitions/${prId}/reject`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
    showToast("Requisition rejected", "info");
    closeModal();
    renderCurrentPage();
  } catch (err) {
    // Toast handled
  }
};

// CREATE PO MODAL
window.openCreatePOModal = async function() {
  const vendors = await apiRequest("/api/v1/vendors");
  const items = await apiRequest("/api/v1/items");

  openModal("Create Purchase Order", `
    <form id="create-po-form">
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Select Vendor *</label>
          <select id="po-vendor" class="form-select" required>
            <option value="">-- Choose Vendor --</option>
            ${vendors.map(v => `<option value="${escapeHtml(v.id)}">${escapeHtml(v.name)} (${escapeHtml(v.gstin || 'No GSTIN')})</option>`).join("")}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Expected Delivery Date</label>
          <input type="date" id="po-delivery-date" class="form-input">
        </div>
      </div>

      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Payment Terms</label>
          <input type="text" id="po-payment-terms" class="form-input" value="30 Days Net">
        </div>
        <div class="form-group">
          <label class="form-label">Delivery Terms</label>
          <input type="text" id="po-delivery-terms" class="form-input" value="Door Delivery to IBC Plant">
        </div>
      </div>

      <h4 style="margin: 16px 0 8px;">Order Line Items</h4>
      <div id="po-lines-container"></div>
      <button type="button" class="btn btn-secondary btn-sm" onclick="addPOLineRow()" style="margin-bottom:16px;">+ Add Line</button>

      <div style="background:#f8fafc; border:1px solid #cbd5e1; border-radius:6px; padding:12px; margin-bottom:16px;">
        <div style="display:flex; justify-content:space-between; font-weight:700; font-size:15px; color:var(--color-primary);">
          <span>Live Calculated Grand Total:</span>
          <span id="po-grand-total-preview">₹0.00</span>
        </div>
      </div>

      <div style="display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Save Draft PO</button>
      </div>
    </form>
  `);

  window.poItemsMaster = items;
  addPOLineRow();

  document.getElementById("create-po-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const vendor_id = document.getElementById("po-vendor").value;
    const delivery_date = document.getElementById("po-delivery-date").value;
    const payment_terms = document.getElementById("po-payment-terms").value;
    const delivery_terms = document.getElementById("po-delivery-terms").value;

    const lineRows = document.querySelectorAll(".po-line-row");
    const lines = [];
    lineRows.forEach(row => {
      const item_id = row.querySelector(".po-line-item").value;
      const item_name = row.querySelector(".po-line-name").value;
      const part_number = row.querySelector(".po-line-part").value;
      const quantity = parseFloat(row.querySelector(".po-line-qty").value) || 0;
      const unit = row.querySelector(".po-line-unit").value;
      const rate = parseFloat(row.querySelector(".po-line-rate").value) || 0;
      const tax_percent = parseFloat(row.querySelector(".po-line-tax").value) || 18.0;

      if (item_name && quantity > 0) {
        lines.push({ item_id, item_name, part_number, quantity, unit, rate, tax_percent });
      }
    });

    if (lines.length === 0) {
      showToast("Please enter at least one valid line item", "error");
      return;
    }

    try {
      const res = await apiRequest("/api/v1/purchase-orders", {
        method: "POST",
        body: JSON.stringify({
          vendor_id, delivery_date, payment_terms, delivery_terms, lines
        })
      });
      showToast(`Purchase Order ${res.number} created!`, "success");
      closeModal();
      renderCurrentPage();
    } catch (err) {
      // Toast handled
    }
  });
};

window.addPOLineRow = function() {
  const container = document.getElementById("po-lines-container");
  const rowId = "pol-row-" + Date.now();
  const div = document.createElement("div");
  div.className = "po-line-row";
  div.id = rowId;
  div.style.cssText = "display:grid; grid-template-columns: 2fr 1fr 1fr 1fr 1fr auto; gap:8px; align-items:end; margin-bottom:8px;";

  div.innerHTML = `
    <div>
      <label class="form-label" style="font-size:10px;">Item / Description</label>
      <select class="form-select po-line-item" onchange="onPOLineItemSelect(this, '${rowId}')">
        <option value="">-- Custom / Select Item --</option>
        ${(window.poItemsMaster || []).map(i => `<option value="${escapeHtml(i.id)}" data-name="${escapeHtml(i.name)}" data-part="${escapeHtml(i.part_number||'')}" data-unit="${escapeHtml(i.unit)}" data-rate="${i.default_rate}" data-tax="${i.default_tax_percent}">${escapeHtml(i.name)}</option>`).join("")}
      </select>
      <input type="text" class="form-input po-line-name" placeholder="Item name snapshot" style="margin-top:4px;" required>
      <input type="hidden" class="po-line-part" value="">
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Qty</label>
      <input type="number" class="form-input po-line-qty" value="100" min="1" oninput="recalcPOTotals()" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Unit</label>
      <input type="text" class="form-input po-line-unit" value="Nos" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">Rate (₹)</label>
      <input type="number" class="form-input po-line-rate" value="28.50" step="0.01" oninput="recalcPOTotals()" required>
    </div>
    <div>
      <label class="form-label" style="font-size:10px;">GST %</label>
      <input type="number" class="form-input po-line-tax" value="18" oninput="recalcPOTotals()" required>
    </div>
    <div>
      <button type="button" class="btn btn-danger btn-sm" onclick="document.getElementById('${rowId}').remove(); recalcPOTotals();">×</button>
    </div>
  `;
  container.appendChild(div);
  recalcPOTotals();
};

window.onPOLineItemSelect = function(selectEl, rowId) {
  const row = document.getElementById(rowId);
  const selected = selectEl.options[selectEl.selectedIndex];
  if (selected.value) {
    row.querySelector(".po-line-name").value = selected.dataset.name;
    row.querySelector(".po-line-part").value = selected.dataset.part;
    row.querySelector(".po-line-unit").value = selected.dataset.unit;
    row.querySelector(".po-line-rate").value = selected.dataset.rate;
    row.querySelector(".po-line-tax").value = selected.dataset.tax;
  }
  recalcPOTotals();
};

window.recalcPOTotals = function() {
  let subtotal = 0;
  let tax = 0;
  document.querySelectorAll(".po-line-row").forEach(row => {
    const qty = parseFloat(row.querySelector(".po-line-qty")?.value) || 0;
    const rate = parseFloat(row.querySelector(".po-line-rate")?.value) || 0;
    const taxPct = parseFloat(row.querySelector(".po-line-tax")?.value) || 0;
    const lineSub = qty * rate;
    subtotal += lineSub;
    tax += (lineSub * (taxPct / 100));
  });
  const grandTotal = subtotal + tax;
  const preview = document.getElementById("po-grand-total-preview");
  if (preview) preview.textContent = formatCurrency(grandTotal);
};

window.submitPOAction = async function(poId) {
  try {
    await apiRequest(`/api/v1/purchase-orders/${poId}/submit`, { method: "POST" });
    showToast("PO submitted for approval!", "success");
    renderCurrentPage();
  } catch (err) {}
};

window.markSentPOAction = async function(poId) {
  try {
    await apiRequest(`/api/v1/purchase-orders/${poId}/send`, { method: "POST" });
    showToast("PO marked as sent to vendor", "success");
    renderCurrentPage();
  } catch (err) {}
};

window.approvePOModal = function(poId, poNumber) {
  openModal(`Approve PO ${poNumber}`, `
    <p>Are you sure you want to authorize this Purchase Order?</p>
    <div class="form-group" style="margin-top:14px;">
      <label class="form-label">Approval Note</label>
      <input type="text" id="po-approve-note" class="form-input" placeholder="Approved for procurement">
    </div>
    <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:16px;">
      <button class="btn btn-secondary" onclick="closeModal()">Cancel</button>
      <button class="btn btn-primary" onclick="confirmApprovePO('${poId}')">Approve PO</button>
    </div>
  `);
};

window.confirmApprovePO = async function(poId) {
  const note = document.getElementById("po-approve-note").value;
  try {
    await apiRequest(`/api/v1/purchase-orders/${poId}/approve`, {
      method: "POST",
      body: JSON.stringify({ note })
    });
    showToast("PO Approved!", "success");
    closeModal();
    renderCurrentPage();
  } catch (err) {}
};

// CREATE GRN MODAL
window.openCreateGRNModal = async function() {
  const pos = await apiRequest("/api/v1/purchase-orders");
  const eligiblePOs = pos.filter(p => ["approved", "sent", "part_received"].includes(p.status));

  openModal("Post Goods Receipt Note (GRN)", `
    <form id="create-grn-form">
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Select Approved Purchase Order *</label>
          <select id="grn-po-id" class="form-select" onchange="onGRNPOSelected(this.value)" required>
            <option value="">-- Choose PO --</option>
            ${eligiblePOs.map(p => `<option value="${escapeHtml(p.id)}">${escapeHtml(p.number)} — ${escapeHtml(p.vendor_name)} (${escapeHtml(p.status)})</option>`).join("")}
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Receipt Date *</label>
          <input type="date" id="grn-date" class="form-input" value="${new Date().toISOString().split('T')[0]}" required>
        </div>
      </div>

      <div class="form-grid-3">
        <div class="form-group">
          <label class="form-label">Transporter</label>
          <input type="text" id="grn-transporter" class="form-input" placeholder="V-Trans / SafeXpress">
        </div>
        <div class="form-group">
          <label class="form-label">LR / Docket No</label>
          <input type="text" id="grn-lr" class="form-input" placeholder="LR-88992">
        </div>
        <div class="form-group">
          <label class="form-label">Vehicle No</label>
          <input type="text" id="grn-vehicle" class="form-input" placeholder="MH-04-AB-1234">
        </div>
      </div>

      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Supplier Invoice No</label>
          <input type="text" id="grn-inv-no" class="form-input" placeholder="INV-2026-091">
        </div>
        <div class="form-group">
          <label class="form-label">Supplier Invoice Date</label>
          <input type="date" id="grn-inv-date" class="form-input">
        </div>
      </div>

      <h4 style="margin: 14px 0 8px;">Received Quantities Check</h4>
      <div id="grn-lines-container">
        <p style="color:#64748b; font-size:12px;">Select a purchase order above to inspect lines.</p>
      </div>

      <div style="display:flex; justify-content:flex-end; gap:8px; margin-top:20px;">
        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Post GRN</button>
      </div>
    </form>
  `);

  document.getElementById("create-grn-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const purchase_order_id = document.getElementById("grn-po-id").value;
    const received_date = document.getElementById("grn-date").value;
    const transporter = document.getElementById("grn-transporter").value;
    const lr_number = document.getElementById("grn-lr").value;
    const vehicle_no = document.getElementById("grn-vehicle").value;
    const supplier_invoice_no = document.getElementById("grn-inv-no").value;
    const supplier_invoice_date = document.getElementById("grn-inv-date").value;

    const lineRows = document.querySelectorAll(".grn-line-row");
    const lines = [];
    for (const r of lineRows) {
      const po_line_id = r.dataset.poLineId;
      const received_qty = parseFloat(r.querySelector(".grn-rec-qty").value) || 0;
      const accepted_qty = parseFloat(r.querySelector(".grn-acc-qty").value) || 0;
      const rejected_qty = parseFloat(r.querySelector(".grn-rej-qty").value) || 0;
      const rejection_reason = r.querySelector(".grn-rej-reason")?.value || "";

      if (received_qty > 0) {
        if (accepted_qty + rejected_qty !== received_qty) {
          showToast(`Line error: Accepted (${accepted_qty}) + Rejected (${rejected_qty}) must equal Received (${received_qty})`, "error");
          return;
        }
        lines.push({ po_line_id, received_qty, accepted_qty, rejected_qty, rejection_reason });
      }
    }

    if (lines.length === 0) {
      showToast("Please enter received quantities for at least one item", "error");
      return;
    }

    try {
      const res = await apiRequest("/api/v1/grns", {
        method: "POST",
        body: JSON.stringify({
          purchase_order_id, received_date, transporter, lr_number, vehicle_no,
          supplier_invoice_no, supplier_invoice_date, lines
        })
      });
      showToast(`GRN ${res.number} posted! PO status is now ${res.po_status}`, "success");
      closeModal();
      renderCurrentPage();
    } catch (err) {}
  });
};

window.onGRNPOSelected = async function(poId) {
  if (!poId) return;
  const po = await apiRequest(`/api/v1/purchase-orders/${poId}`);
  const container = document.getElementById("grn-lines-container");

  container.innerHTML = `
    <div class="table-responsive">
      <table class="table">
        <thead>
          <tr>
            <th>Item</th>
            <th>Ordered</th>
            <th>Prev Recvd</th>
            <th>Received Qty</th>
            <th>Accepted Qty</th>
            <th>Rejected Qty</th>
          </tr>
        </thead>
        <tbody>
          ${po.lines.map(l => `
            <tr class="grn-line-row" data-po-line-id="${l.id}">
              <td><strong>${escapeHtml(l.item_name_snapshot)}</strong></td>
              <td>${l.quantity} ${escapeHtml(l.unit)}</td>
              <td>${l.received_qty} ${escapeHtml(l.unit)}</td>
              <td>
                <input type="number" class="form-input grn-rec-qty" style="width:90px;" value="${Math.max(0, l.quantity - l.received_qty)}" min="0" step="0.1" oninput="autoBalanceGRN(this)">
              </td>
              <td>
                <input type="number" class="form-input grn-acc-qty" style="width:90px;" value="${Math.max(0, l.quantity - l.received_qty)}" min="0" step="0.1">
              </td>
              <td>
                <input type="number" class="form-input grn-rej-qty" style="width:90px;" value="0" min="0" step="0.1">
              </td>
            </tr>
          `).join("")}
        </tbody>
      </table>
    </div>
  `;
};

window.autoBalanceGRN = function(inputEl) {
  const row = inputEl.closest(".grn-line-row");
  const rec = parseFloat(inputEl.value) || 0;
  row.querySelector(".grn-acc-qty").value = rec;
  row.querySelector(".grn-rej-qty").value = 0;
};

// ADD ITEM MODAL
window.openAddItemModal = function() {
  openModal("Add Master Item", `
    <form id="add-item-form">
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">SKU Code *</label>
          <input type="text" id="item-sku" class="form-input" placeholder="e.g. HB-1680" required>
        </div>
        <div class="form-group">
          <label class="form-label">Item Name *</label>
          <input type="text" id="item-name" class="form-input" placeholder="Hex Bolt M16 x 80 Grade 8.8" required>
        </div>
      </div>
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Part Number</label>
          <input type="text" id="item-part" class="form-input" placeholder="HB-1680">
        </div>
        <div class="form-group">
          <label class="form-label">HSN Code</label>
          <input type="text" id="item-hsn" class="form-input" value="7318">
        </div>
      </div>
      <div class="form-grid-3">
        <div class="form-group">
          <label class="form-label">Unit *</label>
          <input type="text" id="item-unit" class="form-input" value="Nos" required>
        </div>
        <div class="form-group">
          <label class="form-label">Default Rate (₹)</label>
          <input type="number" id="item-rate" class="form-input" value="35.00" step="0.01">
        </div>
        <div class="form-group">
          <label class="form-label">Tax %</label>
          <input type="number" id="item-tax" class="form-input" value="18">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Description</label>
        <textarea id="item-desc" class="form-textarea" rows="2" placeholder="Technical specifications, grade, plating..."></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Save Item</button>
      </div>
    </form>
  `);

  document.getElementById("add-item-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const sku = document.getElementById("item-sku").value;
    const name = document.getElementById("item-name").value;
    const part_number = document.getElementById("item-part").value;
    const hsn_code = document.getElementById("item-hsn").value;
    const unit = document.getElementById("item-unit").value;
    const default_rate = parseFloat(document.getElementById("item-rate").value) || 0;
    const default_tax_percent = parseFloat(document.getElementById("item-tax").value) || 18;
    const description = document.getElementById("item-desc").value;

    try {
      await apiRequest("/api/v1/items", {
        method: "POST",
        body: JSON.stringify({ sku, name, part_number, hsn_code, unit, default_rate, default_tax_percent, description })
      });
      showToast("Item created successfully", "success");
      closeModal();
      renderCurrentPage();
    } catch (err) {}
  });
};

// ADD VENDOR MODAL
window.openAddVendorModal = function() {
  openModal("Add Master Vendor", `
    <form id="add-vendor-form">
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Vendor Business Name *</label>
          <input type="text" id="v-name" class="form-input" placeholder="e.g. Mahavir Fasteners Pvt Ltd" required>
        </div>
        <div class="form-group">
          <label class="form-label">Contact Person</label>
          <input type="text" id="v-contact" class="form-input" placeholder="Mr. Ramesh Jain">
        </div>
      </div>
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Email</label>
          <input type="email" id="v-email" class="form-input" placeholder="sales@mahavir.in">
        </div>
        <div class="form-group">
          <label class="form-label">Phone / WhatsApp</label>
          <input type="text" id="v-phone" class="form-input" placeholder="+91 98200 11223">
        </div>
      </div>
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">GSTIN</label>
          <input type="text" id="v-gstin" class="form-input" placeholder="27AAACM1234F1Z1">
        </div>
        <div class="form-group">
          <label class="form-label">Payment Terms</label>
          <input type="text" id="v-terms" class="form-input" value="30 Days Net">
        </div>
      </div>
      <div class="form-group">
        <label class="form-label">Plant / Office Address</label>
        <textarea id="v-addr" class="form-textarea" rows="2" placeholder="Factory Address"></textarea>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Save Vendor</button>
      </div>
    </form>
  `);

  document.getElementById("add-vendor-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const name = document.getElementById("v-name").value;
    const contact_person = document.getElementById("v-contact").value;
    const email = document.getElementById("v-email").value;
    const phone = document.getElementById("v-phone").value;
    const gstin = document.getElementById("v-gstin").value;
    const default_payment_terms = document.getElementById("v-terms").value;
    const address = document.getElementById("v-addr").value;

    try {
      await apiRequest("/api/v1/vendors", {
        method: "POST",
        body: JSON.stringify({ name, contact_person, email, phone, gstin, default_payment_terms, address })
      });
      showToast("Vendor created successfully", "success");
      closeModal();
      renderCurrentPage();
    } catch (err) {}
  });
};

// INVITE USER MODAL
window.openInviteUserModal = async function() {
  const depts = await apiRequest("/api/v1/departments");
  openModal("Invite / Add User", `
    <form id="invite-user-form">
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Full Name *</label>
          <input type="text" id="u-name" class="form-input" required>
        </div>
        <div class="form-group">
          <label class="form-label">Work Email *</label>
          <input type="email" id="u-email" class="form-input" required>
        </div>
      </div>
      <div class="form-grid-2">
        <div class="form-group">
          <label class="form-label">Role *</label>
          <select id="u-role" class="form-select">
            <option value="employee">Employee</option>
            <option value="manager">Manager</option>
          </select>
        </div>
        <div class="form-group">
          <label class="form-label">Department</label>
          <select id="u-dept" class="form-select">
            ${depts.map(d => `<option value="${escapeHtml(d.id)}">${escapeHtml(d.name)}</option>`).join("")}
          </select>
        </div>
      </div>
      <div style="background:#f8fafc; padding:12px; border-radius:6px; margin-bottom:16px;">
        <div style="font-weight:600; font-size:12px; margin-bottom:6px;">Per-User Capability Flags (§2.2):</div>
        <label style="display:flex; align-items:center; gap:8px; font-size:12px; margin-bottom:4px;">
          <input type="checkbox" id="u-can-procure"> <strong>can_procure</strong> (Enquiries, PO create/submit, Vendors)
        </label>
        <label style="display:flex; align-items:center; gap:8px; font-size:12px;">
          <input type="checkbox" id="u-can-receive"> <strong>can_receive</strong> (GRN entry & material checking)
        </label>
      </div>
      <div style="display:flex; justify-content:flex-end; gap:8px;">
        <button type="button" class="btn btn-secondary" onclick="closeModal()">Cancel</button>
        <button type="submit" class="btn btn-primary">Add User</button>
      </div>
    </form>
  `);

  document.getElementById("invite-user-form").addEventListener("submit", async (e) => {
    e.preventDefault();
    const full_name = document.getElementById("u-name").value;
    const email = document.getElementById("u-email").value;
    const role = document.getElementById("u-role").value;
    const department_id = document.getElementById("u-dept").value;
    const can_procure = document.getElementById("u-can-procure").checked;
    const can_receive = document.getElementById("u-can-receive").checked;

    try {
      const result = await apiRequest("/api/v1/users", {
        method: "POST",
        body: JSON.stringify({ full_name, email, role, department_id, can_procure, can_receive })
      });
      const inviteUrl = `${location.origin}/accept-invitation#token=${encodeURIComponent(result.invite_token)}`;
      window.prompt("Copy this one-time invitation link and share it securely (expires in 24 hours):", inviteUrl);
      showToast("Invitation created. The setup link is shown in the copy dialog.", "success");
      closeModal();
      renderCurrentPage();
    } catch (err) {}
  });
};

window.deactivateUserAction = async function(userId, name) {
  if (confirm(`Are you sure you want to deactivate ${name}? They will immediately lose access.`)) {
    try {
      await apiRequest(`/api/v1/users/${userId}/deactivate`, { method: "POST" });
      showToast("User deactivated and sessions revoked", "info");
      renderCurrentPage();
    } catch (err) {}
  }
};
