import path from 'path';
import { existsSync, readFileSync } from 'fs';

export function normalizeWhatsAppIdentifier(value) {
  return String(value || '')
    .trim()
    .replace(/:.*@/, '@')
    .replace(/@.*/, '')
    .replace(/^\+/, '');
}

export function parseAllowedUsers(rawValue) {
  return new Set(
    String(rawValue || '')
      .split(',')
      .map((value) => normalizeWhatsAppIdentifier(value))
      .filter(Boolean)
  );
}

export function parseAllowedGroups(rawValue) {
  const groups = new Set();
  const normalizedGroups = new Set();

  for (const entry of String(rawValue || '').split(',')) {
    const value = String(entry || '').trim();
    if (!value) {
      continue;
    }

    groups.add(value);

    if (value !== '*') {
      const normalized = normalizeWhatsAppIdentifier(value);
      if (normalized) {
        normalizedGroups.add(normalized);
      }
    }
  }

  return { groups, normalizedGroups };
}

export function parseAllowAllFlag(rawValue) {
  const value = String(rawValue || '').trim().toLowerCase();
  return value === '1' || value === 'true' || value === 'yes';
}

function readMappingFile(sessionDir, identifier, suffix = '') {
  const filePath = path.join(sessionDir, `lid-mapping-${identifier}${suffix}.json`);
  if (!existsSync(filePath)) {
    return null;
  }

  try {
    const parsed = JSON.parse(readFileSync(filePath, 'utf8'));
    const normalized = normalizeWhatsAppIdentifier(parsed);
    return normalized || null;
  } catch {
    return null;
  }
}

export function expandWhatsAppIdentifiers(identifier, sessionDir) {
  const normalized = normalizeWhatsAppIdentifier(identifier);
  if (!normalized) {
    return new Set();
  }

  // Walk both phone->LID and LID->phone mapping files so allowlists can use
  // either form transparently in bot mode.
  const resolved = new Set();
  const queue = [normalized];

  while (queue.length > 0) {
    const current = queue.shift();
    if (!current || resolved.has(current)) {
      continue;
    }

    resolved.add(current);

    for (const suffix of ['', '_reverse']) {
      const mapped = readMappingFile(sessionDir, current, suffix);
      if (mapped && !resolved.has(mapped)) {
        queue.push(mapped);
      }
    }
  }

  return resolved;
}

export function matchesAllowedUser(senderId, allowedUsers, sessionDir) {
  // Empty allowlist = NO ONE allowed (secure default, #8389).  Operators
  // who want an open bot must set ``WHATSAPP_ALLOWED_USERS=*`` explicitly.
  // Previous behaviour (empty → return true) let any stranger DM the
  // bridge and trigger a Python-side pairing-code reply.
  if (!allowedUsers || allowedUsers.size === 0) {
    return false;
  }

  // "*" means allow everyone (consistent with SIGNAL_GROUP_ALLOWED_USERS)
  if (allowedUsers.has('*')) {
    return true;
  }

  const aliases = expandWhatsAppIdentifiers(senderId, sessionDir);
  for (const alias of aliases) {
    if (allowedUsers.has(alias)) {
      return true;
    }
  }

  return false;
}

export function isSenderAllowed({
  senderId,
  chatId,
  isGroup,
  allowedUsers,
  allowedGroups,
  normalizedAllowedGroups,
  allowAllUsers,
  sessionDir,
}) {
  if (allowAllUsers) {
    return true;
  }

  if (isGroup) {
    if (allowedGroups?.has('*')) {
      return true;
    }

    const rawChatId = String(chatId || '').trim();
    const normalizedChatId = normalizeWhatsAppIdentifier(rawChatId);

    if ((rawChatId && allowedGroups?.has(rawChatId)) || (normalizedChatId && normalizedAllowedGroups?.has(normalizedChatId))) {
      return true;
    }
  }

  return matchesAllowedUser(senderId, allowedUsers, sessionDir);
}
