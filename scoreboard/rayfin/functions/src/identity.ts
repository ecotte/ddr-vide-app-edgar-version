export const SPECIAL_NAMES = ['YaelTheChampion', 'EdgarIsGoingDown', 'The Dancing Queen', 'Bowser'] as const;
export type SpecialName = typeof SPECIAL_NAMES[number];

export const ADJECTIVES = [
  'silly', 'jolly', 'bouncy', 'cheery', 'zippy', 'groovy', 'sparkly', 'peppy',
  'wobbly', 'bubbly', 'giggly', 'fluffy', 'snazzy', 'jazzy', 'perky', 'quirky',
  'sunny', 'happy', 'merry', 'joyful', 'playful', 'sprightly', 'lively', 'jaunty',
  'plucky', 'spunky', 'sassy', 'nifty', 'dapper', 'chirpy', 'cuddly', 'fuzzy',
  'twinkly', 'zany', 'goofy', 'whimsical', 'cheerful', 'radiant', 'brilliant', 'dazzling',
];
export const NOUNS = [
  'eventhouse', 'lakehouse', 'warehouse', 'notebook', 'pipeline', 'onelake', 'dataflow', 'spark',
  'eventstream', 'activator', 'shortcut', 'mirroring', 'report', 'dashboard', 'workspace', 'capacity',
  'dataset', 'table', 'column', 'measure', 'dimension', 'partition', 'schema', 'catalog',
  'query', 'database', 'sqlendpoint', 'deltatable', 'datahub', 'semanticmodel', 'connector', 'gateway',
  'experiment', 'model', 'environment', 'schedule', 'trigger', 'parameter', 'function', 'deployment',
];

function randomInteger(limit: number) {
  const ceiling = Math.floor(0x100000000 / limit) * limit;
  const bytes = new Uint32Array(1);
  do { crypto.getRandomValues(bytes); } while (bytes[0] >= ceiling);
  return bytes[0] % limit;
}

export function newIdentity() {
  const adjective = ADJECTIVES[randomInteger(ADJECTIVES.length)];
  const noun = NOUNS[randomInteger(NOUNS.length)];
  return {
    username: adjective[0].toUpperCase() + adjective.slice(1)
      + noun[0].toUpperCase() + noun.slice(1) + (1000 + randomInteger(9000)),
    pin: String(100000 + randomInteger(900000)),
  };
}
