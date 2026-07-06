(() => {
  const pinScreen = document.getElementById('pin-screen');
  const appScreen = document.getElementById('app-screen');
  const pinInput = document.getElementById('pin-input');
  const pinSubmit = document.getElementById('pin-submit');
  const pinError = document.getElementById('pin-error');
  const breadcrumbs = document.getElementById('breadcrumbs');
  const fileList = document.getElementById('file-list');
  const logoutBtn = document.getElementById('logout-btn');
  const uploadBtn = document.getElementById('upload-btn');
  const newFolderBtn = document.getElementById('new-folder-btn');
  const fileInput = document.getElementById('file-input');
  const folderInput = document.getElementById('folder-input');
  const dropHint = document.getElementById('drop-hint');
  const actionSheet = document.getElementById('action-sheet');
  const sheetTitle = document.getElementById('sheet-title');
  const uploadProgress = document.getElementById('upload-progress');
  const uploadProgressBar = document.getElementById('upload-progress-bar');
  const uploadProgressText = document.getElementById('upload-progress-text');

  const selectToggleBtn = document.getElementById('select-toggle-btn');
  const selectBar = document.getElementById('select-bar');
  const selectCount = document.getElementById('select-count');
  const selectDownloadBtn = document.getElementById('select-download-btn');
  const selectDeleteBtn = document.getElementById('select-delete-btn');
  const selectCancelBtn = document.getElementById('select-cancel-btn');

  const statsBtn = document.getElementById('stats-btn');
  const deviceDrawer = document.getElementById('device-drawer');
  const drawerCloseBtn = document.getElementById('drawer-close-btn');
  const deviceCards = document.getElementById('device-cards');

  const menuSheet = document.getElementById('menu-sheet');
  const menuChangeAddressBtn = document.getElementById('menu-change-address');
  const menuReloadBtn = document.getElementById('menu-reload');

  let currentPath = '';
  let sheetTarget = null;
  let lastEntries = [];
  let selectMode = false;
  const selectedPaths = new Set();

  // ---------- multi-device state ----------
  // Each saved PC looks like { id, name, address, pin }. "address" is
  // host:port with no protocol. This list (and which one is active) lives
  // in this browser's localStorage -- i.e. it's tied to whichever PC's page
  // you're currently using as your entry point, since there's no shared
  // backend across different PCs' independent servers.

  function loadDevices() {
    try {
      return JSON.parse(localStorage.getItem('pcbridge-devices') || '[]');
    } catch (e) {
      return [];
    }
  }

  let devices = loadDevices();
  let activeDeviceId = localStorage.getItem('pcbridge-active-device') || (devices[0] && devices[0].id) || null;

  function saveDevices() {
    localStorage.setItem('pcbridge-devices', JSON.stringify(devices));
    if (activeDeviceId) localStorage.setItem('pcbridge-active-device', activeDeviceId);
    syncActiveDeviceToNative();
  }

  // Mirrors the active device into the Android app's native storage, if
  // we're running inside it (window.AndroidBridge only exists there) --
  // lets the home-screen widget show status without needing the WebView.
  // No-op in a normal browser/PWA.
  function syncActiveDeviceToNative() {
    if (!window.AndroidBridge || !window.AndroidBridge.syncActiveDevice) return;
    const device = getActiveDevice();
    if (!device) return;
    try {
      window.AndroidBridge.syncActiveDevice(device.name, device.address, device.pin);
    } catch (e) {
      // Ignore -- purely a nice-to-have for the widget.
    }
  }

  function getActiveDevice() {
    return devices.find((d) => d.id === activeDeviceId) || null;
  }

  function deviceBase(device) {
    if (!device) return '';
    return device.address === window.location.host ? '' : `http://${device.address}`;
  }

  function makeDeviceId() {
    return 'd' + Date.now().toString(36) + Math.random().toString(36).slice(2, 7);
  }

  function escapeHtml(s) {
    const div = document.createElement('div');
    div.textContent = s == null ? '' : String(s);
    return div.innerHTML;
  }

  function withTimeout(ms) {
    const controller = new AbortController();
    const timer = setTimeout(() => controller.abort(), ms);
    return { signal: controller.signal, cancel: () => clearTimeout(timer) };
  }

  // ---------- networking ----------

  function apiUrl(path, params = {}, device = getActiveDevice()) {
    const base = deviceBase(device);
    const url = new URL(base + path, window.location.origin);
    Object.entries(params).forEach(([k, v]) => url.searchParams.set(k, v));
    if (device && device.pin && !url.searchParams.has('pin')) {
      url.searchParams.set('pin', device.pin);
    }
    return url.toString();
  }

  async function api(path, { method = 'GET', params = {}, body = null, device = getActiveDevice() } = {}) {
    const url = apiUrl(path, params, device);
    const headers = device && device.pin ? { 'X-PIN': device.pin } : {};
    const res = await fetch(url, { method, headers, body });
    if (res.status === 401) {
      if (device && device.id === activeDeviceId) showPinScreen('Incorrect PIN');
      throw new Error('unauthorized');
    }
    if (!res.ok) {
      const detail = await res.json().catch(() => ({}));
      throw new Error(detail.detail || `Request failed (${res.status})`);
    }
    return res;
  }

  function showPinScreen(errorMsg) {
    pinScreen.classList.remove('hidden');
    appScreen.classList.add('hidden');
    pinError.textContent = errorMsg || '';
    pinInput.value = '';
    pinInput.focus();
  }

  function showAppScreen() {
    pinScreen.classList.add('hidden');
    appScreen.classList.remove('hidden');
  }

  async function submitPin(candidatePin) {
    const existing = getActiveDevice();
    const address = existing ? existing.address : window.location.host;
    const candidate = {
      id: existing ? existing.id : makeDeviceId(),
      name: existing ? existing.name : 'This PC',
      address,
      pin: candidatePin,
    };
    try {
      await api('/api/whoami', { device: candidate });
      if (!existing) {
        try {
          const res = await api('/api/stats', { device: candidate });
          const data = await res.json();
          candidate.name = data.hostname || 'This PC';
        } catch (e) {
          // stats failing isn't fatal -- keep the default name
        }
      }
      devices = devices.filter((d) => d.id !== candidate.id);
      devices.push(candidate);
      activeDeviceId = candidate.id;
      saveDevices();
      showAppScreen();
      loadDir('');
    } catch (e) {
      pinError.textContent = e.message === 'unauthorized' ? 'Incorrect PIN' : 'Could not reach server: ' + e.message;
      pinInput.value = '';
      pinInput.focus();
    }
  }

  pinSubmit.addEventListener('click', () => {
    if (pinInput.value.trim()) submitPin(pinInput.value.trim());
  });
  pinInput.addEventListener('keydown', (e) => {
    if (e.key === 'Enter' && pinInput.value.trim()) submitPin(pinInput.value.trim());
  });

  // "⋯" opens a small menu instead of doing one single thing on tap --
  // it used to jump straight to "re-enter PIN" with no explanation, which
  // read as confusing since nothing prompted it. On Android, this menu is
  // also where the native "change PC address"/"reload" actions live now
  // (they used to be a separate floating wrench button overlaid on top of
  // the WebView, which could visually collide with these header icons).
  function openMenuSheet() {
    const bridge = window.AndroidBridge;
    menuChangeAddressBtn.classList.toggle('hidden', !(bridge && bridge.changeServerAddress));
    menuReloadBtn.classList.toggle('hidden', !(bridge && bridge.reloadPage));
    menuSheet.classList.remove('hidden');
  }
  function closeMenuSheet() {
    menuSheet.classList.add('hidden');
  }

  logoutBtn.addEventListener('click', openMenuSheet);

  menuSheet.addEventListener('click', (e) => {
    if (e.target === menuSheet) return closeMenuSheet();
    const action = e.target.dataset.menuAction;
    if (!action) return;
    closeMenuSheet();
    if (action === 'reenter-pin') {
      if (confirm('Re-enter the PIN for the current PC?')) showPinScreen();
    } else if (action === 'change-address') {
      try { window.AndroidBridge.changeServerAddress(); } catch (e) {}
    } else if (action === 'reload') {
      try { window.AndroidBridge.reloadPage(); } catch (e) {}
    }
  });

  function fmtSize(bytes) {
    if (bytes == null) return '';
    const units = ['B', 'KB', 'MB', 'GB', 'TB'];
    let i = 0, n = bytes;
    while (n >= 1024 && i < units.length - 1) { n /= 1024; i++; }
    return `${n.toFixed(n < 10 && i > 0 ? 1 : 0)} ${units[i]}`;
  }

  function renderBreadcrumbs(path) {
    breadcrumbs.innerHTML = '';
    const rootSpan = document.createElement('span');
    rootSpan.textContent = 'Home';
    if (path === '') rootSpan.classList.add('current');
    rootSpan.addEventListener('click', () => loadDir(''));
    breadcrumbs.appendChild(rootSpan);

    if (!path) return;
    const parts = path.split('/');
    let accum = '';
    parts.forEach((part, idx) => {
      accum = accum ? accum + '/' + part : part;
      const sep = document.createElement('span');
      sep.className = 'sep';
      sep.textContent = '›';
      breadcrumbs.appendChild(sep);

      const span = document.createElement('span');
      span.textContent = part;
      const target = accum;
      if (idx === parts.length - 1) span.classList.add('current');
      else span.addEventListener('click', () => loadDir(target));
      breadcrumbs.appendChild(span);
    });
  }

  async function loadDir(path) {
    try {
      const res = await api('/api/list', { params: { path } });
      const data = await res.json();
      currentPath = data.path;
      exitSelectMode();
      renderBreadcrumbs(currentPath);
      lastEntries = data.entries;
      renderList(lastEntries);
      history.replaceState(null, '', '#' + encodeURIComponent(currentPath));
    } catch (e) {
      if (e.message !== 'unauthorized') alert(e.message);
    }
  }

  function renderList(entries) {
    fileList.innerHTML = '';
    fileList.classList.toggle('select-mode', selectMode);
    if (!entries.length) {
      const empty = document.createElement('div');
      empty.className = 'empty-state';
      empty.textContent = 'This folder is empty';
      fileList.appendChild(empty);
      return;
    }
    entries.forEach((entry) => {
      const row = document.createElement('div');
      row.className = 'row';
      const path = entryPath(entry.name);
      if (selectMode && selectedPaths.has(path)) row.classList.add('selected');

      if (selectMode) {
        const checkbox = document.createElement('input');
        checkbox.type = 'checkbox';
        checkbox.className = 'row-checkbox';
        checkbox.checked = selectedPaths.has(path);
        checkbox.addEventListener('click', (e) => e.stopPropagation());
        checkbox.addEventListener('change', () => toggleSelection(path, row));
        row.appendChild(checkbox);
      }

      const icon = document.createElement('div');
      icon.className = 'icon';
      icon.textContent = entry.is_dir ? '📁' : '📄';
      row.appendChild(icon);

      const name = document.createElement('div');
      name.className = 'name';
      name.textContent = entry.name;
      row.appendChild(name);

      if (!entry.is_dir) {
        const meta = document.createElement('div');
        meta.className = 'meta';
        meta.textContent = fmtSize(entry.size);
        row.appendChild(meta);
      }

      if (!selectMode) {
        const more = document.createElement('div');
        more.className = 'more';
        more.textContent = '⋮';
        more.addEventListener('click', (e) => {
          e.stopPropagation();
          openSheet(entry);
        });
        row.appendChild(more);
      }

      row.addEventListener('click', () => {
        if (selectMode) {
          toggleSelection(path, row);
          return;
        }
        if (entry.is_dir) {
          const newPath = currentPath ? currentPath + '/' + entry.name : entry.name;
          loadDir(newPath);
        } else {
          triggerDownload(entry.name);
        }
      });

      fileList.appendChild(row);
    });
  }

  // ---------- multi-select ----------

  function toggleSelection(path, row) {
    if (selectedPaths.has(path)) {
      selectedPaths.delete(path);
      row.classList.remove('selected');
    } else {
      selectedPaths.add(path);
      row.classList.add('selected');
    }
    const checkbox = row.querySelector('.row-checkbox');
    if (checkbox) checkbox.checked = selectedPaths.has(path);
    updateSelectBar();
  }

  function updateSelectBar() {
    const n = selectedPaths.size;
    selectCount.textContent = `${n} selected`;
    selectBar.classList.toggle('hidden', !selectMode || n === 0);
  }

  function exitSelectMode() {
    selectMode = false;
    selectedPaths.clear();
    selectToggleBtn.classList.remove('active');
    selectBar.classList.add('hidden');
  }

  selectToggleBtn.addEventListener('click', () => {
    selectMode = !selectMode;
    selectedPaths.clear();
    selectToggleBtn.classList.toggle('active', selectMode);
    updateSelectBar();
    renderList(lastEntries);
  });

  selectCancelBtn.addEventListener('click', () => {
    exitSelectMode();
    renderList(lastEntries);
  });

  selectDownloadBtn.addEventListener('click', () => {
    if (!selectedPaths.size) return;
    const device = getActiveDevice();
    const params = new URLSearchParams();
    selectedPaths.forEach((p) => params.append('path', p));
    if (device && device.pin) params.set('pin', device.pin);
    window.location.href = `${deviceBase(device)}/api/download-selected?${params.toString()}`;
  });

  selectDeleteBtn.addEventListener('click', async () => {
    if (!selectedPaths.size) return;
    if (!confirm(`Delete ${selectedPaths.size} item(s)? This cannot be undone.`)) return;
    try {
      for (const p of selectedPaths) {
        const body = new URLSearchParams({ path: p });
        await api('/api/delete', { method: 'POST', body });
      }
      exitSelectMode();
      loadDir(currentPath);
    } catch (e) {
      alert(e.message);
    }
  });

  function entryPath(name) {
    return currentPath ? currentPath + '/' + name : name;
  }

  function triggerDownload(name) {
    const url = apiUrl('/api/download', { path: entryPath(name) });
    window.location.href = url;
  }

  function openSheet(entry) {
    sheetTarget = entry;
    sheetTitle.textContent = entry.name;
    actionSheet.classList.remove('hidden');
  }
  function closeSheet() {
    actionSheet.classList.add('hidden');
    sheetTarget = null;
  }

  actionSheet.addEventListener('click', (e) => {
    if (e.target === actionSheet) return closeSheet();
    const action = e.target.dataset.action;
    if (!action) return;
    handleSheetAction(action);
  });

  async function handleSheetAction(action) {
    const entry = sheetTarget;
    closeSheet();
    if (!entry) return;
    const path = entryPath(entry.name);

    if (action === 'download') {
      if (entry.is_dir) {
        window.location.href = apiUrl('/api/download-zip', { path });
      } else {
        triggerDownload(entry.name);
      }
    } else if (action === 'rename') {
      const newName = prompt('New name', entry.name);
      if (newName && newName !== entry.name) {
        try {
          const body = new URLSearchParams({ path, new_name: newName });
          await api('/api/rename', { method: 'POST', body });
          loadDir(currentPath);
        } catch (e) { alert(e.message); }
      }
    } else if (action === 'delete') {
      if (confirm(`Delete "${entry.name}"? This cannot be undone.`)) {
        try {
          const body = new URLSearchParams({ path });
          await api('/api/delete', { method: 'POST', body });
          loadDir(currentPath);
        } catch (e) { alert(e.message); }
      }
    }
  }

  newFolderBtn.addEventListener('click', async () => {
    const name = prompt('Folder name');
    if (!name) return;
    try {
      const body = new URLSearchParams({ path: currentPath, name });
      await api('/api/mkdir', { method: 'POST', body });
      loadDir(currentPath);
    } catch (e) { alert(e.message); }
  });

  uploadBtn.addEventListener('click', () => {
    fileInput.value = '';
    fileInput.click();
  });
  fileInput.addEventListener('change', () => uploadFiles(fileInput.files));

  // long-press upload button to upload a whole folder (webkitdirectory)
  let pressTimer;
  uploadBtn.addEventListener('touchstart', () => {
    pressTimer = setTimeout(() => { folderInput.value = ''; folderInput.click(); }, 600);
  });
  uploadBtn.addEventListener('touchend', () => clearTimeout(pressTimer));
  folderInput.addEventListener('change', () => uploadFiles(folderInput.files));

  async function uploadFiles(fileListObj) {
    if (!fileListObj || !fileListObj.length) return;
    const device = getActiveDevice();
    const formData = new FormData();
    formData.append('path', currentPath);
    Array.from(fileListObj).forEach((f) => {
      const relPath = f.webkitRelativePath || f.name;
      formData.append('files', f, relPath);
    });

    uploadProgress.classList.remove('hidden');
    uploadProgressText.textContent = `Uploading ${fileListObj.length} item(s)...`;
    uploadProgressBar.style.width = '0%';

    try {
      await new Promise((resolve, reject) => {
        const xhr = new XMLHttpRequest();
        xhr.open('POST', apiUrl('/api/upload', {}, device));
        if (device && device.pin) xhr.setRequestHeader('X-PIN', device.pin);
        xhr.upload.onprogress = (e) => {
          if (e.lengthComputable) {
            const pct = Math.round((e.loaded / e.total) * 100);
            uploadProgressBar.style.width = pct + '%';
          }
        };
        xhr.onload = () => (xhr.status >= 200 && xhr.status < 300) ? resolve() : reject(new Error('Upload failed'));
        xhr.onerror = () => reject(new Error('Upload failed'));
        xhr.send(formData);
      });
      loadDir(currentPath);
      if (window.AndroidBridge && window.AndroidBridge.notifyUploadComplete) {
        try {
          window.AndroidBridge.notifyUploadComplete(fileListObj.length);
        } catch (e) {
          // Ignore -- purely a nice-to-have notification.
        }
      }
    } catch (e) {
      alert(e.message);
    } finally {
      uploadProgress.classList.add('hidden');
    }
  }

  // ---------- device info drawer ----------

  let pcListEl = null;
  let addPcBtn = null;
  let phoneCardEl = null;
  let pingInterval = null;
  // True when the drawer is being shown as the mandatory "pick a device"
  // landing screen right after unlocking, rather than a manual ℹ/☰ open on
  // top of an already-loaded file browser -- lets closeDeviceDrawer() know
  // it needs to load *something* if the user dismisses without picking.
  let landingSelectorPending = false;

  function guessOS() {
    const ua = navigator.userAgent;
    if (/android/i.test(ua)) return 'Android';
    if (/iphone|ipad|ipod/i.test(ua)) return 'iOS';
    if (/windows/i.test(ua)) return 'Windows';
    if (/mac os/i.test(ua)) return 'macOS';
    if (/linux/i.test(ua)) return 'Linux';
    return 'Unknown';
  }

  function guessBrowser() {
    const ua = navigator.userAgent;
    if (/edg\//i.test(ua)) return 'Edge';
    if (/chrome|crios/i.test(ua)) return 'Chrome';
    if (/firefox|fxios/i.test(ua)) return 'Firefox';
    if (/safari/i.test(ua)) return 'Safari';
    return 'Browser';
  }

  function guessConnection() {
    const c = navigator.connection || navigator.webkitConnection || navigator.mozConnection;
    if (c && (c.effectiveType || c.type)) return c.effectiveType || c.type;
    return 'Unknown';
  }

  function buildCard(key, title, icon, hint, onOpen) {
    const card = document.createElement('div');
    card.className = 'device-card';
    card.dataset.key = key;
    card.innerHTML = `
      <div class="device-card-title"><span>${icon}</span><span>${title}</span><span class="swap-hint">${hint}</span></div>
      <div class="device-card-body">Loading…</div>
    `;
    card.addEventListener('click', onOpen);
    return card;
  }

  function statRow(label, value) {
    return `<div class="device-stat-row"><span class="stat-label">${label}</span><span class="stat-value">${value}</span></div>`;
  }

  function loadPhoneStats() {
    const phoneBody = phoneCardEl.querySelector('.device-card-body');
    phoneBody.innerHTML =
      statRow('Browser', guessBrowser()) +
      statRow('OS', guessOS()) +
      statRow('Screen', `${screen.width}×${screen.height}`) +
      statRow('Connection', guessConnection());
  }

  function openPhoneDevice() {
    closeDeviceDrawer();
    // The web platform has no API to browse a phone's full file system --
    // the closest equivalent to "opening" it is the native file picker,
    // which lets you browse the phone's own storage to pick files to send.
    fileInput.value = '';
    fileInput.click();
  }

  function renderDeviceList() {
    pcListEl.innerHTML = '';
    devices.forEach((device) => {
      const isActive = device.id === activeDeviceId;
      const row = document.createElement('div');
      row.className = 'device-row' + (isActive ? ' active' : '');
      row.dataset.id = device.id;

      const top = document.createElement('div');
      top.className = 'device-row-top';
      top.innerHTML = `
        <span class="status-dot checking"></span>
        <div class="device-row-info">
          <div class="device-row-name">${escapeHtml(device.name)}${isActive ? ' <span class="current-badge">current</span>' : ''}</div>
          <div class="device-row-address">${escapeHtml(device.address)}</div>
        </div>
      `;
      row.appendChild(top);

      if (devices.length > 1) {
        const removeBtn = document.createElement('button');
        removeBtn.className = 'device-row-remove';
        removeBtn.textContent = '✕';
        removeBtn.title = 'Remove this PC';
        removeBtn.addEventListener('click', (e) => {
          e.stopPropagation();
          removeDevice(device.id);
        });
        top.appendChild(removeBtn);
      }

      if (isActive) {
        const detail = document.createElement('div');
        detail.className = 'device-row-detail';
        detail.textContent = 'Loading…';
        row.appendChild(detail);
      }

      row.addEventListener('click', () => {
        if (device.id !== activeDeviceId) switchToDevice(device);
      });

      pcListEl.appendChild(row);
    });

    loadActiveDeviceStats();
  }

  async function loadActiveDeviceStats() {
    const device = getActiveDevice();
    if (!device) return;
    const detailEl = pcListEl.querySelector(`[data-id="${device.id}"] .device-row-detail`);
    if (!detailEl) return;
    try {
      const res = await api('/api/stats', { device });
      const data = await res.json();
      detailEl.innerHTML =
        statRow('OS', data.platform) +
        statRow('Storage free', `${data.storage.free_human} / ${data.storage.total_human}`);
    } catch (e) {
      detailEl.textContent = 'Could not load stats';
    }
  }

  async function pingAllDevices() {
    devices.forEach(async (device) => {
      const row = pcListEl && pcListEl.querySelector(`[data-id="${device.id}"] .status-dot`);
      if (!row) return;
      // Generous timeout -- this runs right as the drawer opens (including
      // as the very first thing shown after unlocking), when the network
      // stack may not be fully warmed up yet. A too-tight timeout here was
      // showing devices as offline (red) when they were actually online.
      const t = withTimeout(3500);
      try {
        const res = await fetch(apiUrl('/api/ping', {}, device), { signal: t.signal });
        row.className = 'status-dot ' + (res.ok ? 'online' : 'offline');
      } catch (e) {
        row.className = 'status-dot offline';
      } finally {
        t.cancel();
      }
    });
  }

  async function switchToDevice(device) {
    landingSelectorPending = false;
    activeDeviceId = device.id;
    saveDevices();
    closeDeviceDrawer();
    currentPath = '';
    exitSelectMode();
    await loadDir('');
  }

  function removeDevice(id) {
    if (devices.length <= 1) {
      alert('You need at least one saved PC.');
      return;
    }
    if (!confirm('Remove this PC from your list?')) return;
    const wasActive = activeDeviceId === id;
    devices = devices.filter((d) => d.id !== id);
    if (wasActive) {
      activeDeviceId = devices[0].id;
    }
    saveDevices();
    renderDeviceList();
    pingAllDevices();
    if (wasActive) loadDir('');
  }

  async function addPcFlow() {
    const address = prompt("PC address (from its server console), e.g. 192.168.1.50:8000");
    if (!address) return;
    const trimmed = address.trim().replace(/^https?:\/\//, '').replace(/\/+$/, '');
    if (!trimmed) return;

    const pinValue = prompt('PIN for that PC');
    if (!pinValue || !pinValue.trim()) return;

    const candidate = { id: makeDeviceId(), name: trimmed, address: trimmed, pin: pinValue.trim() };
    try {
      const res = await api('/api/stats', { device: candidate });
      const data = await res.json();
      candidate.name = data.hostname || trimmed;
    } catch (e) {
      alert('Could not connect: ' + e.message);
      return;
    }

    devices = devices.filter((d) => d.address !== candidate.address);
    devices.push(candidate);
    saveDevices();
    renderDeviceList();
    pingAllDevices();

    if (confirm(`Added "${candidate.name}". Switch to it now?`)) {
      switchToDevice(candidate);
    }
  }

  function openDeviceDrawer(opts) {
    if (opts && opts.landing) landingSelectorPending = true;
    if (!pcListEl) {
      const pcHeading = document.createElement('div');
      pcHeading.className = 'device-section-label';
      pcHeading.textContent = 'Your PCs';
      deviceCards.appendChild(pcHeading);

      pcListEl = document.createElement('div');
      pcListEl.id = 'pc-device-list';
      deviceCards.appendChild(pcListEl);

      addPcBtn = document.createElement('button');
      addPcBtn.className = 'add-pc-btn';
      addPcBtn.textContent = '+ Add PC';
      addPcBtn.addEventListener('click', addPcFlow);
      deviceCards.appendChild(addPcBtn);

      const phoneHeading = document.createElement('div');
      phoneHeading.className = 'device-section-label';
      phoneHeading.textContent = 'This device';
      deviceCards.appendChild(phoneHeading);

      phoneCardEl = buildCard('phone', 'This phone', '📱', 'tap to open', openPhoneDevice);
      deviceCards.appendChild(phoneCardEl);
    }

    renderDeviceList();
    pingAllDevices();
    loadPhoneStats();
    deviceDrawer.classList.remove('hidden');

    if (pingInterval) clearInterval(pingInterval);
    pingInterval = setInterval(pingAllDevices, 4000);
  }

  function closeDeviceDrawer() {
    deviceDrawer.classList.add('hidden');
    if (pingInterval) {
      clearInterval(pingInterval);
      pingInterval = null;
    }
    // Dismissed the mandatory landing selector without picking a device --
    // fall back to whichever device was already active, same as before this
    // selector existed.
    if (landingSelectorPending) {
      landingSelectorPending = false;
      loadDir('');
    }
  }

  statsBtn.addEventListener('click', openDeviceDrawer);
  drawerCloseBtn.addEventListener('click', closeDeviceDrawer);
  deviceDrawer.addEventListener('click', (e) => {
    if (e.target === deviceDrawer) closeDeviceDrawer();
  });

  // drag & drop (desktop browsers)
  ['dragenter', 'dragover'].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      e.preventDefault();
      if (!appScreen.classList.contains('hidden')) dropHint.classList.remove('hidden');
    });
  });
  ['dragleave', 'drop'].forEach((evt) => {
    document.addEventListener(evt, (e) => {
      e.preventDefault();
      dropHint.classList.add('hidden');
    });
  });
  document.addEventListener('drop', (e) => {
    if (e.dataTransfer.files && e.dataTransfer.files.length) {
      uploadFiles(e.dataTransfer.files);
    }
  });

  // service worker
  if ('serviceWorker' in navigator) {
    navigator.serviceWorker.register('/service-worker.js').catch(() => {});
  }

  // init
  if (getActiveDevice()) {
    showAppScreen();
    // Land on the device selector first (which PC, online or not) rather
    // than jumping straight into whatever folder was last open -- this is
    // also what a returning user sees right after the Android app's
    // biometric unlock, since the web page loads underneath that lock
    // screen and is simply revealed once it succeeds.
    openDeviceDrawer({ landing: true });
  } else {
    showPinScreen();
  }

  window.addEventListener('hashchange', () => {
    const path = decodeURIComponent(location.hash.slice(1));
    if (!appScreen.classList.contains('hidden') && path !== currentPath) loadDir(path);
  });
})();
