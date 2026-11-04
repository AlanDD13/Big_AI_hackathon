import { test } from 'node:test';
import assert from 'node:assert/strict';
import { makeProfile, readProfiles } from '../src/fleet.ts';

const input = { id: 'profile-one', name: 'Warehouse rover', serial: 'TEST0000000001' };
test('EP Core setups require a confirmed-format serial and cannot silently select another robot', () => {
  assert.throws(() => makeProfile({ ...input, serial: '192.168.2.1' }, []), /14 letters/);
  assert.throws(() => makeProfile({ ...input, serial: 'TEST00000000!1' }, []), /14 letters/);
  assert.throws(() => makeProfile({ ...input, name: ' ' }, []), /robot name/);
  const profile = makeProfile({ ...input, serial: ' test0000000001 ' }, []);
  assert.equal(profile.serial, input.serial);
  assert.equal(profile.connection, 'sta');
  assert.equal(profile.transport, 'udp');
  assert.equal(profile.model, 'rmepcore');
  assert.throws(() => makeProfile({ ...input, id: 'other' }, [profile]), /already has/);
  assert.equal(makeProfile({ ...input, name: 'Updated name' }, [profile]).name, 'Updated name');
});
test('saved setup parsing preserves identity and rejects corrupt, duplicate and unsupported configurations', () => {
  const profile = makeProfile(input, []);
  assert.deepEqual(readProfiles(JSON.stringify([profile])), [profile]);
  assert.deepEqual(readProfiles(null), []);
  assert.throws(() => readProfiles('{'));
  assert.throws(() => readProfiles(JSON.stringify([profile, profile])));
  assert.throws(() => readProfiles(JSON.stringify([{ ...profile, transport: 'tcp' }])));
  assert.throws(() => readProfiles(JSON.stringify([{ ...profile, serial: 'AUTOMATIC' }])));
});
