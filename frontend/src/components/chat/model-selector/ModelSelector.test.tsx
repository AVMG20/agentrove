// @vitest-environment jsdom

import { cleanup, fireEvent, render, screen } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';
import type { Model } from '@/types/chat.types';
import type { DropdownItemType } from '@/components/ui/primitives/Dropdown/Dropdown';
import { useModelStore } from '@/store/modelStore';
import { ModelSelector } from './ModelSelector';

vi.mock('@/hooks/useIsSplitMode', () => ({ useIsSplitMode: () => false }));
vi.mock('@/hooks/queries/useModelQueries', () => ({
  useModelsQuery: () => ({ data: models, isLoading: false }),
}));
vi.mock('@/components/ui/primitives/Dropdown/Dropdown', () => ({
  Dropdown: ({
    items,
    value,
    onSelect,
  }: {
    items: DropdownItemType<Model>[];
    value: Model;
    onSelect: (model: Model) => void;
  }) => (
    <div>
      <span>{value.name}</span>
      <div data-testid="choices">
        {items.map(
          (item) =>
            item.type === 'item' && (
              <button key={item.data.model_id} onClick={() => onSelect(item.data)}>
                {item.data.model_id}
              </button>
            ),
        )}
      </div>
    </div>
  ),
}));

const models: Model[] = [
  {
    model_id: 'opencode:opencode/minimax-m3.1-flash-free',
    name: 'MiniMax M3.1 Flash',
    agent_kind: 'opencode',
    context_window: 1_000_000,
  },
  {
    model_id: 'opencode:opencode/minimax-m2.7',
    name: 'MiniMax M2.7',
    agent_kind: 'opencode',
    context_window: 204_800,
    legacy: true,
  },
  {
    model_id: 'opencode:opencode/minimax-m3',
    name: 'MiniMax M3',
    agent_kind: 'opencode',
    context_window: 512_000,
  },
  {
    model_id: 'opencode:custom/minimax-private',
    name: 'Custom',
    agent_kind: 'opencode',
    context_window: null,
  },
  { model_id: 'haiku', name: 'Haiku', agent_kind: 'claude', context_window: 200_000 },
];

beforeEach(() => useModelStore.setState({ modelByChat: {}, favoriteModelIds: [] }));
afterEach(cleanup);

function openPicker(selectedModelId = models[0].model_id) {
  const onModelChange = vi.fn();
  render(<ModelSelector selectedModelId={selectedModelId} onModelChange={onModelChange} />);
  return onModelChange;
}

describe('MiniMax choices', () => {
  it('offers Flash before M3, hides legacy, and keeps custom and other providers', () => {
    const onModelChange = openPicker();
    const choices = screen.getByTestId('choices');
    expect(
      Array.from(choices.querySelectorAll('button')).map((button) => button.textContent),
    ).toEqual([models[0].model_id, models[2].model_id, models[3].model_id, models[4].model_id]);
    fireEvent.click(screen.getByText(models[2].model_id));
    expect(onModelChange).toHaveBeenCalledWith(models[2].model_id);
  });

  it('retains an explicitly saved legacy selection', () => {
    openPicker(models[1].model_id);
    expect(screen.getByText(models[1].model_id)).toBeTruthy();
    expect(screen.getByText('MiniMax M2.7')).toBeTruthy();
  });

  it('retains a saved legacy favorite without changing it', () => {
    useModelStore.setState({ favoriteModelIds: [models[1].model_id] });
    openPicker();
    expect(screen.getByText(models[1].model_id)).toBeTruthy();
    expect(useModelStore.getState().favoriteModelIds).toEqual([models[1].model_id]);
  });
});
