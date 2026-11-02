// Leader week page (/shifts/week), L1b: search and filters in the people panel, selecting a person to
// see them in the grid, and folding the panel away. Read only; everything comes from the server
// rendered markup and <script type="application/json" id="week-data">. No dependencies.
//
// The pure functions at the top have tests in tests/js/shift-week-people.test.cjs. The DOM part below
// uses delegated listeners on the document, bound once, and keeps its state per page root in a
// WeakMap, so a page that arrives by hx-boost needs no re-initialisation and nothing is bound twice.
(() => {
  const ANSWER_WORDS = { vil: 'vil', kan: 'kan', nei: 'kan ikke' };
  const COUNT_DELAY_MS = 300;

  // Lower case and without accents (e, u and the like). The ring on "å" goes too, so "a" finds "å";
  // "ø" and "æ" are separate letters and stay as typed.
  function fold(text) {
    return String(text || '').normalize('NFD').replace(/[̀-ͯ]/g, '').toLowerCase().trim();
  }

  // Every word of the query must occur in the name. filters: {query, sl, unanswered, noShift}.
  function matchesPerson(person, filters) {
    const name = fold(person.name);
    const words = fold(filters.query).split(/\s+/).filter(Boolean);
    if (!words.every((word) => name.includes(word))) return false;
    if (filters.sl && !person.sl) return false;
    if (filters.unanswered && person.began) return false;
    if (filters.noShift && person.week_shifts > 0) return false;
    return true;
  }

  function visibleIds(people, filters) {
    return people.filter((person) => matchesPerson(person, filters)).map((person) => person.volunteer_id);
  }

  function hasActiveFilter(filters) {
    return Boolean(fold(filters.query) || filters.sl || filters.unanswered || filters.noShift);
  }

  function countText(shown, total) {
    return `Viser ${shown} av ${total}`;
  }

  // What the grid shows in one shift cell for the selected person: whether they are placed there
  // and their answer for that shift (null when they have not answered).
  function cellMarker(shift, volunteerId) {
    const placed = shift.places.some((place) => place.volunteer_id === volunteerId);
    const answer = shift.answers[String(volunteerId)] || null;
    return { placed, answer };
  }

  // Days (YYYY-MM-DD) on which the person is placed in a shift of the shown bar.
  function placedDays(shifts, volunteerId) {
    return new Set(
      shifts.filter((shift) => shift.places.some((place) => place.volunteer_id === volunteerId)).map((shift) => shift.day),
    );
  }

  // "Opptatt": a shift that day in some other bar or group. busy_days holds every shift of the day,
  // so a day where the person is placed in the shown bar says nothing extra (the cell says "Satt opp").
  function isBusy(person, day, placed) {
    return person.busy_days.includes(day) && !placed.has(day);
  }

  function answerWord(answer) {
    return ANSWER_WORDS[answer] || '';
  }

  const api = { fold, matchesPerson, visibleIds, hasActiveFilter, countText, cellMarker, placedDays, isBusy, answerWord };
  if (typeof module === 'object' && module.exports) module.exports = api;
  if (typeof document === 'undefined') return;

  // ---- DOM part ----
  const states = new WeakMap();

  function readData(root) {
    const block = root.querySelector('#week-data');
    if (!block) return null;
    try {
      const data = JSON.parse(block.textContent);
      return Array.isArray(data.people) && Array.isArray(data.shifts) ? data : null;
    } catch (error) {
      return null;
    }
  }

  function getState(root) {
    if (!states.has(root)) {
      const data = readData(root);
      states.set(root, data && {
        root,
        data,
        people: data.people,
        byId: new Map(data.people.map((person) => [person.volunteer_id, person])),
        shifts: new Map(data.shifts.map((shift) => [String(shift.shift_id), shift])),
        selected: null,
        folded: false,
        filters: { query: '', sl: false, unanswered: false, noShift: false },
        countTimer: null,
      });
    }
    return states.get(root);
  }

  // A browser may restore the search text when the user comes back to the page; follow it.
  function syncRestoredSearch(state) {
    const search = state.root.querySelector('[data-people-search]');
    if (search && search.value) {
      state.filters.query = search.value;
      applyFilters(state, 0);
    }
  }

  const $ = (state, selector) => state.root.querySelector(selector);

  // The count is announced politely. Typing updates it after a short pause, and the text is only
  // written when it changed, so a screen reader hears one message per change.
  function writeCount(state, shown) {
    const text = state.root.querySelector('[data-count-text]');
    const empty = state.root.querySelector('[data-people-empty]');
    const next = countText(shown, state.people.length);
    if (text.textContent !== next) text.textContent = next;
    empty.hidden = shown !== 0;
  }

  function applyFilters(state, delay) {
    const ids = new Set(visibleIds(state.people, state.filters));
    state.root.querySelectorAll('[data-person]').forEach((row) => {
      row.hidden = !ids.has(Number(row.dataset.volunteerId));
    });
    state.root.querySelectorAll('[data-filter]').forEach((button) => {
      const key = { sl: 'sl', unanswered: 'unanswered', noshift: 'noShift' }[button.dataset.filter];
      button.setAttribute('aria-pressed', String(state.filters[key]));
    });
    $(state, '[data-people-reset]').hidden = !hasActiveFilter(state.filters);
    clearTimeout(state.countTimer);
    if (delay) state.countTimer = setTimeout(() => writeCount(state, ids.size), delay);
    else writeCount(state, ids.size);
  }

  function node(tag, className, text) {
    const element = document.createElement(tag);
    if (className) element.className = className;
    if (text) element.textContent = text;
    return element;
  }

  function clearGridMarks(state) {
    state.root.querySelectorAll('[data-select-note], [data-busy-note]').forEach((item) => item.remove());
    state.root.querySelectorAll('.week-cell-placed').forEach((cell) => cell.classList.remove('week-cell-placed'));
  }

  function markGrid(state) {
    clearGridMarks(state);
    const person = state.byId.get(state.selected);
    if (!person) return;
    state.root.querySelectorAll('[data-week-grid] [data-cell]').forEach((cell) => {
      const shift = state.shifts.get(cell.dataset.shiftId);
      if (!shift) return;
      const marker = cellMarker(shift, person.volunteer_id);
      if (marker.placed) {
        const note = node('p', 'week-select-note week-select-placed');
        note.setAttribute('data-select-note', 'placed');
        const visible = node('span', '', 'Satt opp');
        visible.setAttribute('aria-hidden', 'true');
        note.append(visible, node('span', 'sr-only', 'Valgt person er satt opp her'));
        cell.classList.add('week-cell-placed');
        cell.prepend(note);
      } else if (marker.answer) {
        const note = node('p', 'week-select-note week-select-answer');
        note.setAttribute('data-select-note', marker.answer);
        const dot = node('span', `week-dot week-dot-${marker.answer}`);
        dot.setAttribute('aria-hidden', 'true');
        note.append(dot, node('span', 'sr-only', 'Valgt persons svar: '));
        if (marker.answer === 'nei') {
          const bang = node('span', '', '! ');
          bang.setAttribute('aria-hidden', 'true');
          note.append(bang);
        }
        note.append(document.createTextNode(answerWord(marker.answer)));
        cell.prepend(note);
      }
    });
    const placed = placedDays(state.data.shifts, person.volunteer_id);
    state.root.querySelectorAll('[data-week-grid] th[data-day]').forEach((head) => {
      if (isBusy(person, head.dataset.day, placed)) {
        const note = node('span', 'week-busy', 'Opptatt');
        note.setAttribute('data-busy-note', '');
        head.append(note);
      }
    });
  }

  function select(state, volunteerId) {
    state.selected = volunteerId;
    state.root.querySelectorAll('[data-person]').forEach((row) => {
      const on = Number(row.dataset.volunteerId) === volunteerId;
      row.dataset.selected = String(on);
      row.querySelector('[data-person-button]').setAttribute('aria-pressed', String(on));
    });
    const line = $(state, '[data-selection]');
    const person = state.byId.get(volunteerId);
    line.hidden = !person;
    $(state, '[data-selection-name]').textContent = person ? person.name : '';
    markGrid(state);
  }

  // After "Fjern valg" the button is gone, so focus goes to the person's own button if it can be
  // reached, else to the search box, else to the fold button.
  function focusAfterClear(state, previousId) {
    const row = state.root.querySelector(`[data-person][data-volunteer-id="${previousId}"]`);
    const target = (row && !row.hidden && !state.folded && row.querySelector('[data-person-button]'))
      || (!state.folded && $(state, '[data-people-search]'))
      || $(state, '[data-people-toggle]');
    if (target) target.focus();
  }

  function clearSelection(state, { moveFocus }) {
    const previous = state.selected;
    if (previous === null) return;
    select(state, null);
    if (moveFocus) focusAfterClear(state, previous);
  }

  function setFolded(state, folded) {
    state.folded = folded;
    $(state, '[data-people-panel-wrap]').hidden = folded;
    $(state, '[data-week-layout]').dataset.peopleFolded = String(folded);
    const button = $(state, '[data-people-toggle]');
    button.setAttribute('aria-expanded', String(!folded));
    button.textContent = folded ? 'Vis folk' : 'Skjul folk';
  }

  function stateFor(event) {
    const root = event.target.closest && event.target.closest('[data-week-root]');
    if (!root) return null;
    const fresh = !states.has(root);
    const state = getState(root);
    if (state && fresh) syncRestoredSearch(state);
    return state;
  }

  function onClick(event) {
    const state = stateFor(event);
    if (!state) return;
    const target = event.target;
    const personButton = target.closest('[data-person-button]');
    if (personButton) {
      const id = Number(personButton.closest('[data-person]').dataset.volunteerId);
      select(state, state.selected === id ? null : id);
      return;
    }
    const filter = target.closest('[data-filter]');
    if (filter) {
      const key = { sl: 'sl', unanswered: 'unanswered', noshift: 'noShift' }[filter.dataset.filter];
      state.filters[key] = !state.filters[key];
      applyFilters(state, 0);
      return;
    }
    if (target.closest('[data-people-reset]')) {
      state.filters = { query: '', sl: false, unanswered: false, noShift: false };
      const search = $(state, '[data-people-search]');
      search.value = '';
      applyFilters(state, 0);
      search.focus();
      return;
    }
    if (target.closest('[data-selection-clear]')) {
      clearSelection(state, { moveFocus: true });
      return;
    }
    if (target.closest('[data-people-toggle]')) setFolded(state, !state.folded);
  }

  function onInput(event) {
    if (!event.target.matches || !event.target.matches('[data-people-search]')) return;
    const state = stateFor(event);
    if (!state) return;
    state.filters.query = event.target.value;
    applyFilters(state, COUNT_DELAY_MS);
  }

  // Escape clears the selection when focus is in the people panel or the grid. In the search box it
  // first belongs to the browser, which empties a search field.
  function onKeydown(event) {
    if (event.key !== 'Escape') return;
    const state = stateFor(event);
    if (!state || state.selected === null) return;
    if (!event.target.closest('[data-people-panel], [data-week-grid]')) return;
    if (event.target.matches('[data-people-search]') && event.target.value) return;
    event.preventDefault();
    clearSelection(state, { moveFocus: false });
  }

  if (!window.shiftWeekPeopleBound) {
    window.shiftWeekPeopleBound = true;
    document.addEventListener('click', onClick);
    document.addEventListener('input', onInput);
    document.addEventListener('keydown', onKeydown);
  }
})();
