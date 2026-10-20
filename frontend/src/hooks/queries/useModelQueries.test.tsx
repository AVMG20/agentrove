// @vitest-environment jsdom

import { cleanup, renderHook } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Model } from '@/types/chat.types';
import { useModelStore } from '@/store/modelStore';
import { useModelSelection } from './useModelQueries';

const h = vi.hoisted(() => ({ models: [] as Model[] }));
vi.mock('@tanstack/react-query', () => ({ useQuery: () => ({ data: h.models }) }));

const legacy: Model = {
  model_id: 'opencode:opencode/minimax-m2.7',
  name: 'MiniMax M2.7',
  agent_kind: 'opencode',
  context_window: 204_800,
  legacy: true,
};
const flash: Model = {
  model_id: 'opencode:opencode/minimax-m3.1-flash-free',
  name: 'MiniMax M3.1 Flash',
  agent_kind: 'opencode',
  context_window: 1_000_000,
};
const other: Model = {
  model_id: 'haiku',
  name: 'Haiku',
  agent_kind: 'claude',
  context_window: 200_000,
};

beforeEach(() => {
  h.models = [legacy, flash];
  useModelStore.setState({ modelByChat: {}, favoriteModelIds: [] });
});
afterEach(cleanup);

describe('model defaults and saved selections', () => {
  it('skips legacy entries when assigning an ordinary default', () => {
    const { result } = renderHook(() => useModelSelection({ chatId: 'new' }));
    expect(result.current.selectedModelId).toBe(flash.model_id);
  });

  it('preserves a persisted legacy selection', () => {
    useModelStore.setState({ modelByChat: { saved: legacy.model_id } });
    const { result } = renderHook(() => useModelSelection({ chatId: 'saved' }));
    expect(result.current.selectedModelId).toBe(legacy.model_id);
  });

  it('restores legacy history without replacing it with the default', () => {
    const { result } = renderHook(() =>
      useModelSelection({ chatId: 'history', initialModelId: legacy.model_id }),
    );
    expect(result.current.selectedModelId).toBe(legacy.model_id);
  });

  it('does not assign a default while history is loading', () => {
    renderHook(() => useModelSelection({ chatId: 'loading', initialModelId: null }));
    expect(useModelStore.getState().modelByChat.loading).toBeUndefined();
  });

  it('preserves non-MiniMax default ordering', () => {
    h.models = [other, legacy, flash];
    const { result } = renderHook(() => useModelSelection({ chatId: 'other' }));
    expect(result.current.selectedModelId).toBe(other.model_id);
  });

  it('leaves a custom MiniMax identifier eligible without a legacy flag', () => {
    h.models = [{ ...flash, model_id: 'opencode:custom/minimax-private' }, flash];
    const { result } = renderHook(() => useModelSelection({ chatId: 'custom' }));
    expect(result.current.selectedModelId).toBe('opencode:custom/minimax-private');
  });
});
