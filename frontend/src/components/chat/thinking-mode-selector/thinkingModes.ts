import { queryKeys } from '@/hooks/queries/queryKeys';
import { useModelsQuery } from '@/hooks/queries/useModelQueries';
import { queryClient } from '@/lib/queryClient';
import type { AgentKind, Model } from '@/types/chat.types';

export interface ThinkingModeOption {
  value: string;
  label: string;
}

const THINKING_MODE_ORDER = ['none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max', 'ultra'];

const THINKING_MODE_LABELS: Record<string, string> = {
  none: 'None',
  minimal: 'Minimal',
  low: 'Low',
  medium: 'Medium',
  high: 'High',
  xhigh: 'XHigh',
  max: 'Max',
  ultra: 'Ultra',
};

const EMPTY_THINKING_MODES: ThinkingModeOption[] = [];

// "default" sends no effort so OpenCode keeps its own provider default.
const OPENCODE_DEFAULT_THINKING_MODE: ThinkingModeOption = { value: 'default', label: 'Default' };

const DEFAULT_BY_AGENT: Record<AgentKind, string> = {
  antigravity: 'high',
  claude: 'high',
  codex: 'high',
  copilot: 'high',
  cursor: 'high',
  grok: 'high',
  opencode: 'default',
};

// Per-model tiers come from the models API (backend MODELS); an empty list
// hides the selector.
export function getThinkingModesForAgent(
  agentKind: AgentKind,
  modelId?: string,
): ThinkingModeOption[] {
  const model = queryClient
    .getQueryData<Model[]>([queryKeys.models])
    ?.find((candidate) => candidate.model_id === modelId);
  if (!model?.thinking_modes.length) return EMPTY_THINKING_MODES;
  const modes = model.thinking_modes.map((value) => ({
    value,
    label: THINKING_MODE_LABELS[value],
  }));
  return agentKind === 'opencode' ? [OPENCODE_DEFAULT_THINKING_MODE, ...modes] : modes;
}

// Subscribes to the models query so the selector appears once tiers load.
export function useThinkingModes(agentKind: AgentKind, modelId?: string): ThinkingModeOption[] {
  useModelsQuery();
  return getThinkingModesForAgent(agentKind, modelId);
}

export function coerceThinkingModeForAgent(
  thinkingMode: string,
  agentKind: AgentKind,
  modelId?: string,
): string {
  const modes = getThinkingModesForAgent(agentKind, modelId);
  if (
    agentKind === 'antigravity' &&
    modelId === 'antigravity:gemini-3.1-pro' &&
    thinkingMode === 'medium'
  ) {
    return 'high';
  }
  const requestedMode = THINKING_MODE_ORDER.includes(thinkingMode)
    ? thinkingMode
    : DEFAULT_BY_AGENT[agentKind];
  const exactMode = modes.find((mode) => mode.value === requestedMode);
  if (exactMode) return exactMode.value;

  const requestedIndex = THINKING_MODE_ORDER.indexOf(requestedMode);
  for (let index = requestedIndex - 1; index >= 0; index -= 1) {
    const lowerMode = modes.find((mode) => mode.value === THINKING_MODE_ORDER[index]);
    if (lowerMode) return lowerMode.value;
  }

  return modes[0]?.value ?? DEFAULT_BY_AGENT[agentKind];
}

export function getThinkingModeOption(
  thinkingMode: string,
  agentKind: AgentKind,
  modelId?: string,
): ThinkingModeOption | null {
  const modes = getThinkingModesForAgent(agentKind, modelId);
  if (modes.length === 0) return null;
  const effectiveMode = coerceThinkingModeForAgent(thinkingMode, agentKind, modelId);
  const selectedMode = modes.find((mode) => mode.value === effectiveMode);

  if (!selectedMode) {
    throw new Error(`Missing thinking mode option for ${agentKind}: ${effectiveMode}`);
  }

  return selectedMode;
}
