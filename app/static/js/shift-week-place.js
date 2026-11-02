// Leader week page (/shifts/week), L2a: placing people by clicking. A place is put, swapped or removed
// with one click each; every action is one request that saves at once, and the answer is the refreshed
// week (grid, chips, status and people list) plus a short message. The server decides every rule; this
// file only opens the picker, sends the request, swaps the answer in and moves focus.
//
// Like shift-week-people.js: pure functions at the top (tests in tests/js/shift-week-place.test.cjs),
// delegated listeners bound once on the document, nothing to initialise after an hx-boost navigation.
// The selection, search text, filters and fold of shift-week-people.js are untouched: it is told with a
// "week:refreshed" event and reads the new week-data block itself.
(() => {
  const people = typeof module === 'object' && module.exports
    ? require('./shift-week-people.js')
    : (typeof window !== 'undefined' ? window.shiftWeekPeople : null);

  // Picker search: the same folding and word matching as the people panel.
  function pickerVisible(rows, query) {
    return rows.filter((row) => people.matchesPerson({ name: row.name }, { query })).map((row) => row.id);
  }

  function pickerCountText(shown, total) {
    if (shown === 0) return 'Ingen treff';
    if (shown === total) return `${total} ${total === 1 ? 'person' : 'personer'} passer`;
    return `Viser ${shown} av ${total}`;
  }

  // Where focus goes after an action: the changed place. A placed person has the "Endre" button; a
  // place left empty has its "Ledig" button. The grid itself is the last resort.
  function focusSelectors(slotId, volunteerId) {
    const place = `[data-place][data-slot-id="${slotId}"]`;
    const list = [];
    if (volunteerId) list.push(`${place}[data-volunteer-id="${volunteerId}"] [data-place-menu-toggle]`);
    list.push(`${place} [data-place-open]`, `${place} [data-place-menu-toggle]`, '[data-week-grid]');
    return list;
  }

  function isHtml(contentType) {
    return String(contentType || '').toLowerCase().includes('text/html');
  }

  const api = { pickerVisible, pickerCountText, focusSelectors, isHtml };
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof document === 'undefined' || !people) return;

  // ---- DOM part ----
  const SWAPS = ['[data-week-grid]', '[data-week-status-badge]', '[data-week-chips]', '[data-people-list]'];
  const triggers = new WeakMap(); // dialog -> the button that opened it
  let busy = false;

  const csrfToken = () => document.querySelector('meta[name="csrf-token"]')?.content || '';
  const rootOf = (element) => element.closest('[data-week-root]');

  function say(root, text, level) {
    const live = root.querySelector('[data-week-live]');
    if (!live) return;
    live.dataset.level = level || 'ok';
    live.textContent = '';
    // Written a moment later so a screen reader sees the region change, also for the same text twice.
    setTimeout(() => { live.textContent = text; }, 30);
  }

  function focusChanged(root, slotId, volunteerId) {
    for (const selector of focusSelectors(slotId, volunteerId)) {
      const target = root.querySelector(selector);
      if (target) {
        if (!target.matches('button, a, input, [tabindex]')) target.setAttribute('tabindex', '-1');
        target.focus();
        return;
      }
    }
  }

  // Swap the changed parts of a rendered week (a fragment or a whole page) into the page.
  function applyWeek(root, doc) {
    const grid = root.querySelector('[data-week-grid]');
    const scroll = grid ? { left: grid.scrollLeft, top: grid.scrollTop } : null;
    SWAPS.forEach((selector) => {
      const next = doc.querySelector(selector);
      const current = root.querySelector(selector);
      if (next && current) current.replaceWith(document.importNode(next, true));
    });
    const data = doc.querySelector('#week-data');
    const block = root.querySelector('#week-data');
    if (data && block) block.textContent = data.textContent;
    const fresh = root.querySelector('[data-week-grid]');
    if (fresh && scroll) { fresh.scrollLeft = scroll.left; fresh.scrollTop = scroll.top; }
    root.dispatchEvent(new CustomEvent('week:refreshed', { bubbles: true }));
  }

  async function reloadWeek(root) {
    const url = `/shifts/week?week=${encodeURIComponent(root.dataset.weekStart)}&group=${encodeURIComponent(root.dataset.groupId)}`;
    try {
      const response = await fetch(url, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
      if (response.ok) applyWeek(root, new DOMParser().parseFromString(await response.text(), 'text/html'));
    } catch (error) { /* the message already says what happened */ }
  }

  function dialogOf(root) { return root.querySelector('[data-picker]'); }

  function closePicker(dialog, restoreFocus) {
    dialog.dataset.skipFocus = restoreFocus ? '' : '1';
    if (dialog.open) dialog.close(); else dialog.dispatchEvent(new Event('close'));
  }

  // The browser closes a modal dialog on Escape and fires "close": focus goes back to the trigger.
  function onDialogClose(event) {
    const dialog = event.target;
    if (!dialog.matches || !dialog.matches('[data-picker]')) return;
    const trigger = triggers.get(dialog);
    if (trigger) trigger.setAttribute('aria-expanded', 'false');
    const skip = dialog.dataset.skipFocus === '1';
    dialog.dataset.skipFocus = '';
    triggers.delete(dialog);
    if (!skip && trigger && trigger.isConnected) trigger.focus();
  }

  async function openPicker(root, trigger, slotId, assignmentId) {
    if (busy) return;
    const dialog = dialogOf(root);
    if (!dialog) return;
    trigger.setAttribute('aria-expanded', 'true');
    triggers.set(dialog, trigger);
    const query = `slot_id=${encodeURIComponent(slotId)}${assignmentId ? `&replace=${encodeURIComponent(assignmentId)}` : ''}`;
    let response;
    try {
      response = await fetch(`/shifts/week/picker?${query}`, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
    } catch (error) {
      trigger.setAttribute('aria-expanded', 'false');
      say(root, 'Kunne ikke kontakte serveren. Prøv igjen.', 'error');
      return;
    }
    if (!response.ok) {
      trigger.setAttribute('aria-expanded', 'false');
      triggers.delete(dialog);
      let detail = 'Kunne ikke åpne lista. Oppdater siden og prøv igjen.';
      try { detail = (await response.json()).detail || detail; } catch (error) { /* keep the default */ }
      say(root, `Ikke åpnet: ${detail}`, 'error');
      if (response.status === 409) await reloadWeek(root);
      return;
    }
    dialog.querySelector('[data-picker-body]').innerHTML = await response.text();
    if (typeof dialog.showModal === 'function') dialog.showModal(); else dialog.setAttribute('open', '');
    dialog.querySelector('[data-picker-search]')?.focus();
  }

  async function send(root, url, body, opener) {
    if (busy) return;
    busy = true;
    const dialog = dialogOf(root);
    try {
      const response = await fetch(url, {
        method: 'POST',
        credentials: 'same-origin',
        headers: { 'X-CSRF-Token': csrfToken(), 'X-Week-Fragment': '1', Accept: 'text/html' },
        body: new URLSearchParams(body),
      });
      if (isHtml(response.headers.get('content-type'))) {
        const doc = new DOMParser().parseFromString(await response.text(), 'text/html');
        const refresh = doc.querySelector('[data-week-refresh]');
        if (dialog && dialog.open) closePicker(dialog, false);
        if (refresh) {
          applyWeek(root, doc);
          say(root, doc.querySelector('[data-live-text]')?.textContent || '', refresh.dataset.level);
          focusChanged(root, refresh.dataset.focusSlot, refresh.dataset.focusVolunteer);
          return;
        }
      }
      let detail = 'Kunne ikke lagre. Oppdater siden og prøv igjen.';
      try { detail = (await response.json()).detail || detail; } catch (error) { /* keep the default */ }
      if (dialog && dialog.open) closePicker(dialog, true);
      say(root, `Ikke lagret: ${detail}`, 'error');
    } catch (error) {
      say(root, 'Kunne ikke kontakte serveren. Prøv igjen.', 'error');
    } finally {
      busy = false;
    }
  }

  function closeMenus(root, except) {
    root.querySelectorAll('[data-place-menu]').forEach((menu) => {
      if (menu === except) return;
      menu.hidden = true;
      const toggle = root.querySelector(`[aria-controls="${menu.id}"]`);
      if (toggle) toggle.setAttribute('aria-expanded', 'false');
    });
  }

  function onClick(event) {
    const root = event.target.closest && rootOf(event.target);
    if (!root) return;
    const target = event.target;
    const open = target.closest('[data-place-open]');
    if (open) {
      if (open.dataset.offerVolunteer) {
        send(root, '/shifts/week/place', {
          slot_id: open.dataset.slotId,
          volunteer_id: open.dataset.offerVolunteer,
          group: root.dataset.groupId,
          week: root.dataset.weekStart,
        }, open);
      } else {
        openPicker(root, open, open.dataset.slotId, null);
      }
      return;
    }
    const swap = target.closest('[data-place-swap]');
    if (swap) { openPicker(root, swap, swap.dataset.slotId, swap.dataset.assignmentId); return; }
    const toggle = target.closest('[data-place-menu-toggle]');
    if (toggle) {
      const menu = root.querySelector(`#${toggle.getAttribute('aria-controls')}`);
      const show = menu.hidden;
      closeMenus(root, menu);
      menu.hidden = !show;
      toggle.setAttribute('aria-expanded', String(show));
      return;
    }
    if (target.closest('[data-picker-close]')) {
      closePicker(dialogOf(root), true);
    }
  }

  function onSubmit(event) {
    const form = event.target;
    if (!form.matches || !form.matches('[data-place-form]')) return;
    const root = rootOf(form);
    if (!root) return;
    event.preventDefault();
    send(root, form.action, new FormData(form), form);
  }

  function onInput(event) {
    if (!event.target.matches || !event.target.matches('[data-picker-search]')) return;
    const content = event.target.closest('[data-picker-content]');
    const rows = Array.from(content.querySelectorAll('[data-picker-row]'));
    const visible = new Set(pickerVisible(rows.map((row) => ({ id: row.dataset.volunteerId, name: row.dataset.name })), event.target.value));
    rows.forEach((row) => { row.hidden = !visible.has(row.dataset.volunteerId); });
    const text = pickerCountText(visible.size, rows.length);
    const none = content.querySelector('[data-picker-none]');
    const count = content.querySelector('[data-picker-count-text]');
    if (none) none.hidden = visible.size !== 0 || rows.length === 0;
    if (count) count.textContent = visible.size === 0 ? '' : text;
  }

  // Escape closes an open place menu and returns focus to its button. (The picker is a modal dialog: the
  // browser closes it on Escape and onDialogClose returns focus.)
  function onKeydown(event) {
    if (event.key !== 'Escape' || !event.target.closest) return;
    const root = rootOf(event.target);
    const inMenu = event.target.closest('[data-place-menu]');
    const expanded = event.target.closest('[data-place-menu-toggle][aria-expanded="true"]');
    if (!root || !(inMenu || expanded)) return;
    const menu = inMenu || root.querySelector(`#${expanded.getAttribute('aria-controls')}`);
    const toggle = root.querySelector(`[aria-controls="${menu.id}"]`);
    closeMenus(root, null);
    if (toggle) toggle.focus();
    event.preventDefault();
  }

  if (!window.shiftWeekPlaceBound) {
    window.shiftWeekPlaceBound = true;
    document.addEventListener('click', onClick);
    document.addEventListener('submit', onSubmit);
    document.addEventListener('input', onInput);
    document.addEventListener('keydown', onKeydown);
    document.addEventListener('close', onDialogClose, true);
  }
})();
