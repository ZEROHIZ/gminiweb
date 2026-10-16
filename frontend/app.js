/**
 * frontend/app.js
 * 
 * 核心职责：
 * Gemini 多账号管理与代理网关系统的前端业务逻辑驱动。
 * 负责四大 Tab 状态切换、管理认证生命周期、账号增删改查与实时健康检测、
 * 系统动态配置保存、高保真请求日志表格与详情弹窗（严格对照参考图 1/2/3）、
 * 网页历史会话审查以及控制台在线接口测试。
 */

// ==================== 全局状态 ====================
const state = {
  activeTab: 'tab-accounts',
  accounts: [],
  settings: {},
  logs: {
    page: 1,
    pageSize: 10,
    total: 0,
    items: [],
    operation: '',
    status: ''
  },
  authRequired: false,
  panelToken: localStorage.getItem('gemini_panel_token') || ''
};

// ==================== API 请求封装 ====================
async function apiRequest(endpoint, options = {}) {
  const headers = {
    'Content-Type': 'application/json',
    ...(options.headers || {})
  };

  if (state.panelToken) {
    headers['Authorization'] = `Bearer ${state.panelToken}`;
  }

  const config = {
    ...options,
    headers
  };

  try {
    const res = await fetch(endpoint, config);
    if (res.status === 401) {
      // 鉴权失效或未登录，展示登录弹窗
      showLoginModal();
      throw new Error('未授权访问，请输入面板密码');
    }
    if (!res.ok) {
      let errDetail = '请求失败';
      try {
        const errJson = await res.json();
        errDetail = errJson.detail?.error?.message || errJson.detail || JSON.stringify(errJson);
      } catch (e) {
        errDetail = `HTTP ${res.status} ${res.statusText}`;
      }
      throw new Error(errDetail);
    }
    return await res.json();
  } catch (err) {
    throw err;
  }
}

// ==================== Toast 轻量通知 ====================
function showToast(message, type = 'info') {
  const container = document.getElementById('toast-container');
  if (!container) return;

  const toast = document.createElement('div');
  toast.className = `toast ${type}`;
  toast.innerHTML = `
    <span>${type === 'success' ? '✅' : type === 'error' ? '❌' : 'ℹ️'}</span>
    <span>${escapeHtml(message)}</span>
  `;
  container.appendChild(toast);

  setTimeout(() => {
    toast.style.opacity = '0';
    toast.style.transform = 'translateY(10px)';
    setTimeout(() => toast.remove(), 250);
  }, 3500);
}

function escapeHtml(str) {
  if (!str) return '';
  return String(str)
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;')
    .replace(/'/g, '&#039;');
}

// ==================== 1. 认证流与初始化 ====================
async function checkAuthStatus() {
  try {
    const res = await apiRequest('/api/auth/status');
    state.authRequired = res.auth_required;
    const dot = document.getElementById('auth-status-dot');
    const text = document.getElementById('auth-status-text');

    if (!state.authRequired) {
      dot.className = 'status-dot green';
      text.innerText = '免密模式';
      closeLoginModal();
    } else if (state.panelToken) {
      dot.className = 'status-dot green';
      text.innerText = '已认证';
    } else {
      dot.className = 'status-dot yellow';
      text.innerText = '需登录';
      showLoginModal();
    }
  } catch (err) {
    console.error('检查认证状态失败:', err);
  }
}

function showLoginModal() {
  document.getElementById('modal-login').style.display = 'flex';
}

function closeLoginModal() {
  document.getElementById('modal-login').style.display = 'none';
}

// ==================== 2. Tab 切换逻辑 ====================
function initTabs() {
  const tabBtns = document.querySelectorAll('.tab-btn');
  tabBtns.forEach(btn => {
    btn.addEventListener('click', () => {
      const targetTab = btn.getAttribute('data-tab');
      switchTab(targetTab);
    });
  });
}

function switchTab(tabId) {
  state.activeTab = tabId;
  document.querySelectorAll('.tab-btn').forEach(b => {
    b.classList.toggle('active', b.getAttribute('data-tab') === tabId);
  });
  document.querySelectorAll('.tab-pane').forEach(p => {
    p.classList.toggle('active', p.id === tabId);
  });

  // 切换加载对应数据
  if (tabId === 'tab-accounts') loadAccounts();
  else if (tabId === 'tab-settings') loadSettings();
  else if (tabId === 'tab-logs') loadLogs();
  else if (tabId === 'tab-images') loadImages();
  else if (tabId === 'tab-test') populateTestAccounts();
}

// ==================== 3. Tab 1: Token 账号管理 ====================
async function loadAccounts() {
  try {
    const accounts = await apiRequest('/api/accounts');
    state.accounts = accounts;
    renderAccounts();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderAccounts() {
  const grid = document.getElementById('accounts-grid');
  grid.innerHTML = '';

  const total = state.accounts.length;
  const active = state.accounts.filter(a => a.is_enabled && a.status === 'ACTIVE').length;
  const expired = state.accounts.filter(a => !a.is_enabled || a.status === 'EXPIRED').length;

  document.getElementById('stat-total-accounts').innerText = total;
  document.getElementById('stat-active-accounts').innerText = active;
  document.getElementById('stat-expired-accounts').innerText = expired;

  if (total === 0) {
    grid.innerHTML = `
      <div class="empty-placeholder" style="grid-column: 1 / -1;">
        <p>暂无配置任何 Gemini 账号凭据</p>
        <button class="btn btn-primary" style="margin-top: 12px;" onclick="openAccountModal()">➕ 立即添加第一个账号</button>
      </div>
    `;
    return;
  }

  state.accounts.forEach(acc => {
    const isOk = acc.is_enabled && acc.status === 'ACTIVE';
    const statusBadgeClass = isOk ? 'badge-success' : (acc.status === 'EXPIRED' ? 'badge-danger' : 'badge-warning');
    const statusText = !acc.is_enabled ? '已禁用' : (acc.status === 'ACTIVE' ? '正常' : (acc.status === 'EXPIRED' ? '失效' : '受限'));
    const dotClass = isOk ? 'green' : (acc.status === 'EXPIRED' ? 'red' : 'yellow');

    const quota = acc.quota_info || {};
    const flashInfo = quota.flash || { status: '正常', remaining: '100%' };
    const proInfo = quota.pro || { status: '正常', remaining: '100%' };

    const card = document.createElement('div');
    card.className = 'account-card';
    card.id = `account-card-${acc.id}`;
    card.innerHTML = `
      <div class="acc-card-header">
        <div class="acc-card-email">
          <span class="status-dot ${dotClass}"></span>
          <span>${escapeHtml(acc.email)}</span>
        </div>
        <div style="display:flex; align-items:center; gap:6px;">
          <span class="badge-concurrency ${acc.active_connections >= (acc.effective_concurrency || 2) ? 'busy' : ''}" title="当前活动请求数 / 最大并发上限">
            ⚡ 并发: ${acc.active_connections || 0}/${acc.effective_concurrency || 2}${acc.concurrency_limit ? ' (独立)' : ''}
          </span>
          <span class="badge ${statusBadgeClass}">[${statusText}]</span>
        </div>
      </div>

      <div class="acc-card-body">
        <div class="acc-meta-item">
          <strong>凭据:</strong>
          <code>${escapeHtml(acc.secure_1psid_masked || '未设置')}</code>
          <span style="font-size:11px; color:#10b981;">(已脱敏)</span>
        </div>
        <div class="acc-meta-item">
          <strong>备注:</strong>
          <span>${escapeHtml(acc.remark || '无备注')}</span>
        </div>
        ${acc.proxy ? `
          <div class="acc-meta-item">
            <strong>独立代理:</strong>
            <code>${escapeHtml(acc.proxy)}</code>
          </div>
        ` : ''}

        <div class="acc-quotas-box">
          <div class="acc-quotas-title">📊 动态配额 (Quotas 自动解析)</div>
          <div class="acc-quota-row">
            <span>Flash 配额:</span>
            <span class="badge ${flashInfo.status === '充裕' || flashInfo.status === '正常' ? 'badge-success' : 'badge-warning'}">
              ${escapeHtml(flashInfo.remaining || '100%')} (${escapeHtml(flashInfo.status)})
            </span>
          </div>
          <div class="acc-quota-row">
            <span>Pro 配额:</span>
            <span class="badge ${proInfo.status === '充裕' || proInfo.status === '正常' ? 'badge-success' : 'badge-warning'}">
              ${escapeHtml(proInfo.remaining || '100%')} (${escapeHtml(proInfo.status)})
            </span>
          </div>
          <div class="acc-quota-row">
            <span>Compute Units:</span>
            <span style="color: #93c5fd; font-size:11px;">正常 (无硬封锁)</span>
          </div>
        </div>

        <div style="margin-top: 4px;">
          <div style="font-size:11px; color:var(--text-dim); margin-bottom: 4px;">🏷️ 可用模型 (动态获取):</div>
          <div class="acc-models-tags">
            ${(acc.models || ['gemini-2.5-flash', 'gemini-2.5-pro']).map(m => `<span class="model-tag">${escapeHtml(m)}</span>`).join('')}
          </div>
        </div>
      </div>

      <div class="acc-card-footer">
        <div class="acc-actions-left">
          <button type="button" class="btn btn-secondary btn-sm" onclick="openChatsModal('${acc.id}', '${escapeHtml(acc.email)}')">
            <span>💬</span> 历史会话
          </button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="checkAccountHealth('${acc.id}')">
            <span>⚡</span> 检测
          </button>
          <button type="button" class="btn btn-secondary btn-sm" onclick="openHealthLogsModal('${acc.id}', '${escapeHtml(acc.remark || acc.email)}')" title="查看有效性自动巡检与会话保活日志">
            <span>🛡️</span> 检查日志
          </button>
        </div>
        <div>
          <button type="button" class="btn btn-secondary btn-sm" onclick="editAccount('${acc.id}')" title="编辑">
            ✏️
          </button>
          <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteAccount('${acc.id}')" title="删除">
            🗑️
          </button>
        </div>
      </div>
    `;
    grid.appendChild(card);
  });
}

let currentCookieMode = 'smart';

function setCookieMode(mode) {
  currentCookieMode = mode;
  const btnSmart = document.getElementById('btn-mode-smart');
  const btnManual = document.getElementById('btn-mode-manual');
  const boxSmart = document.getElementById('container-smart-cookie');
  const boxManual = document.getElementById('container-manual-cookie');
  const manualTitle = document.getElementById('manual-cookie-title');
  const manualHelp = document.getElementById('manual-cookie-help');

  if (btnSmart && btnManual && boxSmart && boxManual) {
    if (mode === 'smart') {
      btnSmart.classList.add('active');
      btnManual.classList.remove('active');
      boxSmart.style.display = 'block';
      boxManual.style.display = 'flex';
      if (manualTitle) manualTitle.innerText = '🔍 实时自动拆分结果:';
      if (manualHelp) manualHelp.innerText = '上方粘贴后下方将实时自动拆分填充';
    } else {
      btnSmart.classList.remove('active');
      btnManual.classList.add('active');
      boxSmart.style.display = 'none';
      boxManual.style.display = 'flex';
      if (manualTitle) manualTitle.innerText = '✍️ 手动分项填写两个 Key:';
      if (manualHelp) manualHelp.innerText = '请分别填入 __Secure-1PSID 与 __Secure-1PSIDTS';
    }
  }
}

function parseCookieText(raw) {
  if (!raw || !raw.trim()) {
    return { psid: '', psidts: '', email: '' };
  }
  const text = raw.trim();
  let psid = '';
  let psidts = '';
  let email = '';

  // 1. JSON 格式解析（支持 Key-Value 对象或 EditThisCookie 数组格式）
  try {
    const data = JSON.parse(text);
    if (Array.isArray(data)) {
      for (const item of data) {
        if (!item || typeof item !== 'object') continue;
        const name = (item.name || item.key || '').trim();
        const val = (item.value || '').trim();
        if (name === '__Secure-1PSID') psid = val;
        else if (name === '__Secure-1PSIDTS') psidts = val;
      }
    } else if (typeof data === 'object' && data !== null) {
      if (data['__Secure-1PSID']) psid = String(data['__Secure-1PSID']).trim();
      if (data['__Secure-1PSIDTS']) psidts = String(data['__Secure-1PSIDTS']).trim();
      if (data['email']) email = String(data['email']).trim();

      // 容错：不区分大小写匹配
      for (const [k, v] of Object.entries(data)) {
        const lowerK = k.toLowerCase();
        if (!psid && lowerK === '__secure-1psid') psid = String(v).trim();
        if (!psidts && lowerK === '__secure-1psidts') psidts = String(v).trim();
        if (!email && (lowerK === 'email' || lowerK === 'account')) email = String(v).trim();
      }
    }
  } catch (e) {
    // 非合法 JSON，继续正则匹配
  }

  // 2. 正则提取 PSID
  if (!psid) {
    const m = text.match(/__Secure-1PSID(?:["':=\s\t]+)([^"';\s\r\n&]+)/i);
    if (m) psid = m[1].trim();
  }

  // 3. 正则提取 PSIDTS
  if (!psidts) {
    const m = text.match(/__Secure-1PSIDTS(?:["':=\s\t]+)([^"';\s\r\n&]+)/i);
    if (m) psidts = m[1].trim();
  }

  // 4. 正则提取邮箱
  if (!email) {
    const m = text.match(/[a-zA-Z0-9_.+-]+@[a-zA-Z0-9-]+\.[a-zA-Z0-9-.]+/);
    if (m) email = m[0].trim();
  }

  // 5. 容错提取：若直接粘贴了以 g.a 开头的很长字符串（单凭据直接作为 PSID）
  if (!psid && text.startsWith('g.a') && text.length > 50 && !text.includes(' ') && !text.includes('\n')) {
    psid = text;
  }

  return { psid, psidts, email };
}

function handleCookieInput() {
  const cookieInput = document.getElementById('input-acc-cookie');
  if (!cookieInput) return;
  const text = cookieInput.value;
  const feedback = document.getElementById('parse-feedback');
  const psidInput = document.getElementById('input-acc-psid');
  const psidtsInput = document.getElementById('input-acc-psidts');
  const remarkInput = document.getElementById('input-acc-remark');

  if (!text || !text.trim()) {
    if (feedback) feedback.style.display = 'none';
    return;
  }

  const { psid, psidts, email } = parseCookieText(text);

  if (psid && psidInput) {
    psidInput.value = psid;
  }
  if (psidts && psidtsInput) {
    psidtsInput.value = psidts;
  }
  if (email && remarkInput && !remarkInput.value.trim()) {
    remarkInput.value = email;
  }

  if (feedback) {
    if (psid && psidts) {
      feedback.style.display = 'block';
      feedback.style.color = 'var(--success)';
      feedback.innerText = '✨ 自动拆分成功！已提取 Key 1 (__Secure-1PSID) 与 Key 2 (__Secure-1PSIDTS)';
    } else if (psid) {
      feedback.style.display = 'block';
      feedback.style.color = 'var(--warning)';
      feedback.innerText = '✨ 已提取 Key 1 (__Secure-1PSID)；未检测到 Key 2 (__Secure-1PSIDTS)，建议补充';
    } else {
      feedback.style.display = 'none';
    }
  }
}

function openAccountModal(accountData = null) {
  const modal = document.getElementById('modal-account');
  const title = document.getElementById('modal-account-title');
  const form = document.getElementById('form-account');

  form.reset();

  const feedback = document.getElementById('parse-feedback');
  if (feedback) feedback.style.display = 'none';

  if (accountData) {
    title.innerText = '编辑 Gemini 账号';
    document.getElementById('input-acc-id').value = accountData.id;
    document.getElementById('input-acc-remark').value = accountData.remark || accountData.email || '';
    document.getElementById('input-acc-proxy').value = accountData.proxy || '';
    document.getElementById('input-acc-concurrency').value = accountData.concurrency_limit || '';
    document.getElementById('check-acc-enabled').checked = accountData.is_enabled;

    const psid = accountData.secure_1psid || '';
    const psidts = accountData.secure_1psidts || '';

    document.getElementById('input-acc-psid').value = psid;
    document.getElementById('input-acc-psidts').value = psidts;

    if (psid && psidts) {
      document.getElementById('input-acc-cookie').value = JSON.stringify({
        "__Secure-1PSID": psid,
        "__Secure-1PSIDTS": psidts
      }, null, 2);
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.color = 'var(--success)';
        feedback.innerText = '✨ 凭据已成功载入！可直接在下方查看或修改两个 Key';
      }
    } else if (psid) {
      document.getElementById('input-acc-cookie').value = psid;
      if (feedback) {
        feedback.style.display = 'block';
        feedback.style.color = 'var(--warning)';
        feedback.innerText = '✨ 已载入 Key 1 (__Secure-1PSID)，未配置 Key 2';
      }
    } else {
      document.getElementById('input-acc-cookie').value = '';
    }
  } else {
    title.innerText = '添加 Gemini 账号';
    document.getElementById('input-acc-id').value = '';
    document.getElementById('input-acc-remark').value = '';
    document.getElementById('input-acc-proxy').value = '';
    document.getElementById('input-acc-concurrency').value = '';
    document.getElementById('check-acc-enabled').checked = true;
    document.getElementById('input-acc-cookie').placeholder = '📋 在此粘贴包含 __Secure-1PSID 与 __Secure-1PSIDTS 的完整 JSON、数组或标头，粘贴后下方将实时自动拆分填入两个 Key！';
    document.getElementById('input-acc-psid').placeholder = 'g.a8i... / g.a000... (必填)';
    document.getElementById('input-acc-psidts').placeholder = 'sidts-... (建议填写)';
    document.getElementById('input-acc-cookie').value = '';
    document.getElementById('input-acc-psid').value = '';
    document.getElementById('input-acc-psidts').value = '';
  }
  setCookieMode('smart');
  modal.style.display = 'flex';
}

function closeAccountModal() {
  document.getElementById('modal-account').style.display = 'none';
}

async function editAccount(accId) {
  try {
    showToast('正在读取账号配置与凭据...', 'info');
    const detail = await apiRequest(`/api/accounts/${accId}`);
    openAccountModal(detail);
  } catch (err) {
    showToast(`获取账号详情失败: ${err.message}`, 'error');
  }
}

async function deleteAccount(accId) {
  if (!confirm('确定要删除此 Gemini 账号吗？')) return;
  try {
    await apiRequest(`/api/accounts/${accId}`, { method: 'DELETE' });
    showToast('账号已成功删除', 'success');
    loadAccounts();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function checkAccountHealth(accId) {
  try {
    showToast('正在向 Google Gemini 检测账号凭据与配额...', 'info');
    const res = await apiRequest(`/api/accounts/${accId}/check`, { method: 'POST' });
    const r = res.result || {};
    const modelsCount = (r.models || []).length;
    const flashRemain = r.quota?.flash?.remaining || '100%';
    const statusText = r.status === 'ACTIVE' ? '正常可用' : (r.status === 'EXPIRED' ? '已失效' : '受限');
    showToast(`检测结果: [${statusText}] | 可用模型: ${modelsCount} 个 | Flash额度: ${flashRemain}`, r.status === 'ACTIVE' ? 'success' : 'error');
    loadAccounts();
  } catch (err) {
    showToast(`检测失败: ${err.message}`, 'error');
  }
}

// ==================== 4. Tab 2: 系统配置 ====================
async function loadSettings() {
  try {
    const data = await apiRequest('/api/settings');
    state.settings = data;

    // 巡检间隔单选框匹配
    const interval = data.health_check_interval_minutes || 30;
    const presets = [10, 30, 60];
    const radios = document.getElementsByName('check_interval_preset');
    if (presets.includes(interval)) {
      radios.forEach(r => { if (parseInt(r.value) === interval) r.checked = true; });
      document.getElementById('input-custom-interval').value = '';
    } else {
      radios.forEach(r => { if (r.value === 'custom') r.checked = true; });
      document.getElementById('input-custom-interval').value = interval;
    }

    document.getElementById('check-auto-disable').checked = !!data.auto_disable_expired;
    document.getElementById('check-failover-retry').checked = !!data.failover_retry;
    const defConcurrencyEl = document.getElementById('input-default-concurrency');
    if (defConcurrencyEl) defConcurrencyEl.value = data.default_account_concurrency || 2;
    document.getElementById('input-timeout').value = data.timeout_seconds || 30;
    document.getElementById('input-proxy').value = data.global_proxy || '';
    document.getElementById('input-panel-password').value = data.panel_password || '';
    document.getElementById('input-api-key').value = data.api_key || '';
    document.getElementById('input-max-logs').value = data.max_log_entries || 20000;
    document.getElementById('input-retention-days').value = data.log_retention_days || 30;
  } catch (err) {
    showToast(err.message, 'error');
  }
}

async function saveSettings(e) {
  e.preventDefault();

  let interval = 30;
  const radios = document.getElementsByName('check_interval_preset');
  let selectedPreset = '30';
  radios.forEach(r => { if (r.checked) selectedPreset = r.value; });

  if (selectedPreset === 'custom') {
    interval = parseInt(document.getElementById('input-custom-interval').value) || 30;
  } else {
    interval = parseInt(selectedPreset) || 30;
  }

  const payload = {
    health_check_interval_minutes: interval,
    default_account_concurrency: parseInt(document.getElementById('input-default-concurrency')?.value) || 2,
    auto_disable_expired: document.getElementById('check-auto-disable').checked,
    failover_retry: document.getElementById('check-failover-retry').checked,
    timeout_seconds: parseInt(document.getElementById('input-timeout').value) || 30,
    global_proxy: document.getElementById('input-proxy').value.trim(),
    panel_password: document.getElementById('input-panel-password').value.trim(),
    api_key: document.getElementById('input-api-key').value.trim(),
    max_log_entries: parseInt(document.getElementById('input-max-logs').value) || 20000,
    log_retention_days: parseInt(document.getElementById('input-retention-days').value) || 30,
  };

  try {
    await apiRequest('/api/settings', {
      method: 'POST',
      body: JSON.stringify(payload)
    });
    showToast('配置保存成功，动态重置立即生效！', 'success');
    checkAuthStatus();
    loadSettings();
  } catch (err) {
    showToast(`保存失败: ${err.message}`, 'error');
  }
}

// ==================== 5. Tab 3: 请求日志 (参考图 1、2、3) ====================
async function loadLogs() {
  try {
    const params = new URLSearchParams({
      page: state.logs.page,
      page_size: state.logs.pageSize,
    });
    if (state.logs.status) params.append('status', state.logs.status);
    if (state.logs.operation) params.append('operation', state.logs.operation);

    const res = await apiRequest(`/api/logs?${params.toString()}`);
    state.logs.total = res.total;
    state.logs.items = res.items || [];
    renderLogsTable();
  } catch (err) {
    showToast(err.message, 'error');
  }
}

function renderLogsTable() {
  const tbody = document.getElementById('logs-tbody');
  tbody.innerHTML = '';

  const { total, page, pageSize, items } = state.logs;

  if (items.length === 0) {
    tbody.innerHTML = `
      <tr>
        <td colspan="9" style="text-align: center; color: var(--text-dim); padding: 40px 0;">
          暂无匹配的审计日志记录
        </td>
      </tr>
    `;
  } else {
    items.forEach(log => {
      const isSuccess = log.status === '已完成';
      const statusBadge = isSuccess ? 'badge-success' : 'badge-danger';
      const statusCodeColor = log.status_code === 200 ? 'var(--success)' : 'var(--danger)';

      const tr = document.createElement('tr');
      tr.innerHTML = `
        <td><code>${escapeHtml(log.operation)}</code></td>
        <td>${escapeHtml(log.token_email || '未知')}</td>
        <td><span class="badge ${statusBadge}">[${escapeHtml(log.status)}]</span></td>
        <td>${escapeHtml(log.progress || '100%')}</td>
        <td><strong style="color: ${statusCodeColor}; font-family: var(--font-mono);">${log.status_code}</strong></td>
        <td class="summary-col" title="${escapeHtml(log.summary || '')}">${escapeHtml(log.summary || '-')}</td>
        <td>${Number(log.duration).toFixed(2)}</td>
        <td style="color: var(--text-dim); font-size: 12px;">${escapeHtml(log.created_at)}</td>
        <td>
          <button type="button" class="btn btn-secondary btn-sm" onclick="showLogDetail(${log.id})">
            [详情]
          </button>
        </td>
      </tr>
      `;
      tbody.appendChild(tr);
    });
  }

  // 分页文本 (参考图 1)
  const startIdx = total === 0 ? 0 : (page - 1) * pageSize + 1;
  const endIdx = Math.min(page * pageSize, total);
  document.getElementById('pagination-info').innerText = `显示第 ${startIdx} 到 ${endIdx} 条，共 ${total} 条记录`;
  document.getElementById('text-current-page').innerText = page;

  document.getElementById('btn-prev-page').disabled = (page <= 1);
  document.getElementById('btn-next-page').disabled = (endIdx >= total);
}

// 日志详情弹窗 (完全对照图 2、图 3)
async function showLogDetail(logId) {
  try {
    const log = await apiRequest(`/api/logs/${logId}`);
    const modal = document.getElementById('modal-log-detail');
    const container = document.getElementById('log-detail-content');

    const isSuccess = log.status === '已完成';
    const reqStr = JSON.stringify(log.request_data || {}, null, 2);

    let middleHtml = '';

    if (isSuccess) {
      // 图 2 成功态排版
      const isImage = log.operation === 'generate_image' || log.result_url;
      middleHtml = `
        <div>
          <div class="section-label">结果摘要</div>
          <div class="summary-card-success">
            ${escapeHtml(log.summary || '生成成功，已返回图片/文本结果')}
          </div>
        </div>

        ${isImage ? `
          <div>
            <div class="section-label">生成结果</div>
            <div class="result-preview-card">
              <div style="font-size: 12px; color: var(--text-dim);">主结果</div>
              <div class="result-url-text">URL: ${escapeHtml(log.result_url || '无直链地址')}</div>
              <div id="img-preview-box-${log.id}">
                ${log.result_url ? `
                  <button type="button" class="btn btn-secondary btn-sm" onclick="loadProxyImage('${log.id}', '${encodeURIComponent(log.result_url)}')">
                    <span>🖼️</span> [ 点击加载预览 ] (直接在弹窗内展示生成的图片)
                  </button>
                ` : '<span style="color:var(--text-dim); font-size:12px;">无可用图片地址</span>'}
              </div>
            </div>
          </div>

          <div>
            <div class="section-label">2K/4K 资产信息</div>
            <div style="background: rgba(0,0,0,0.2); border:1px solid var(--border-color); border-radius:var(--radius-sm); padding:10px 14px; font-size:12px; color:var(--text-dim);">
              [ 无资产详情 ]
            </div>
          </div>
        ` : ''}

        ${log.response_data ? `
          <details>
            <summary style="cursor: pointer; font-size: 13px; font-weight: 600; color: #60a5fa; margin: 4px 0;">
              ▶ 完整响应 (点击展开/折叠)
            </summary>
            <pre class="code-box" style="margin-top: 8px;">${escapeHtml(JSON.stringify(log.response_data, null, 2))}</pre>
          </details>
        ` : ''}
      `;
    } else {
      // 图 3 失败态排版
      const errRespStr = JSON.stringify(log.error_response || {}, null, 2);
      middleHtml = `
        <div>
          <div class="section-label">错误原因</div>
          <div class="summary-card-danger">
            ${escapeHtml(log.error_reason || '没有可用的 Token 进行请求或调用超时')}
          </div>
        </div>

        <div>
          <div class="section-label">错误响应</div>
          <pre class="code-box">${escapeHtml(errRespStr)}</pre>
        </div>
      `;
    }

    // 基本信息网格 (图 2、图 3 通用)
    const basicInfoHtml = `
      <div>
        <div class="section-label">基本信息</div>
        <div class="info-grid">
          <div class="info-item"><span>操作:</span> <strong>${escapeHtml(log.operation)}</strong></div>
          <div class="info-item"><span>状态:</span> <strong>${escapeHtml(log.status)}</strong></div>
          <div class="info-item"><span>状态码:</span> <strong>${log.status_code}</strong></div>
          <div class="info-item"><span>耗时:</span> <strong>${Number(log.duration).toFixed(2)}秒</strong></div>
          <div class="info-item"><span>时间:</span> <strong>${escapeHtml(log.created_at)}</strong></div>
          <div class="info-item"><span>Token:</span> <strong>${escapeHtml(log.token_email || '未知')}</strong></div>
          <div class="info-item"><span>日志ID:</span> <strong>${log.id}</strong></div>
          <div class="info-item"><span>进度:</span> <strong>${escapeHtml(log.progress || '100%')}</strong></div>
        </div>
      </div>
    `;

    container.innerHTML = `
      <div>
        <div class="section-label">请求数据</div>
        <pre class="code-box">${escapeHtml(reqStr)}</pre>
      </div>

      ${middleHtml}

      ${basicInfoHtml}
    `;

    modal.style.display = 'flex';
  } catch (err) {
    showToast(`拉取详情失败: ${err.message}`, 'error');
  }
}

// 通过反向代理加载 Google CDN 图片 (解决国内破损)
function loadProxyImage(logId, encodedUrl) {
  const box = document.getElementById(`img-preview-box-${logId}`);
  if (!box) return;

  const rawUrl = decodeURIComponent(encodedUrl);
  const proxyUrl = `/api/proxy/image?url=${encodeURIComponent(rawUrl)}`;

  box.innerHTML = `
    <div class="preview-img-container">
      <div style="padding: 20px; font-size: 12px; color: var(--text-dim);" id="img-loading-${logId}">
        🔄 正在通过后端代理中转加载图片...
      </div>
      <img src="${proxyUrl}" alt="Generated Preview" style="display:none;" 
           onload="document.getElementById('img-loading-${logId}').style.display='none'; this.style.display='block';"
           onerror="document.getElementById('img-loading-${logId}').innerText='❌ 图片中转拉取失败，请检查全局网络代理';">
    </div>
  `;
}

// ==================== 6. Tab 4: 测试页面 (Chat 独立会话工作室) ====================
function populateTestAccounts() {
  const select = document.getElementById('select-test-account');
  if (!select) return;
  const currentVal = select.value;
  select.innerHTML = '<option value="">自动调度 (Round-Robin 账号池)</option>';

  state.accounts.forEach(acc => {
    const opt = document.createElement('option');
    opt.value = acc.id;
    opt.textContent = `${acc.remark || acc.email} [${acc.status}]`;
    select.appendChild(opt);
  });

  if (currentVal && state.accounts.some(a => a.id === currentVal)) {
    select.value = currentVal;
  }
  updateTestModels();
}

function updateTestModels() {
  const accountSelect = document.getElementById('select-test-account');
  const modelSelect = document.getElementById('select-test-model');
  if (!accountSelect || !modelSelect) return;

  const selectedAccId = accountSelect.value;
  let availableModels = [];

  if (selectedAccId) {
    const acc = state.accounts.find(a => a.id === selectedAccId);
    if (acc && acc.models && acc.models.length > 0) {
      availableModels = acc.models;
    }
  } else {
    // 自动调度：提取所有启用账号的模型列表并去重
    const modelSet = new Set();
    state.accounts.forEach(a => {
      if (a.is_enabled && a.models && Array.isArray(a.models)) {
        a.models.forEach(m => modelSet.add(m));
      }
    });
    availableModels = Array.from(modelSet);
  }

  // 兜底常用模型
  if (availableModels.length === 0) {
    availableModels = ['gemini-flash', 'gemini-pro', 'gemini-flash-lite', 'gemini-argon'];
  }

  const prevVal = modelSelect.value;
  modelSelect.innerHTML = '';

  // 1. 默认模型（Google 自动调度，完美支持生图与对话）
  const defaultOpt = document.createElement('option');
  defaultOpt.value = 'default';
  defaultOpt.textContent = '🌟 默认模型 (推荐：支持画图生图与全功能对话)';
  modelSelect.appendChild(defaultOpt);

  // 2. 账号具体模型列表
  const modelLabels = {
    'gemini-flash': 'gemini-flash (快速对话)',
    'gemini-flash-lite': 'gemini-flash-lite (轻量低延迟)',
    'gemini-pro': 'gemini-pro (纯文本/复杂推理，不支持生图)',
    'gemini-argon': 'gemini-argon (实验模型)',
  };

  availableModels.forEach(m => {
    const opt = document.createElement('option');
    opt.value = m;
    opt.textContent = modelLabels[m] || m;
    modelSelect.appendChild(opt);
  });

  if (prevVal && (prevVal === 'default' || availableModels.includes(prevVal))) {
    modelSelect.value = prevVal;
  } else {
    modelSelect.value = 'default';
  }
}

async function handleChatSend() {
  const input = document.getElementById('input-chat-message');
  const sendBtn = document.getElementById('btn-send-chat');
  const container = document.getElementById('chat-messages-container');
  const welcomeBanner = document.getElementById('chat-welcome-banner');

  if (!input || !sendBtn || !container) return;

  const text = (input.value || '').trim();
  if (!text) return;

  if (welcomeBanner) welcomeBanner.style.display = 'none';

  // 1. 追加用户发送气泡
  const userBubble = document.createElement('div');
  userBubble.className = 'chat-bubble user';
  userBubble.innerText = text;
  container.appendChild(userBubble);

  input.value = '';
  sendBtn.disabled = true;
  sendBtn.innerHTML = '<span>⏳</span> 生成中...';

  // 2. 追加加载中占位气泡
  const loadingBubble = document.createElement('div');
  loadingBubble.className = 'chat-bubble loading';
  loadingBubble.id = 'chat-loading-bubble';
  loadingBubble.innerHTML = '<span>⏳</span> Gemini 正在思考与生成回答...';
  container.appendChild(loadingBubble);
  container.scrollTop = container.scrollHeight;

  const accountId = document.getElementById('select-test-account').value || null;
  const rawModel = document.getElementById('select-test-model').value;
  const model = (rawModel === 'default' || !rawModel) ? null : rawModel;

  try {
    const res = await apiRequest('/api/test/generate', {
      method: 'POST',
      body: JSON.stringify({
        account_id: accountId,
        operation: 'chat_completion',
        model: model,
        prompt: text,
      })
    });

    if (loadingBubble.parentNode) loadingBubble.remove();

    const assistantBubble = document.createElement('div');
    assistantBubble.className = 'chat-bubble assistant';

    const replyText = res.choices?.[0]?.message?.content || JSON.stringify(res, null, 2);
    const thoughts = res.choices?.[0]?.message?.reasoning_content || '';
    const images = res.choices?.[0]?.message?.images || [];

    let thoughtsHtml = thoughts ? `<div class="turn-thoughts"><strong>💭 思考链路:</strong><br>${escapeHtml(thoughts)}</div>` : '';
    let imagesHtml = '';
    if (images && images.length > 0) {
      imagesHtml = `
        <div style="display:flex; flex-wrap:wrap; gap:12px; margin-top:10px;">
          ${images.map(img => `
            <div class="preview-img-container" style="max-width:320px; border-radius:8px; overflow:hidden; border:1px solid rgba(255,255,255,0.1);">
              <a href="${img.url}" target="_blank" title="点击新窗口查看原图">
                <img src="${img.url}" alt="${escapeHtml(img.alt || 'img')}" style="width:100%; display:block; cursor:pointer;">
              </a>
            </div>
          `).join('')}
        </div>
      `;
    }

    assistantBubble.innerHTML = `
      ${thoughtsHtml}
      <div style="white-space:pre-wrap; line-height:1.6;">${escapeHtml(replyText)}</div>
      ${imagesHtml}
    `;

    container.appendChild(assistantBubble);
  } catch (err) {
    if (loadingBubble.parentNode) loadingBubble.remove();

    const errBubble = document.createElement('div');
    errBubble.className = 'chat-bubble error';
    errBubble.innerHTML = `<strong>❌ 请求失败:</strong> ${escapeHtml(err.message)}`;
    container.appendChild(errBubble);
  } finally {
    sendBtn.disabled = false;
    sendBtn.innerHTML = '<span>🚀</span> 发送';
    container.scrollTop = container.scrollHeight;
  }
}

function clearChatHistory() {
  const container = document.getElementById('chat-messages-container');
  if (!container) return;
  container.innerHTML = `
    <div class="chat-welcome-banner" id="chat-welcome-banner">
      <div class="chat-welcome-icon">💬</div>
      <div class="chat-welcome-title">Gemini 对话与生图在线测试</div>
      <div class="chat-welcome-desc">
        在此直接向已配置账号发送对话消息或生图指令（如 <em>"画一只在赛博朋克霓虹灯下的猫"</em> 或 <em>"用两句话解释量子纠缠"</em>）。
        <br>模型下拉框已直接继承该账号检测出来的实际模型，调用过程将自动入库审计日志。
      </div>
    </div>
  `;
}

// ==================== 7. 历史会话查看 Modal ====================
async function openChatsModal(accId, email) {
  const modal = document.getElementById('modal-chats');
  document.getElementById('modal-chats-title').innerText = `Gemini 网页端历史会话 - ${email}`;
  const listArea = document.getElementById('chats-items');
  const turnsArea = document.getElementById('chats-turns-area');

  listArea.innerHTML = '<div style="padding:16px; color:var(--text-dim); font-size:12px;">加载会话中...</div>';
  turnsArea.innerHTML = '<div class="chats-turns-placeholder">请从左侧选择一个会话查看对话详情</div>';
  modal.style.display = 'flex';

  try {
    const res = await apiRequest(`/api/accounts/${accId}/chats`);
    const chats = res.chats || [];
    if (chats.length === 0) {
      listArea.innerHTML = '<div style="padding:16px; color:var(--text-dim); font-size:12px;">该账号暂无历史会话</div>';
      return;
    }

    listArea.innerHTML = '';
    chats.forEach(c => {
      const item = document.createElement('div');
      item.className = 'chat-item';
      item.innerHTML = `
        <div class="chat-item-title" title="${escapeHtml(c.title)}">${escapeHtml(c.title)}</div>
        <div class="chat-item-time">${escapeHtml(c.timestamp || '近期')}</div>
      `;
      item.onclick = () => loadChatTurns(accId, c.cid, item);
      listArea.appendChild(item);
    });
  } catch (err) {
    listArea.innerHTML = `<div style="padding:16px; color:var(--danger); font-size:12px;">拉取会话失败: ${escapeHtml(err.message)}</div>`;
  }
}

async function loadChatTurns(accId, cid, itemElement) {
  document.querySelectorAll('.chat-item').forEach(el => el.classList.remove('active'));
  if (itemElement) itemElement.classList.add('active');

  const turnsArea = document.getElementById('chats-turns-area');
  turnsArea.innerHTML = '<div class="chats-turns-placeholder">正在提取对话详情...</div>';

  try {
    const res = await apiRequest(`/api/accounts/${accId}/chats/${cid}`);
    const turns = res.history?.turns || [];
    if (turns.length === 0) {
      turnsArea.innerHTML = '<div class="chats-turns-placeholder">该会话暂无对话轮次记录</div>';
      return;
    }

    turnsArea.innerHTML = '';
    turns.forEach(t => {
      const bubble = document.createElement('div');
      bubble.className = `turn-bubble ${t.role === 'user' ? 'user' : 'model'}`;

      let imagesHtml = '';
      if (t.images && t.images.length > 0) {
        imagesHtml = `
          <div style="display:flex; flex-wrap:wrap; gap:8px; margin-top:8px;">
            ${t.images.map(img => `
              <div class="preview-img-container" style="max-width:200px;">
                <img src="/api/proxy/image?url=${encodeURIComponent(img.url)}" alt="${escapeHtml(img.alt || 'img')}">
              </div>
            `).join('')}
          </div>
        `;
      }

      bubble.innerHTML = `
        ${t.thoughts ? `<div class="turn-thoughts"><strong>💭 思考链路:</strong><br>${escapeHtml(t.thoughts)}</div>` : ''}
        <div>${escapeHtml(t.text)}</div>
        ${imagesHtml}
      `;
      turnsArea.appendChild(bubble);
    });
  } catch (err) {
    turnsArea.innerHTML = `<div style="color:var(--danger); padding:20px;">提取会话详情失败: ${escapeHtml(err.message)}</div>`;
  }
}

// ==================== 8. Tab 4: 图片管理 (本地持久化图片清理与查看) ====================
async function loadImages() {
  const grid = document.getElementById('images-gallery-grid');
  const emptyState = document.getElementById('images-empty-state');
  const statCount = document.getElementById('stat-images-count');
  const statSize = document.getElementById('stat-images-size');

  if (!grid) return;
  grid.innerHTML = '<div style="color:var(--text-dim); padding:20px;">正在加载本地图片资产...</div>';

  try {
    const data = await apiRequest('/api/images');
    const images = data.images || [];

    if (statCount) statCount.textContent = data.total_count || 0;
    if (statSize) statSize.textContent = data.total_size_human || '0 B';

    if (images.length === 0) {
      grid.style.display = 'none';
      if (emptyState) emptyState.style.display = 'block';
      grid.innerHTML = '';
      return;
    }

    grid.style.display = 'grid';
    if (emptyState) emptyState.style.display = 'none';
    grid.innerHTML = '';

    images.forEach(img => {
      const card = document.createElement('div');
      card.className = 'image-card';
      const dateStr = img.created_at ? new Date(img.created_at * 1000).toLocaleString('zh-CN') : '近期';

      card.innerHTML = `
        <div class="image-thumb-wrap" onclick="window.open('${img.url}', '_blank')">
          <img src="${img.url}" class="image-thumb" alt="${escapeHtml(img.filename)}" loading="lazy">
          <span class="image-badge-size">${escapeHtml(img.size_human)}</span>
        </div>
        <div class="image-card-info">
          <div class="image-filename" title="${escapeHtml(img.filename)}">${escapeHtml(img.filename)}</div>
          <div class="image-meta-time">📅 ${escapeHtml(dateStr)}</div>
          <div class="image-card-actions">
            <a href="${img.url}" target="_blank" class="btn btn-secondary btn-sm" title="在新窗口打开原图">
              <span>🔍</span> 查看原图
            </a>
            <button type="button" class="btn btn-outline-danger btn-sm" onclick="deleteImage('${escapeHtml(img.filename)}')">
              <span>🗑️</span> 删除
            </button>
          </div>
        </div>
      `;
      grid.appendChild(card);
    });
  } catch (err) {
    grid.innerHTML = `<div style="color:var(--danger); padding:20px;">加载图片资产失败: ${escapeHtml(err.message)}</div>`;
  }
}

async function deleteImage(filename) {
  if (!confirm(`确定要删除图片 "${filename}" 吗？此操作无法恢复。`)) return;
  try {
    await apiRequest(`/api/images/${encodeURIComponent(filename)}`, { method: 'DELETE' });
    showToast(`已成功删除图片 ${filename}`, 'success');
    loadImages();
  } catch (err) {
    showToast(`删除失败: ${err.message}`, 'error');
  }
}

async function clearAllImages() {
  if (!confirm('⚠️ 警告：确定要一键清空所有已保存的本地图片吗？磁盘文件将被永久删除！')) return;
  try {
    const res = await apiRequest('/api/images/clear', { method: 'POST' });
    showToast(res.message || '已清空所有图片', 'success');
    loadImages();
  } catch (err) {
    showToast(`清空失败: ${err.message}`, 'error');
  }
}

// ==================== 8.5 账号有效性巡检与保活日志 Modal ====================
let currentHealthLogAccId = null;

async function openHealthLogsModal(accId, accRemark) {
  currentHealthLogAccId = accId;
  const modal = document.getElementById('modal-health-logs');
  const title = document.getElementById('health-log-title');
  if (title) title.textContent = `账号 [${accRemark || accId}] 有效性巡检与保活日志`;
  if (modal) modal.style.display = 'flex';

  await loadAccountHealthLogs(accId);
}

function closeHealthLogsModal() {
  const modal = document.getElementById('modal-health-logs');
  if (modal) modal.style.display = 'none';
  currentHealthLogAccId = null;
}

async function loadAccountHealthLogs(accId) {
  const tbody = document.getElementById('tbody-health-logs');
  const emptyPlaceholder = document.getElementById('health-logs-empty');
  const statChecks = document.getElementById('health-stat-checks');
  const statKeepAlives = document.getElementById('health-stat-keepalives');
  const statLastCheck = document.getElementById('health-stat-last-check');
  const statLastKeepAlive = document.getElementById('health-stat-last-keepalive');

  if (!tbody) return;
  tbody.innerHTML = '<tr><td colspan="5" style="text-align:center; padding:24px; color:var(--text-dim);">正在加载检查与保活轨迹...</td></tr>';

  try {
    const res = await apiRequest(`/api/accounts/${accId}/health-logs?page=1&page_size=50`);
    const items = res.items || [];
    const stats = res.stats || {};

    if (statChecks) statChecks.textContent = stats.total_checks || 0;
    if (statKeepAlives) statKeepAlives.textContent = stats.total_keep_alives || 0;
    if (statLastCheck) statLastCheck.textContent = stats.last_checked_at || '-';
    if (statLastKeepAlive) statLastKeepAlive.textContent = stats.last_keep_alive_at || '-';

    if (items.length === 0) {
      tbody.innerHTML = '';
      if (emptyPlaceholder) emptyPlaceholder.style.display = 'block';
      return;
    }

    if (emptyPlaceholder) emptyPlaceholder.style.display = 'none';
    tbody.innerHTML = '';

    items.forEach(log => {
      const tr = document.createElement('tr');
      const isKeepAlive = log.operation === 'keep_alive';
      const opBadge = isKeepAlive 
        ? '<span class="badge-keepalive">🛡️ Cookie 自动轮换</span>' 
        : '<span class="badge-healthcheck">⚡ 有效性巡检</span>';

      const isExpired = log.status === 'EXPIRED' || log.status_code === 401;
      const isSuccess = !isExpired && (log.status === '已完成' || (log.status !== '失败' && log.status_code === 200));
      const statusBadge = isExpired 
        ? '<span class="badge badge-danger">已失效</span>' 
        : (isSuccess ? '<span class="badge badge-success">正常</span>' : '<span class="badge badge-warning">异常</span>');

      tr.innerHTML = `
        <td class="font-mono" style="font-size:12px; color:var(--text-dim);">${escapeHtml(log.created_at)}</td>
        <td>${opBadge}</td>
        <td>${statusBadge}</td>
        <td style="font-size:12px; line-height:1.5;">
          <div>${escapeHtml(log.summary || '')}</div>
          ${log.error_reason ? `<div style="color:var(--danger); font-size:11px; margin-top:2px;">❌ ${escapeHtml(log.error_reason)}</div>` : ''}
        </td>
        <td class="font-mono" style="font-size:12px; color:var(--text-dim);">${log.duration !== undefined && log.duration !== null ? log.duration + 's' : '-'}</td>
      `;
      tbody.appendChild(tr);
    });
  } catch (err) {
    tbody.innerHTML = `<tr><td colspan="5" style="text-align:center; padding:20px; color:var(--danger);">拉取日志失败: ${escapeHtml(err.message)}</td></tr>`;
  }
}

// ==================== 9. 事件监听器初始化 ====================
document.addEventListener('DOMContentLoaded', () => {
  initTabs();
  checkAuthStatus();
  loadAccounts();

  // 凭据输入模式切换绑定
  const btnModeSmart = document.getElementById('btn-mode-smart');
  const btnModeManual = document.getElementById('btn-mode-manual');
  if (btnModeSmart) btnModeSmart.onclick = () => setCookieMode('smart');
  if (btnModeManual) btnModeManual.onclick = () => setCookieMode('manual');

  // 绑定智能拆分实时监听 (input & paste)
  const inputAccCookie = document.getElementById('input-acc-cookie');
  if (inputAccCookie) {
    inputAccCookie.addEventListener('input', handleCookieInput);
    inputAccCookie.addEventListener('paste', () => {
      setTimeout(handleCookieInput, 50);
    });
  }

  // 账号表单提交
  document.getElementById('form-account').addEventListener('submit', async (e) => {
    e.preventDefault();
    const id = document.getElementById('input-acc-id').value;
    const remark = document.getElementById('input-acc-remark').value.trim();
    const proxy = document.getElementById('input-acc-proxy').value.trim();
    const concurrencyVal = document.getElementById('input-acc-concurrency').value.trim();
    const concurrencyLimit = concurrencyVal ? parseInt(concurrencyVal) : null;
    const isEnabled = document.getElementById('check-acc-enabled').checked;

    if (!remark) {
      showToast('请填写账号备注名称', 'error');
      return;
    }

    const psid = document.getElementById('input-acc-psid').value.trim();
    const psidts = document.getElementById('input-acc-psidts').value.trim();
    const rawCookie = document.getElementById('input-acc-cookie').value.trim();

    // 优先采用 Key 1 和 Key 2（无论是智能拆分得到的还是手动输入的）
    let cookiePayload = psid || rawCookie;
    let psidtsPayload = psidts;

    if (!id && !cookiePayload) {
      showToast('必须提供 Cookie 凭据或填写 Key 1 (__Secure-1PSID)', 'error');
      return;
    }

    try {
      if (id) {
        await apiRequest(`/api/accounts/${id}`, {
          method: 'PUT',
          body: JSON.stringify({
            email: remark,
            remark: remark,
            raw_cookie_or_psid: cookiePayload || undefined,
            psidts: psidtsPayload || undefined,
            proxy,
            is_enabled: isEnabled,
            concurrency_limit: concurrencyLimit,
          })
        });
        showToast('账号信息更新成功', 'success');
      } else {
        await apiRequest('/api/accounts', {
          method: 'POST',
          body: JSON.stringify({
            email: remark,
            remark: remark,
            raw_cookie_or_psid: cookiePayload,
            psidts: psidtsPayload,
            proxy,
            is_enabled: isEnabled,
            concurrency_limit: concurrencyLimit,
          })
        });
        showToast('账号凭据添加成功', 'success');
      }
      closeAccountModal();
      loadAccounts();
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // 添加账号打开按钮
  document.getElementById('btn-add-account-open').onclick = () => openAccountModal();
  document.getElementById('btn-close-account-modal').onclick = closeAccountModal;
  document.getElementById('btn-cancel-account-modal').onclick = closeAccountModal;
  document.getElementById('btn-refresh-accounts').onclick = () => {
    showToast('刷新账号状态与配额...', 'info');
    loadAccounts();
  };

  // 系统配置表单提交
  document.getElementById('form-settings').addEventListener('submit', saveSettings);
  document.getElementById('btn-trigger-check-now').onclick = async () => {
    try {
      showToast('已唤醒全量账号巡检...', 'info');
      await apiRequest('/api/settings/check-now', { method: 'POST' });
      showToast('巡检任务已启动，后台正在并发检测账号', 'success');
      setTimeout(loadAccounts, 3000);
    } catch (err) {
      showToast(err.message, 'error');
    }
  };

  // 日志筛选与分页
  document.getElementById('select-log-op').onchange = (e) => {
    state.logs.operation = e.target.value;
    state.logs.page = 1;
    loadLogs();
  };
  document.getElementById('select-log-status').onchange = (e) => {
    state.logs.status = e.target.value;
    state.logs.page = 1;
    loadLogs();
  };
  document.getElementById('btn-refresh-logs').onclick = () => {
    showToast('刷新审计日志...', 'info');
    loadLogs();
  };
  document.getElementById('btn-clear-logs').onclick = async () => {
    if (!confirm('确定要清空全部请求日志并回收 SQLite 存储空间吗？此操作不可撤销。')) return;
    try {
      await apiRequest('/api/logs', { method: 'DELETE' });
      showToast('日志已清空', 'success');
      loadLogs();
    } catch (err) {
      showToast(err.message, 'error');
    }
  };
  document.getElementById('btn-prev-page').onclick = () => {
    if (state.logs.page > 1) {
      state.logs.page--;
      loadLogs();
    }
  };
  document.getElementById('btn-next-page').onclick = () => {
    if (state.logs.page * state.logs.pageSize < state.logs.total) {
      state.logs.page++;
      loadLogs();
    }
  };
  document.getElementById('btn-close-log-modal').onclick = () => {
    document.getElementById('modal-log-detail').style.display = 'none';
  };

  // 会话弹窗关闭
  document.getElementById('btn-close-chats-modal').onclick = () => {
    document.getElementById('modal-chats').style.display = 'none';
  };

  // 账号有效性巡检与保活日志弹窗事件绑定
  const btnCloseHealthModal = document.getElementById('btn-close-health-modal');
  const btnRefreshHealthLogs = document.getElementById('btn-refresh-health-logs');
  if (btnCloseHealthModal) btnCloseHealthModal.onclick = closeHealthLogsModal;
  if (btnRefreshHealthLogs) {
    btnRefreshHealthLogs.onclick = () => {
      if (currentHealthLogAccId) loadAccountHealthLogs(currentHealthLogAccId);
    };
  }

  // 图片管理事件绑定
  const btnRefreshImages = document.getElementById('btn-refresh-images');
  const btnClearAllImages = document.getElementById('btn-clear-all-images');
  if (btnRefreshImages) btnRefreshImages.onclick = loadImages;
  if (btnClearAllImages) btnClearAllImages.onclick = clearAllImages;

  // 测试页面 Chat 对话事件绑定
  const btnSendChat = document.getElementById('btn-send-chat');
  const inputChatMessage = document.getElementById('input-chat-message');
  const btnClearChat = document.getElementById('btn-clear-chat');
  const selectTestAccount = document.getElementById('select-test-account');

  if (btnSendChat) btnSendChat.onclick = handleChatSend;
  if (btnClearChat) btnClearChat.onclick = clearChatHistory;
  if (selectTestAccount) selectTestAccount.onchange = updateTestModels;

  if (inputChatMessage) {
    inputChatMessage.addEventListener('keydown', (e) => {
      if (e.key === 'Enter' && !e.shiftKey) {
        e.preventDefault();
        handleChatSend();
      }
    });
  }

  // 登录表单提交
  document.getElementById('form-login').addEventListener('submit', async (e) => {
    e.preventDefault();
    const pwd = document.getElementById('input-login-password').value;
    try {
      const res = await apiRequest('/api/auth/login', {
        method: 'POST',
        body: JSON.stringify({ password: pwd })
      });
      state.panelToken = res.token || '';
      localStorage.setItem('gemini_panel_token', state.panelToken);
      showToast('登录成功', 'success');
      closeLoginModal();
      checkAuthStatus();
      switchTab(state.activeTab);
    } catch (err) {
      showToast(err.message, 'error');
    }
  });

  // 退出登录
  document.getElementById('btn-logout').onclick = async () => {
    try {
      await apiRequest('/api/auth/logout', { method: 'POST' });
    } catch (e) {}
    state.panelToken = '';
    localStorage.removeItem('gemini_panel_token');
    showToast('已退出登录', 'info');
    checkAuthStatus();
    loadAccounts();
  };
});
