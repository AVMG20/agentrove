import { beforeEach, describe, expect, it } from 'vitest';
import type { BackgroundTask } from '@/types/chat.types';
import { useBackgroundTasksStore } from './backgroundTasksStore';

const shell: BackgroundTask = {
  id: 'b1',
  kind: 'shell',
  title: 'npm run dev',
  tool_id: 't1',
  started_at: 1,
  stoppable: true,
};

beforeEach(() => {
  useBackgroundTasksStore.setState({ tasksByChat: {} });
});

describe('backgroundTasksStore', () => {
  it('keeps entries only for chats with running work', () => {
    const { setChatTasks } = useBackgroundTasksStore.getState();
    setChatTasks('c1', [shell]);
    expect(useBackgroundTasksStore.getState().tasksByChat).toEqual({ c1: [shell] });
    setChatTasks('c1', []);
    expect(useBackgroundTasksStore.getState().tasksByChat).toEqual({});
  });

  it("replaces one backend's chats without touching the other's", () => {
    const { setChatTasks, replaceSnapshot } = useBackgroundTasksStore.getState();
    setChatTasks('local-old', [shell]);
    setChatTasks('cloud-1', [shell]);
    replaceSnapshot([{ chat_id: 'local-new', tasks: [shell] }], (id) => id.startsWith('local'));
    expect(Object.keys(useBackgroundTasksStore.getState().tasksByChat).sort()).toEqual([
      'cloud-1',
      'local-new',
    ]);
  });
});
