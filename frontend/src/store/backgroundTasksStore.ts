import { create } from 'zustand';
import type { BackgroundTask, ChatBackgroundTasks } from '@/types/chat.types';

const EMPTY_TASKS: BackgroundTask[] = [];

interface BackgroundTasksState {
  // Only chats with running work have an entry.
  tasksByChat: Record<string, BackgroundTask[]>;
  setChatTasks: (chatId: string, tasks: BackgroundTask[]) => void;
  // A snapshot from one backend replaces what that backend reported before.
  replaceSnapshot: (entries: ChatBackgroundTasks[], ownsChat: (chatId: string) => boolean) => void;
}

export const useBackgroundTasksStore = create<BackgroundTasksState>((set) => ({
  tasksByChat: {},

  setChatTasks: (chatId, tasks) => {
    set((state) => {
      if (tasks.length === 0 && !(chatId in state.tasksByChat)) return state;
      const next = { ...state.tasksByChat };
      if (tasks.length === 0) delete next[chatId];
      else next[chatId] = tasks;
      return { tasksByChat: next };
    });
  },

  replaceSnapshot: (entries, ownsChat) => {
    set((state) => {
      const next = Object.fromEntries(
        Object.entries(state.tasksByChat).filter(([chatId]) => !ownsChat(chatId)),
      );
      for (const entry of entries) {
        if (entry.tasks.length > 0) next[entry.chat_id] = entry.tasks;
      }
      return { tasksByChat: next };
    });
  },
}));

export function useChatBackgroundTasks(chatId: string | undefined): BackgroundTask[] {
  return useBackgroundTasksStore((state) =>
    chatId ? (state.tasksByChat[chatId] ?? EMPTY_TASKS) : EMPTY_TASKS,
  );
}
