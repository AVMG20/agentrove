// OpenCode sends its raw tool name in the ACP `name` field (backend extracts
// it as tool_name for opencode). Tool kinds collapse multiple opencode tools
// into one ACP kind (e.g. edit/write/patch → "edit"), so renderers key off the
// tool name rather than the kind.
//
// The backend shapes every completed opencode result as
//   { output: string, metadata: Record<string, unknown> }
// rawInput shapes below mirror opencode's parameter schemas; v2 renamed
// `filePath` → `path` and `subagent_type` → `agent`, v1 names remain for
// persisted chats.

export interface OpencodeBashInput {
  command?: string;
  timeout?: number;
  workdir?: string;
  description?: string;
}

export interface OpencodeReadInput {
  path?: string;
  filePath?: string;
  offset?: number;
  limit?: number;
}

export interface OpencodeWriteInput {
  path?: string;
  filePath?: string;
  content?: string;
}

export interface OpencodeEditInput {
  path?: string;
  filePath?: string;
  oldString?: string;
  newString?: string;
  replaceAll?: boolean;
}

export interface OpencodeGrepInput {
  pattern?: string;
  path?: string;
  include?: string;
}

export interface OpencodeGrepMetadata {
  matches?: number;
  truncated?: boolean;
}

export interface OpencodeGlobInput {
  pattern?: string;
  path?: string;
}

export interface OpencodeGlobMetadata {
  count?: number;
  truncated?: boolean;
}

export interface OpencodeWebFetchInput {
  url?: string;
  format?: 'text' | 'markdown' | 'html';
  timeout?: number;
}

export interface OpencodeTaskInput {
  description?: string;
  prompt?: string;
  agent?: string;
  subagent_type?: string;
  task_id?: string;
  command?: string;
}

export interface OpencodeTodoInfo {
  id?: string;
  content?: string;
  status?: 'pending' | 'in_progress' | 'completed' | 'cancelled';
  priority?: 'low' | 'medium' | 'high';
}

export interface OpencodeTodoWriteInput {
  todos?: OpencodeTodoInfo[];
}

export interface OpencodeSkillInput {
  name?: string;
}

export interface OpencodeQuestionPrompt {
  question?: string;
  header?: string;
  options?: { label?: string; value?: string }[];
  multiple?: boolean;
}

export interface OpencodeQuestionInput {
  questions?: OpencodeQuestionPrompt[];
}

export interface OpencodeQuestionOutput {
  output?: string;
  error?: string;
  metadata?: { answers?: string[][] };
}

export interface OpencodeOutput {
  output?: string;
  error?: string;
  metadata?: Record<string, unknown>;
}
