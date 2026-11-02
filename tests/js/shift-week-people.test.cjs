const {test} = require('node:test');
const assert = require('node:assert/strict');
const api = require('../../app/static/js/shift-week-people.js');

const people = [
  {volunteer_id: 1, name: 'Åse Ås', sl: true, began: true, week_shifts: 1, busy_days: ['2026-11-02']},
  {volunteer_id: 2, name: 'José Garcia', sl: false, began: false, week_shifts: 0, busy_days: []},
  {volunteer_id: 3, name: 'Øyvind Berg', sl: false, began: true, week_shifts: 0, busy_days: ['2026-11-03']},
];
const none = {query: '', sl: false, unanswered: false, noShift: false};

test('fold ignores case and accents but keeps o-slash and ae', () => {
  assert.equal(api.fold('  José '), 'jose');
  assert.equal(api.fold('Åse'), 'ase');
  assert.equal(api.fold('Øyvind'), 'øyvind');
  assert.equal(api.fold(null), '');
});

test('search is case and accent insensitive and every word must match', () => {
  assert.deepEqual(api.visibleIds(people, {...none, query: 'JOSE'}), [2]);
  assert.deepEqual(api.visibleIds(people, {...none, query: 'ase as'}), [1]);
  assert.deepEqual(api.visibleIds(people, {...none, query: 'xyz'}), []);
  assert.deepEqual(api.visibleIds(people, none), [1, 2, 3]);
});

test('filters combine with each other and with the search', () => {
  assert.deepEqual(api.visibleIds(people, {...none, sl: true}), [1]);
  assert.deepEqual(api.visibleIds(people, {...none, unanswered: true}), [2]);
  assert.deepEqual(api.visibleIds(people, {...none, noShift: true}), [2, 3]);
  assert.deepEqual(api.visibleIds(people, {...none, noShift: true, unanswered: true}), [2]);
  assert.deepEqual(api.visibleIds(people, {...none, noShift: true, query: 'berg'}), [3]);
  assert.deepEqual(api.visibleIds(people, {...none, sl: true, noShift: true}), []);
});

test('active filter and count text', () => {
  assert.equal(api.hasActiveFilter(none), false);
  assert.equal(api.hasActiveFilter({...none, query: '  '}), false);
  assert.equal(api.hasActiveFilter({...none, query: 'a'}), true);
  assert.equal(api.hasActiveFilter({...none, unanswered: true}), true);
  assert.equal(api.countText(2, 5), 'Viser 2 av 5');
});

test('cell marker: placed, answer or nothing', () => {
  const shift = {places: [{volunteer_id: 1}, {volunteer_id: null}], answers: {2: 'kan', 3: 'nei'}};
  assert.deepEqual(api.cellMarker(shift, 1), {placed: true, answer: null});
  assert.deepEqual(api.cellMarker(shift, 2), {placed: false, answer: 'kan'});
  assert.deepEqual(api.cellMarker(shift, 3), {placed: false, answer: 'nei'});
  assert.deepEqual(api.cellMarker(shift, 9), {placed: false, answer: null});
});

test('busy day and answer words', () => {
  assert.equal(api.isBusy(people[0], '2026-11-02'), true);
  assert.equal(api.isBusy(people[0], '2026-11-03'), false);
  assert.equal(api.answerWord('nei'), 'kan ikke');
  assert.equal(api.answerWord('vil'), 'vil');
  assert.equal(api.answerWord('x'), '');
});
