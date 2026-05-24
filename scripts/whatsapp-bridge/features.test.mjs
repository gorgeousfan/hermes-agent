import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';

const here = dirname(fileURLToPath(import.meta.url));
const bridgeSource = readFileSync(join(here, 'bridge.js'), 'utf8');

function assertSourceContains(token) {
  assert.match(bridgeSource, new RegExp(token.replace(/[.*+?^${}()|[\]\\]/g, '\\$&')));
}

test('bridge exposes expanded WhatsApp feature endpoints', () => {
  for (const endpoint of [
    "app.post('/send-buttons'",
    "app.post('/send-list'",
    "app.post('/send-poll'",
    "app.post('/presence'",
    "app.get('/groups'",
    "app.get('/groups/:id/participants'",
    "app.get('/lid-map'",
    "app.get('/account'",
    "app.get('/labels'",
  ]) {
    assertSourceContains(endpoint);
  }
});

test('button and list replies are converted into slash-command text', () => {
  for (const token of [
    'buttonsResponseMessage',
    'templateButtonReplyMessage',
    'listResponseMessage',
    'approvalActionText',
    'hermes_approve_once',
    '/approve ${approvalId}',
    '/approve session',
    '/deny',
  ]) {
    assertSourceContains(token);
  }
});

test('poll creation and poll update messages are represented in bridge events', () => {
  for (const token of [
    'pollCreationMessage',
    'pollCreationMessageV3',
    'pollUpdateMessage',
    'poll.vote',
    'selectedOptions',
    'approvalActions',
    'resolvePollApprovalAction',
  ]) {
    assertSourceContains(token);
  }
});
