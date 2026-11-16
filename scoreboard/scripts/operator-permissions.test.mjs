import assert from 'node:assert/strict';
import { test } from 'node:test';
import { getEntityMetadata } from '@microsoft/rayfin-core';
import { schema } from '../.publisher/rayfin/data/schema.js';

const expectedActions = {
  LiveBoard: ['create', 'read', 'update'],
  GameSession: ['create', 'read', 'update'],
  GamePlayerSession: ['create', 'read', 'update'],
  GameSessionMode: ['create', 'read'],
  UserSession: ['read', 'update'],
  SpecialSession: ['read', 'update'],
};

for (const entity of schema) {
  test(`${entity.name} authorizes signed-in app users without a personal email policy`, () => {
    const metadata = getEntityMetadata(entity);
    const roles = metadata.roles;
    assert.ok(roles?.length > 0);
    assert.ok(roles.every(role => role.role === 'authenticated'));
    assert.ok(roles.every(role => role.policy === undefined));
    const actions = [...new Set(roles.flatMap(role => role.actions))].sort();
    assert.deepEqual(actions, expectedActions[entity.name]);
    assert.ok(!actions.includes('delete') && !actions.includes('*'));
    assert.equal(metadata.permissions.anonymous, undefined);
  });
}

for (const name of ['UserSession', 'SpecialSession', 'GamePlayerSession']) {
  test(`${name} only permits relay acknowledgment fields to be updated`, () => {
    const entity = schema.find(value => value.name === name);
    assert.ok(entity);
    const update = getEntityMetadata(entity).roles.filter(role => role.actions.includes('update'));
    assert.equal(update.length, 1);
    assert.deepEqual(update[0].includedFields, ['id', 'eventQueued']);
  });
}

test('session modes stay immutable and historical registration creation stays disabled', () => {
  for (const entity of schema) {
    const actions = getEntityMetadata(entity).roles.flatMap(role => role.actions);
    if (['UserSession', 'SpecialSession'].includes(entity.name)) {
      assert.ok(!actions.includes('create'));
    }
    if (entity.name === 'GameSessionMode') {
      assert.ok(!actions.includes('update'));
    }
  }
});
