const {test} = require('node:test');
const assert = require('node:assert/strict');
const place = require('../../app/static/js/shift-week-place.js');
const people = require('../../app/static/js/shift-week-people.js');

test('picker search uses the same folding and word rule as the people panel', () => {
  const rows = [{id: '1', name: 'åse ås'}, {id: '2', name: 'josé garcia'}, {id: '3', name: 'øyvind berg'}];
  assert.deepEqual(place.pickerVisible(rows, ''), ['1', '2', '3']);
  assert.deepEqual(place.pickerVisible(rows, 'JOSE'), ['2']);
  assert.deepEqual(place.pickerVisible(rows, 'ase as'), ['1']);
  assert.deepEqual(place.pickerVisible(rows, 'xyz'), []);
});

test('picker count text says "Ingen treff" for no match', () => {
  assert.equal(place.pickerCountText(0, 5), 'Ingen treff');
  assert.equal(place.pickerCountText(5, 5), '5 personer passer');
  assert.equal(place.pickerCountText(1, 1), '1 person passer');
  assert.equal(place.pickerCountText(2, 5), 'Viser 2 av 5');
});

test('focus goes to the changed place, then the open place, then the grid', () => {
  assert.deepEqual(place.focusSelectors('11', '101'), [
    '[data-place][data-slot-id="11"][data-volunteer-id="101"] [data-place-menu-toggle]',
    '[data-place][data-slot-id="11"] [data-place-open]',
    '[data-place][data-slot-id="11"] [data-place-menu-toggle]',
    '[data-week-grid]',
  ]);
  // After a removal there is no person to look for.
  assert.equal(place.focusSelectors('11', '').length, 3);
  assert.ok(!place.focusSelectors('11', '')[0].includes('volunteer-id'));
});

test('only an html answer is a refreshed week', () => {
  assert.equal(place.isHtml('text/html; charset=utf-8'), true);
  assert.equal(place.isHtml('application/json'), false);
  assert.equal(place.isHtml(null), false);
});

test('first name is the first word of the name', () => {
  assert.equal(people.firstName('Ada Marie Lie'), 'Ada');
  assert.equal(people.firstName('  Ola '), 'Ola');
  assert.equal(people.firstName(''), '');
  assert.equal(people.firstName(null), '');
});

test('person first: "Sett inn <fornavn>" with the warnings, or "Ledig" with the reason', () => {
  const fits = people.placeOffer({ok: 1, w: [['svarte kan ikke', 'red'], ['skift nummer 2 denne uka', 'yellow']]}, 'Ada');
  assert.deepEqual(fits, {
    ok: true,
    label: 'Sett inn Ada',
    lines: [{text: 'svarte kan ikke', level: 'red'}, {text: 'skift nummer 2 denne uka', level: 'yellow'}],
  });
  assert.deepEqual(people.placeOffer({ok: 1, w: []}, 'Ada').lines, []);
  assert.deepEqual(people.placeOffer({ok: 0, why: 'Kun for godkjente skiftledere'}, 'Ada'), {
    ok: false,
    label: 'Ledig',
    lines: [{text: 'Kun for godkjente skiftledere', level: ''}],
  });
  assert.equal(people.placeOffer(undefined, 'Ada'), null);
});
