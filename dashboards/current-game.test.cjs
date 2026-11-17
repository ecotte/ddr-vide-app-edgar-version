const { test } = require('node:test');
const assert = require('node:assert/strict');
const { readFileSync } = require('node:fs');
const { createRequire } = require('node:module');
const { JSDOM, VirtualConsole } = createRequire(require.resolve('../scoreboard/package.json'))('jsdom');

const template = readFileSync(require.resolve('./current-game.html'), 'utf8');
const columns = ['Player', 'Outcome', 'Judgment', 'Directions', 'Seconds', 'Sequence', 'EventId', 'GameId', 'DurationSeconds'];
const context = ['', '', '', '[]', null, null, '', 'game-1', 17];
function outcome(player, result, tier, directions, seconds, sequence, id) {
  return [player, result, tier, JSON.stringify(directions), seconds, sequence, id, 'game-1', 17];
}
const sample = [
  context,
  outcome('P1', 'hit', 'perfect', ['Up-Left', 'Up-Right', 'Up-Left'], 1, 1, 'a'),
  outcome('P1', 'hit', 'perfect', ['Up-Left', 'Up-Right'], 1, 1, 'a'),
  outcome('P1', 'miss', 'miss', ['Up-Left'], 7, 3, 'c'),
  outcome('P1', 'hit', 'great', ['Center'], 4, 2, 'b'),
  outcome('P1', 'hit', 'good', [], 11, 4, 'd'),
  outcome('P2', 'hit', 'perfect', ['Down-Right'], 2, 5, 'e')
];

function open(kind, player = '') {
  const errors = [];
  const virtualConsole = new VirtualConsole();
  virtualConsole.on('jsdomError', error => errors.push(error));
  const dom = new JSDOM(template.replace('__KIND__', kind).replace('__PLAYER__', player), {
    runScripts: 'dangerously', virtualConsole
  });
  const send = (rows, names = columns) => {
    dom.window.dispatchEvent(new dom.window.MessageEvent('message', {
      source: dom.window,
      data: { type: 'RTD_VISUAL_BUILDER_DATA', payload: { tables: { PrimaryResult: { columns: names, rows } } } }
    }));
    assert.deepEqual(errors, []);
  };
  return { dom, document: dom.window.document, send };
}

test('heatmap deduplicates events/directions, excludes unknowns, and retains missing lanes', () => {
  const { dom, document, send } = open('heatmap', 'P1');
  send(sample);
  const cells = [...document.querySelectorAll('.cell')];
  assert.equal(cells.length, 5);
  assert.match(cells[0].textContent, /50%1 hits \/ 1 misses/);
  assert.match(cells[1].textContent, /100%1 hits \/ 0 misses/);
  assert.match(cells[2].textContent, /100%1 hits \/ 0 misses/);
  assert.match(cells[3].textContent, /No data/);
  assert.match(document.querySelector('main').textContent, /1 judgments with unknown direction excluded/);
  assert.equal(cells[0].style.gridColumn, '1');
  assert.equal(cells[1].style.gridColumn, '3');
  assert.equal(cells[2].style.gridRow, '2');
  dom.window.close();
});

test('P2 heatmap never includes P1 outcomes', () => {
  const { dom, document, send } = open('heatmap', 'P2');
  send(sample);
  assert.equal(document.querySelectorAll('.cell strong')[0].textContent, 'No data');
  assert.equal(document.querySelectorAll('.cell strong')[4].textContent, '100%');
  dom.window.close();
});

test('judgments count a multi-arrow judgment once and keep tiers separate', () => {
  const { dom, document, send } = open('judgments');
  send(sample);
  assert.match(document.querySelector('main').textContent, /P1 \u00b7 4 judgments/);
  assert.match(document.querySelector('main').textContent, /Perfect: 1 \(25.0%\)/);
  assert.match(document.querySelector('main').textContent, /Miss: 1 \(25.0%\)/);
  assert.match(document.querySelector('main').textContent, /P2 \u00b7 1 judgments/);
  dom.window.close();
});

test('momentum zero-fills five-second bins and excludes misses', () => {
  const { dom, document, send } = open('momentum');
  send(sample);
  const paths = [...document.querySelectorAll('path')];
  assert.equal(paths.length, 2);
  assert.equal(paths[0].getAttribute('d'), 'M 60 14 L 334 228 L 608 121 L 882 228');
  assert.equal(paths[1].getAttribute('d'), 'M 60 121 L 334 228 L 608 228 L 882 228');
  assert.match(document.querySelector('main').textContent, /Successful judgments \/ 5 seconds/);
  assert.equal(document.getElementById('status').textContent, '');
  dom.window.close();
});

test('streaks reorder delayed judgments before calculating current/best', () => {
  const { dom, document, send } = open('streaks');
  send(sample);
  assert.match(document.querySelector('main').textContent, /P1: 1 current \u00b7 2 best/);
  assert.match(document.querySelector('main').textContent, /P2: 1 current \u00b7 1 best/);
  assert.equal(document.querySelectorAll('circle[fill="#e03131"]').length, 1);
  dom.window.close();
});

test('all visuals clear old data on a game reset without inventing accuracy', () => {
  for (const kind of ['heatmap', 'judgments', 'momentum', 'streaks']) {
    const { dom, document, send } = open(kind, 'P1');
    send(sample);
    send([['', '', '', '[]', null, null, '', 'game-2', 0]]);
    assert.match(document.getElementById('context').textContent, /game-2/);
    assert.match(document.getElementById('status').textContent, /Waiting for recognized judgments/);
    assert.doesNotMatch(document.getElementById('visual').textContent, /100%1 hits/);
    assert.equal(document.querySelectorAll('path').length, 0);
    dom.window.close();
  }
});

test('column-object schema works and malformed payloads surface an error then recover', () => {
  const { dom, document, send } = open('heatmap', 'P1');
  send(sample, columns.map(ColumnName => ({ ColumnName })));
  assert.equal(document.querySelectorAll('.cell').length, 5);
  send(sample, ['wrong']);
  assert.match(document.getElementById('status').textContent, /Unable to display.*Missing column/);
  assert.equal(document.querySelectorAll('.cell').length, 0);
  send(sample);
  assert.equal(document.getElementById('status').textContent, '');
  dom.window.close();
});

test('missing timing is explicit, and ungraded judgments remain visible', () => {
  const rows = [context, outcome('P1', 'hit', 'hit', [], null, 1, 'a')];
  const momentum = open('momentum');
  momentum.send(rows);
  assert.match(momentum.document.querySelector('main').textContent, /No timed judgments available/);
  momentum.dom.window.close();
  const judgments = open('judgments');
  judgments.send(rows);
  assert.match(judgments.document.querySelector('main').textContent, /Ungraded: 1 \(100.0%\)/);
  judgments.dom.window.close();
});

test('BAD is a separate hit tier and contributes to pad accuracy and momentum', () => {
  const rows = [
    context,
    outcome('P1', 'hit', 'bad', ['Center'], 1, 1, 'bad'),
    outcome('P1', 'hit', 'perfect', ['Center'], 2, 2, 'perfect')
  ];
  const judgments = open('judgments');
  judgments.send(rows);
  assert.match(judgments.document.querySelector('main').textContent, /Bad: 1 \(50.0%\)/);
  assert.doesNotMatch(judgments.document.querySelector('.summary').textContent, /Ungraded/);
  judgments.dom.window.close();
  const pad = open('heatmap', 'P1');
  pad.send(rows);
  assert.match(pad.document.querySelectorAll('.cell')[2].textContent, /100%2 hits \/ 0 misses/);
  pad.dom.window.close();
  const pace = open('momentum');
  pace.send(rows);
  assert.equal(pace.document.querySelector('path').getAttribute('d'), 'M 60 14 L 334 228 L 608 228 L 882 228');
  pace.dom.window.close();
});

test('BAD leaves streaks unchanged before, during and after a run, including delayed duplicates', () => {
  const { dom, document, send } = open('streaks');
  send([
    context,
    outcome('P1', 'hit', 'bad', [], 0, 1, 'initial-bad'),
    outcome('P1', 'hit', 'perfect', ['Center'], 1, 2, 'perfect'),
    outcome('P1', 'hit', 'good', ['Center'], 3, 4, 'good'),
    outcome('P1', 'miss', 'miss', [], 4, 5, 'miss'),
    outcome('P1', 'hit', 'bad', [], 5, 6, 'last-bad'),
    outcome('P1', 'hit', 'bad', ['Center'], 2, 3, 'delayed-bad'),
    outcome('P1', 'hit', 'bad', ['Center'], 2, 3, 'delayed-bad')
  ]);
  assert.match(document.querySelector('main').textContent, /P1: 0 current \u00b7 2 best/);
  assert.equal(document.querySelectorAll('circle[fill="#e03131"]').length, 1);
  dom.window.close();
});
