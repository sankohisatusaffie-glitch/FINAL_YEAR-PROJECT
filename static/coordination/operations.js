// Site operations: GPS clock-in, QR scanning, the offline queue and chart tooltips.
// No libraries: QR scanning uses the browser's BarcodeDetector, storage uses IndexedDB.
(() => {
  const body = document.body;
  const userId = body.dataset.userId || '';
  const csrfToken = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const newUuid = () => (crypto.randomUUID ? crypto.randomUUID()
    : '10000000-1000-4000-8000-100000000000'.replace(/[018]/g, (c) => (c ^ (crypto.getRandomValues(new Uint8Array(1))[0] & (15 >> (c / 4)))).toString(16)));

  // --- Small helpers --------------------------------------------------------------

  document.querySelectorAll('[data-print]').forEach((button) => button.addEventListener('click', () => window.print()));
  document.querySelectorAll('[data-reload]').forEach((button) => button.addEventListener('click', () => window.location.reload()));
  // Confirmation dialog for destructive or irreversible actions (forms with data-confirm).
  const dialog = document.querySelector('[data-confirm-dialog]');
  document.querySelectorAll('form[data-confirm]').forEach((form) => {
    form.addEventListener('submit', (event) => {
      if (form.dataset.confirmed) { delete form.dataset.confirmed; return; }
      event.preventDefault();
      if (!dialog || typeof dialog.showModal !== 'function') {
        if (window.confirm(form.dataset.confirm)) { form.dataset.confirmed = '1'; form.requestSubmit(event.submitter); }
        return;
      }
      dialog.querySelector('[data-confirm-message]').textContent = form.dataset.confirm;
      dialog.querySelector('[data-confirm-accept]').textContent = form.dataset.confirmLabel || 'Confirm';
      dialog.returnValue = '';
      dialog.addEventListener('close', () => {
        if (dialog.returnValue === 'confirm') { form.dataset.confirmed = '1'; form.requestSubmit(event.submitter); }
      }, { once: true });
      dialog.showModal();
      dialog.querySelector('[data-confirm-accept]').focus();
    });
  });

  // Header project selector: go straight to the chosen project.
  document.querySelectorAll('[data-project-switcher] select').forEach((select) => {
    select.addEventListener('change', () => { if (select.value) select.form.submit(); });
  });
  document.querySelectorAll('[data-client-uuid]').forEach((input) => { input.value = newUuid(); });

  const toast = (message, tone = 'info') => {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      stack.setAttribute('role', 'status');
      stack.setAttribute('aria-live', 'polite');
      body.append(stack);
    }
    const item = document.createElement('div');
    item.className = `toast toast-${tone}`;
    item.textContent = message;
    stack.append(item);
    window.setTimeout(() => item.remove(), 6000);
  };

  // Server messages appear as toasts; the inline copy stays for no-JavaScript visitors.
  const flashStack = document.querySelector('[data-flash-stack]');
  if (flashStack) {
    flashStack.querySelectorAll('.flash').forEach((flash) => {
      const tone = flash.classList.contains('flash-error') ? 'error' : flash.classList.contains('flash-warning') ? 'warning' : 'success';
      toast(flash.textContent.trim(), tone);
    });
    flashStack.classList.add('is-toasted');
  }

  // --- Offline queue (IndexedDB) ------------------------------------------------------

  const DB_NAME = 'buildlink-offline';
  const STORE = 'queue';
  const openDb = () => new Promise((resolve, reject) => {
    const request = indexedDB.open(DB_NAME, 1);
    request.onupgradeneeded = () => request.result.createObjectStore(STORE, { keyPath: 'id' });
    request.onsuccess = () => resolve(request.result);
    request.onerror = () => reject(request.error);
  });
  const withStore = async (mode, work) => {
    const db = await openDb();
    return new Promise((resolve, reject) => {
      const tx = db.transaction(STORE, mode);
      const result = work(tx.objectStore(STORE));
      tx.oncomplete = () => resolve(result?.result ?? result);
      tx.onerror = () => reject(tx.error);
    });
  };
  const allQueued = async () => {
    if (!window.indexedDB) return [];
    const items = await withStore('readonly', (store) => store.getAll());
    return (items || []).filter((item) => item.userId === userId);
  };

  const queueSubmission = async (url, formData, label) => {
    const fields = [];
    formData.set('offline', '1');
    if (!formData.get('client_uuid')) formData.set('client_uuid', newUuid());
    if (!formData.get('captured_at')) formData.set('captured_at', new Date().toISOString());
    formData.forEach((value, name) => { if (!(value instanceof File && value.size === 0)) fields.push([name, value]); });
    await withStore('readwrite', (store) => store.put({ id: formData.get('client_uuid'), url, label, userId, created: Date.now(), fields }));
    updateQueueBadge();
  };

  let flushing = false;
  const flushQueue = async () => {
    if (flushing || !navigator.onLine || !userId) return;
    flushing = true;
    try {
      for (const item of await allQueued()) {
        const formData = new FormData();
        item.fields.forEach(([name, value]) => formData.append(name, value));
        if (csrfToken()) formData.set('csrfmiddlewaretoken', csrfToken());
        let response;
        try {
          response = await fetch(item.url, { method: 'POST', body: formData, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        } catch {
          break; // Still offline; try again later.
        }
        if (response.redirected && new URL(response.url).pathname.startsWith('/login')) {
          toast('Sign in again to send the records saved on this phone.', 'warning');
          break;
        }
        let result = {};
        try { result = await response.json(); } catch { result = {}; }
        if (response.ok) {
          toast(`Synced: ${item.label}. ${result.message || ''}`.trim(), 'success');
        } else {
          toast(`Not saved — ${item.label}: ${result.message || 'the server rejected it.'}`, 'error');
        }
        await withStore('readwrite', (store) => store.delete(item.id));
      }
    } finally {
      flushing = false;
      updateQueueBadge();
      showConnection();
    }
  };

  const updateQueueBadge = async () => {
    let count = 0;
    try { count = (await allQueued()).length; } catch { count = 0; }
    let badge = document.querySelector('.sync-badge');
    if (!count) { badge?.remove(); return; }
    if (!badge) {
      badge = document.createElement('button');
      badge.type = 'button';
      badge.className = 'sync-badge';
      badge.addEventListener('click', flushQueue);
      body.append(badge);
    }
    badge.textContent = `${count} saved offline · ${navigator.onLine ? 'Sync now' : 'waiting for signal'}`;
    document.querySelectorAll('[data-offline-queue-count]').forEach((node) => { node.textContent = `${count} record${count === 1 ? '' : 's'} waiting to sync.`; });
    showConnection();
  };

  const syncStatus = document.querySelector('[data-sync-status]');
  const showConnection = async () => {
    document.querySelectorAll('[data-offline-indicator]').forEach((node) => { node.hidden = navigator.onLine; });
    body.classList.toggle('is-offline', !navigator.onLine);
    if (!syncStatus) return;
    let waiting = 0;
    try { waiting = userId ? (await allQueued()).length : 0; } catch { waiting = 0; }
    syncStatus.classList.toggle('is-offline', !navigator.onLine);
    syncStatus.classList.toggle('is-pending', navigator.onLine && waiting > 0);
    syncStatus.querySelector('[data-sync-label]').textContent = !navigator.onLine ? `Offline${waiting ? ` · ${waiting} saved` : ''}` : waiting ? `${waiting} to sync` : 'Online';
    document.querySelectorAll('[data-sync-strip]').forEach((strip) => {
      strip.classList.toggle('is-offline', !navigator.onLine);
      strip.classList.toggle('is-pending', navigator.onLine && waiting > 0);
      strip.querySelector('[data-sync-strip-text]').textContent = !navigator.onLine
        ? `Offline · ${waiting ? `${waiting} record${waiting === 1 ? '' : 's'} saved on this phone` : 'you can keep working, records will sync later'}`
        : waiting ? `Online · syncing ${waiting} saved record${waiting === 1 ? '' : 's'}…` : 'Online · everything is synced';
    });
  };
  window.addEventListener('online', () => { showConnection(); flushQueue(); });
  window.addEventListener('offline', () => { showConnection(); updateQueueBadge(); });
  showConnection();
  if (userId) {
    updateQueueBadge();
    flushQueue();
    window.setInterval(flushQueue, 60000);
  }

  if ('serviceWorker' in navigator && body.dataset.serviceWorker) {
    navigator.serviceWorker.register(body.dataset.serviceWorker).catch(() => {});
  }
  // Signing out clears saved pages so the next person on this phone cannot open them.
  document.querySelectorAll('[data-logout-form]').forEach((form) => form.addEventListener('submit', () => {
    if (window.caches) caches.keys().then((keys) => keys.forEach((key) => caches.delete(key)));
  }));

  // Forms that keep working offline (daily site reports).
  document.querySelectorAll('form[data-offline-queue]').forEach((form) => {
    form.addEventListener('submit', async (event) => {
      if (event.defaultPrevented || form.dataset.nativeSubmit) return; // Failed validation, or a deliberate fallback.
      event.preventDefault();
      const formData = new FormData(form);
      const label = form.dataset.offlineLabel || 'Form';
      try {
        if (!navigator.onLine) throw new TypeError('offline');
        const response = await fetch(form.action || window.location.href, { method: 'POST', body: formData, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
        const result = await response.json().catch(() => ({}));
        if (response.ok) {
          window.location.assign(result.redirect || window.location.href.replace(/new\/?$/, ''));
          return;
        }
        // Let the server render its validation messages next to each field.
        form.dataset.nativeSubmit = '1';
        form.submit();
      } catch {
        await queueSubmission(form.action || window.location.href, formData, label);
        form.reset();
        form.querySelectorAll('[data-client-uuid]').forEach((input) => { input.value = newUuid(); });
        toast(`${label} saved on this phone. It will be sent automatically when you are back online.`, 'success');
        delete form.dataset.submitting;
        form.querySelectorAll('[aria-disabled="true"]').forEach((button) => button.removeAttribute('aria-disabled'));
      }
    });
  });

  // --- Location -------------------------------------------------------------------------

  const currentPosition = () => new Promise((resolve) => {
    if (!navigator.geolocation) { resolve({ error: 'This device cannot share its location.' }); return; }
    navigator.geolocation.getCurrentPosition(
      (position) => resolve({ latitude: position.coords.latitude, longitude: position.coords.longitude, accuracy: position.coords.accuracy }),
      (error) => resolve({ error: error.code === error.PERMISSION_DENIED ? 'Location is blocked. Allow location for this site in your browser settings.' : 'Could not get your location. Move into the open and try again.' }),
      { enableHighAccuracy: true, timeout: 15000, maximumAge: 30000 },
    );
  });

  document.querySelectorAll('[data-fill-location]').forEach((button) => {
    const status = button.parentElement.querySelector('[data-location-status]');
    button.addEventListener('click', async () => {
      button.setAttribute('aria-disabled', 'true');
      status.textContent = 'Getting your location…';
      const position = await currentPosition();
      button.removeAttribute('aria-disabled');
      if (position.error) { status.textContent = position.error; return; }
      const lat = document.getElementById(button.dataset.latField);
      const lng = document.getElementById(button.dataset.lngField);
      lat.value = position.latitude.toFixed(6);
      lng.value = position.longitude.toFixed(6);
      [lat, lng].forEach((input) => input.dispatchEvent(new Event('input', { bubbles: true })));
      status.textContent = `Location set (accurate to about ${Math.round(position.accuracy)} m).`;
    });
  });

  const postClock = async (endpoint, formData, label) => {
    formData.set('csrfmiddlewaretoken', csrfToken());
    formData.set('client_uuid', newUuid());
    formData.set('captured_at', new Date().toISOString());
    try {
      if (!navigator.onLine) throw new TypeError('offline');
      const response = await fetch(endpoint, { method: 'POST', body: formData, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const result = await response.json().catch(() => ({ ok: false, message: 'Unexpected response from the server.' }));
      return { ...result, offline: false };
    } catch {
      await queueSubmission(endpoint, formData, label);
      return { ok: true, offline: true, message: 'No signal — saved on this phone. It will sync automatically.' };
    }
  };

  // --- Worker GPS clock-in -----------------------------------------------------------

  const selfClock = document.querySelector('[data-clock-self]');
  if (selfClock) {
    const button = selfClock.querySelector('[data-clock-button]');
    const label = selfClock.querySelector('[data-clock-label]');
    const message = selfClock.querySelector('[data-clock-message]');
    const target = selfClock.querySelector('[data-clock-target]');
    button?.addEventListener('click', async () => {
      button.setAttribute('aria-disabled', 'true');
      message.className = 'clock-message';
      message.textContent = 'Checking your location…';
      const position = await currentPosition();
      const [project, site] = target.value.split(':');
      const formData = new FormData();
      formData.set('project', project);
      formData.set('site', site || '');
      formData.set('method', 'gps');
      if (!position.error) {
        formData.set('latitude', position.latitude);
        formData.set('longitude', position.longitude);
        formData.set('accuracy', position.accuracy);
      }
      const result = await postClock(selfClock.dataset.endpoint, formData, 'Clock-in');
      button.removeAttribute('aria-disabled');
      message.textContent = result.ok ? result.message : (position.error && !result.ok ? `${result.message} ${position.error}` : result.message);
      message.classList.add(result.ok ? 'is-success' : 'is-error');
      if (result.ok && !result.offline) {
        if (result.action === 'clock_in') { label.textContent = 'Clock out'; button.classList.add('is-out'); }
        if (result.action === 'clock_out') { label.textContent = 'Done for today'; button.disabled = true; }
      }
    });
  }

  // --- Supervisor QR scanning ---------------------------------------------------------

  const scanner = document.querySelector('[data-clock-scanner]');
  if (scanner) {
    const video = scanner.querySelector('[data-scanner-video]');
    const startButton = scanner.querySelector('[data-scanner-start]');
    const stopButton = scanner.querySelector('[data-scanner-stop]');
    const idle = scanner.querySelector('[data-scanner-idle]');
    const log = scanner.querySelector('[data-scan-log]');
    const manual = scanner.querySelector('[data-scanner-manual]');
    const siteField = scanner.querySelector('[data-clock-site]');
    let stream = null;
    let detector = null;
    let lastCode = '';
    let lastCodeAt = 0;
    let location = null;

    const addLog = (result, text) => {
      const item = document.createElement('li');
      item.className = `scan-entry ${result.ok ? 'is-success' : 'is-error'}`;
      const time = document.createElement('time');
      time.textContent = new Date().toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
      const strong = document.createElement('strong');
      strong.textContent = result.message || text;
      item.append(time, strong);
      log.prepend(item);
      if (navigator.vibrate) navigator.vibrate(result.ok ? 80 : [60, 60, 60]);
    };

    const submitCode = async (code) => {
      if (!location || Date.now() - location.at > 60000) location = { ...(await currentPosition()), at: Date.now() };
      const formData = new FormData();
      formData.set('project', scanner.dataset.project);
      formData.set('site', siteField?.value || '');
      formData.set('method', 'qr');
      formData.set('code', code);
      if (!location.error) {
        formData.set('latitude', location.latitude);
        formData.set('longitude', location.longitude);
        formData.set('accuracy', location.accuracy);
      }
      addLog(await postClock(scanner.dataset.endpoint, formData, 'QR check-in'), code);
    };

    const scanFrame = async () => {
      if (!stream) return;
      try {
        const codes = await detector.detect(video);
        const code = codes[0]?.rawValue;
        if (code && (code !== lastCode || Date.now() - lastCodeAt > 5000)) {
          lastCode = code;
          lastCodeAt = Date.now();
          await submitCode(code);
        }
      } catch { /* Frame not ready yet. */ }
      window.setTimeout(() => window.requestAnimationFrame(scanFrame), 250);
    };

    const supported = 'BarcodeDetector' in window;
    if (!supported) {
      startButton.disabled = true;
      startButton.textContent = 'Camera scanning not supported here';
    }
    startButton.addEventListener('click', async () => {
      try {
        const formats = await window.BarcodeDetector.getSupportedFormats();
        if (!formats.includes('qr_code')) throw new Error('QR codes are not supported by this browser.');
        detector = new window.BarcodeDetector({ formats: ['qr_code'] });
        stream = await navigator.mediaDevices.getUserMedia({ video: { facingMode: 'environment' }, audio: false });
        video.srcObject = stream;
        await video.play();
        idle.hidden = true;
        startButton.hidden = true;
        stopButton.hidden = false;
        scanFrame();
      } catch (error) {
        addLog({ ok: false, message: error.name === 'NotAllowedError' ? 'Camera access was blocked. Allow the camera in your browser settings.' : (error.message || 'Could not start the camera.') });
      }
    });
    stopButton.addEventListener('click', () => {
      stream?.getTracks().forEach((track) => track.stop());
      stream = null;
      idle.hidden = false;
      startButton.hidden = false;
      stopButton.hidden = true;
    });
    manual.addEventListener('submit', async (event) => {
      event.preventDefault();
      const input = manual.querySelector('input[name="code"]');
      if (!input.value.trim()) return;
      await submitCode(input.value.trim());
      input.value = '';
      input.focus();
    });
  }

  // --- Chart tooltips -----------------------------------------------------------------

  document.querySelectorAll('[data-chart]').forEach((chart) => {
    const tooltip = chart.querySelector('.chart-tooltip');
    const frame = chart.querySelector('.chart-frame');
    if (!tooltip || !frame) return;
    const show = (column) => {
      const bar = column.querySelector('.chart-hit').getBoundingClientRect();
      const box = frame.getBoundingClientRect();
      tooltip.querySelector('strong').textContent = column.dataset.tooltipValue;
      tooltip.querySelector('span').textContent = column.dataset.tooltipLabel;
      tooltip.hidden = false;
      const left = Math.min(Math.max(bar.left - box.left + bar.width / 2, 60), box.width - 60);
      tooltip.style.left = `${left}px`;
      column.classList.add('is-active');
    };
    const hide = (column) => { tooltip.hidden = true; column.classList.remove('is-active'); };
    chart.querySelectorAll('.chart-column').forEach((column) => {
      column.addEventListener('pointerenter', () => show(column));
      column.addEventListener('pointerleave', () => hide(column));
      column.addEventListener('focus', () => show(column));
      column.addEventListener('blur', () => hide(column));
    });
  });
})();

// Dashboard site map (Leaflet + OpenStreetMap). Loaded only on dashboards that have mapped sites.
window.addEventListener('load', () => {
  const container = document.querySelector('[data-site-map]');
  const data = document.getElementById('site-map-data');
  if (!container || !data || !window.L) {
    container?.classList.remove('skeleton');
    return;
  }
  const sites = JSON.parse(data.textContent);
  const map = window.L.map(container, { scrollWheelZoom: false, attributionControl: true });
  window.L.tileLayer('https://tile.openstreetmap.org/{z}/{x}/{y}.png', {
    maxZoom: 19, attribution: '&copy; <a href="https://www.openstreetmap.org/copyright">OpenStreetMap</a> contributors',
  }).addTo(map);
  const pin = window.L.divIcon({ className: 'map-pin', html: '<span></span>', iconSize: [28, 28], iconAnchor: [14, 28], popupAnchor: [0, -26] });
  const bounds = [];
  sites.forEach((site) => {
    const popup = document.createElement('div');
    const title = document.createElement('strong');
    title.textContent = site.site;
    const detail = document.createElement('p');
    detail.textContent = `${site.project} · ${site.progress}% complete · ${site.on_site} on site`;
    const link = document.createElement('a');
    link.href = site.url;
    link.textContent = 'Open project →';
    popup.append(title, detail, link);
    window.L.marker([site.lat, site.lng], { icon: pin, title: site.site }).addTo(map).bindPopup(popup);
    window.L.circle([site.lat, site.lng], { radius: site.radius, color: '#E8672A', weight: 1.5, fillColor: '#F5B700', fillOpacity: 0.15 }).addTo(map);
    bounds.push([site.lat, site.lng]);
  });
  if (bounds.length === 1) map.setView(bounds[0], 15);
  else map.fitBounds(bounds, { padding: [40, 40], maxZoom: 15 });
  container.classList.remove('skeleton');
});

// Loading skeletons around images clear once the image arrives (or fails).
document.querySelectorAll('.skeleton img').forEach((img) => {
  const done = () => img.parentElement.classList.remove('skeleton');
  if (img.complete) done();
  else { img.addEventListener('load', done, { once: true }); img.addEventListener('error', done, { once: true }); }
});

// Kanban task board: drag and drop between columns, plus a "Move to" control on every card.
(() => {
  const board = document.querySelector('[data-kanban]');
  if (!board) return;
  const token = () => document.querySelector('input[name="csrfmiddlewaretoken"]')?.value || '';
  const notify = (message, tone) => {
    let stack = document.querySelector('.toast-stack');
    if (!stack) {
      stack = document.createElement('div');
      stack.className = 'toast-stack';
      stack.setAttribute('role', 'status');
      document.body.append(stack);
    }
    const item = document.createElement('div');
    item.className = `toast toast-${tone}`;
    item.textContent = message;
    stack.append(item);
    setTimeout(() => item.remove(), 5000);
  };
  const refreshCounts = () => board.querySelectorAll('[data-kanban-column]').forEach((column) => {
    const cards = column.querySelectorAll('[data-kanban-card]').length;
    column.querySelector('[data-kanban-count]').textContent = cards;
    column.querySelector('[data-kanban-empty]')?.toggleAttribute('hidden', cards > 0);
  });
  const move = async (card, status) => {
    const origin = card.parentElement;
    const target = board.querySelector(`[data-kanban-column="${status}"] [data-kanban-dropzone]`);
    if (!target || target === origin) return;
    target.prepend(card);
    refreshCounts();
    const form = new FormData();
    form.set('status', status);
    form.set('csrfmiddlewaretoken', token());
    try {
      const response = await fetch(card.dataset.moveUrl, { method: 'POST', body: form, credentials: 'same-origin', headers: { 'X-Requested-With': 'XMLHttpRequest' } });
      const result = await response.json();
      if (!response.ok || !result.ok) throw new Error(result.message);
      card.querySelector('.kanban-progress span').style.width = `${result.progress}%`;
      card.querySelector('[data-kanban-move]').value = result.status;
      notify(result.message, 'success');
    } catch (error) {
      origin.prepend(card);
      refreshCounts();
      notify(error.message || 'Could not move the task. Check your connection.', 'error');
    }
  };
  let dragged = null;
  board.addEventListener('dragstart', (event) => {
    dragged = event.target.closest('[data-kanban-card]');
    if (!dragged) return;
    dragged.classList.add('is-dragging');
    event.dataTransfer.effectAllowed = 'move';
  });
  board.addEventListener('dragend', () => {
    dragged?.classList.remove('is-dragging');
    board.querySelectorAll('.is-drop-target').forEach((zone) => zone.classList.remove('is-drop-target'));
    dragged = null;
  });
  board.querySelectorAll('[data-kanban-column]').forEach((column) => {
    column.addEventListener('dragover', (event) => {
      if (!dragged) return;
      event.preventDefault();
      column.classList.add('is-drop-target');
    });
    column.addEventListener('dragleave', (event) => {
      if (!column.contains(event.relatedTarget)) column.classList.remove('is-drop-target');
    });
    column.addEventListener('drop', (event) => {
      event.preventDefault();
      column.classList.remove('is-drop-target');
      if (dragged) move(dragged, column.dataset.kanbanColumn);
    });
  });
  // With JavaScript, the "Move to" select acts straight away instead of submitting the page.
  board.querySelectorAll('.kanban-move').forEach((form) => {
    form.querySelector('button').hidden = true;
    form.querySelector('[data-kanban-move]').addEventListener('change', (event) => move(form.closest('[data-kanban-card]'), event.target.value));
  });
})();
