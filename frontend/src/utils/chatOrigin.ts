import { useSyncExternalStore } from 'react';

// Cloud-owned chat/sandbox IDs for routing API calls to the VPS.
// Flat sets are enough (single-user; IDs unique across backends).
// Hydrated sync at load so deep-links don't fall back to the local backend.
const CHATS_KEY = 'cloud-chat-ids';
const SANDBOXES_KEY = 'cloud-sandbox-ids';

function load(key: string): Set<string> {
  try {
    const raw = localStorage.getItem(key);
    const parsed: unknown = raw ? JSON.parse(raw) : null;
    if (!Array.isArray(parsed)) return new Set();
    return new Set(parsed.filter((id): id is string => typeof id === 'string'));
  } catch {
    return new Set();
  }
}

const listeners = new Set<() => void>();

function notify(): void {
  chatIdsSnapshot = new Set(cloudChatIds);
  for (const listener of listeners) listener();
}

function subscribe(listener: () => void): () => void {
  listeners.add(listener);
  return () => {
    listeners.delete(listener);
  };
}

const cloudChatIds = load(CHATS_KEY);
const cloudSandboxIds = load(SANDBOXES_KEY);
let chatIdsSnapshot: ReadonlySet<string> = new Set(cloudChatIds);

function persist(key: string, ids: Set<string>): void {
  try {
    localStorage.setItem(key, JSON.stringify([...ids]));
  } catch {
    // localStorage unavailable/full — routing still works for this session in-memory.
  }
}

function add(ids: string[], set: Set<string>, key: string): boolean {
  let changed = false;
  for (const id of ids) {
    if (!set.has(id)) {
      set.add(id);
      changed = true;
    }
  }
  if (changed) persist(key, set);
  return changed;
}

export function markCloudChats(ids: string[]): void {
  if (add(ids, cloudChatIds, CHATS_KEY)) notify();
}

export function isCloudChat(id: string): boolean {
  return cloudChatIds.has(id);
}

export function useIsCloudChat(id: string | undefined): boolean {
  return useSyncExternalStore(subscribe, () => !!id && cloudChatIds.has(id));
}

export function useCloudChatIds(): ReadonlySet<string> {
  return useSyncExternalStore(subscribe, () => chatIdsSnapshot);
}

export function markCloudSandboxes(ids: string[]): void {
  add(ids, cloudSandboxIds, SANDBOXES_KEY);
}

export function isCloudSandbox(id: string): boolean {
  return cloudSandboxIds.has(id);
}

export function clearCloudOrigins(): void {
  cloudChatIds.clear();
  cloudSandboxIds.clear();
  persist(CHATS_KEY, cloudChatIds);
  persist(SANDBOXES_KEY, cloudSandboxIds);
  notify();
}
