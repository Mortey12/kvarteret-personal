// Drag and drop for the shift plan (/shifts). Works like application-board.js:
// the dragged element is put back where it was, the change is posted to the
// server, and the board is reloaded from the server so it always shows the
// saved state. The server checks every rule; the highlight is only a hint.
(() => {
  const instances = new Map();
  let saving = false;
  let originalNext = null;

  function feedback(message, isError) {
    const target = document.getElementById('shift-board-feedback');
    if (!target) return;
    target.hidden = false;
    target.textContent = message;
    target.classList.toggle('border-kvarteret-red', Boolean(isError));
  }

  function csrfToken() {
    return document.querySelector('meta[name="csrf-token"]')?.content || '';
  }

  function allowedTokens(volunteerId) {
    const poolItem = document.querySelector(`[data-pool-person][data-volunteer-id="${volunteerId}"]`);
    return poolItem ? new Set((poolItem.dataset.allowed || '').split(' ').filter(Boolean)) : null;
  }

  function canDrop(item, lane) {
    if (lane.hasAttribute('data-shift-pool')) return item.hasAttribute('data-assignment-id');
    const tokens = allowedTokens(item.dataset.volunteerId);
    if (!tokens) return true; // Not in the filtered list: let the server decide.
    return tokens.has(`${lane.dataset.groupId}:${lane.dataset.kind}:${lane.dataset.open}`);
  }

  async function post(url, body) {
    const response = await fetch(url, {
      method: 'POST',
      credentials: 'same-origin',
      headers: { 'X-CSRF-Token': csrfToken(), Accept: 'application/json' },
      body: body ? new URLSearchParams(body) : undefined,
    });
    let data = {};
    if (response.headers.get('content-type')?.includes('application/json')) data = await response.json();
    if (!response.ok) {
      throw new Error(typeof data.detail === 'string' ? data.detail : 'Kunne ikke lagre. Oppdater siden og prøv igjen.');
    }
    return data.message || 'Lagret.';
  }

  async function refresh() {
    const response = await fetch(location.href, { credentials: 'same-origin', headers: { Accept: 'text/html' } });
    if (!response.ok) return;
    const fresh = new DOMParser().parseFromString(await response.text(), 'text/html');
    const next = fresh.getElementById('shift-board-root');
    const current = document.getElementById('shift-board-root');
    if (next && current) {
      const scrollPool = current.querySelector('[data-shift-pool]')?.scrollTop || 0;
      current.replaceWith(next);
      const pool = next.querySelector('[data-shift-pool]');
      if (pool) pool.scrollTop = scrollPool;
      initialize();
    }
  }

  async function save(action) {
    if (saving) return;
    saving = true;
    instances.forEach((instance) => instance.option('disabled', true));
    feedback('Lagrer …');
    try {
      const message = await action();
      await refresh();
      feedback(message);
    } catch (error) {
      feedback(error.message || 'Kunne ikke kontakte serveren. Prøv igjen.', true);
    } finally {
      saving = false;
      instances.forEach((instance) => instance.option('disabled', false));
    }
  }

  function highlight(item) {
    instances.forEach((_, lane) => {
      const free = lane.hasAttribute('data-shift-pool') || Number(lane.dataset.free || 0) > 0;
      lane.classList.toggle('application-drop-allowed', free && canDrop(item, lane));
    });
  }

  function clearHighlight() {
    instances.forEach((_, lane) => lane.classList.remove('application-drop-allowed'));
  }

  function initialize() {
    for (const [element, instance] of instances) {
      if (!element.isConnected) { instance.destroy(); instances.delete(element); }
    }
    if (!window.Sortable) return;
    document.querySelectorAll('[data-shift-slot], [data-shift-pool]').forEach((lane) => {
      if (instances.has(lane)) return;
      const isPool = lane.hasAttribute('data-shift-pool');
      instances.set(lane, new Sortable(lane, {
        group: { name: 'shifts', pull: isPool ? 'clone' : true, put: true },
        sort: false, draggable: '[data-draggable]', filter: 'button', preventOnFilter: false,
        animation: 150, delay: 150, delayOnTouchOnly: true, touchStartThreshold: 5,
        ghostClass: 'application-drag-ghost', disabled: saving,
        onStart(event) { originalNext = event.item.nextSibling; highlight(event.item); },
        onMove(event) { return event.to === event.from || canDrop(event.dragged, event.to); },
        onEnd(event) {
          clearHighlight();
          const { item, from, to } = event;
          if (from === to) return;
          // Put the element back; the refreshed board shows the saved result.
          if (event.pullMode === 'clone') item.remove();
          else from.insertBefore(item, originalNext);
          const assignmentId = item.dataset.assignmentId;
          if (to.hasAttribute('data-shift-pool')) {
            save(() => post(`/shifts/assignments/${assignmentId}/delete`));
          } else if (assignmentId) {
            save(() => post(`/shifts/assignments/${assignmentId}/move`, { slot_id: to.dataset.slotId }));
          } else {
            save(() => post('/shifts/assignments', { slot_id: to.dataset.slotId, volunteer_id: item.dataset.volunteerId }));
          }
        },
      }));
    });
  }

  document.addEventListener('click', (event) => {
    const button = event.target.closest('[data-remove-assignment]');
    if (!button) return;
    event.preventDefault();
    save(() => post(`/shifts/assignments/${button.dataset.removeAssignment}/delete`));
  });
  document.addEventListener('DOMContentLoaded', initialize);
  document.addEventListener('htmx:after:process', initialize);
})();
