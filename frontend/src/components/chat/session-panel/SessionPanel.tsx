import { memo, useMemo, type ReactNode } from 'react';
import { useNavigate } from 'react-router-dom';
import {
  Box,
  Cloud,
  CornerLeftUp,
  ExternalLink,
  FileDiff,
  Folder,
  GitBranch,
  Monitor,
  Plus,
} from 'lucide-react';
import clsx from 'clsx';
import { Button } from '@/components/ui/primitives/Button/Button';
import { FloatingTooltip } from '@/components/ui/FloatingTooltip/FloatingTooltip';
import { AsciiSpinner } from '@/components/ui/AsciiSpinner/AsciiSpinner';
import { ChatStatusDot } from '@/components/ui/ChatStatusDot/ChatStatusDot';
import { ProviderIcon } from '@/components/ui/icons/ProviderIcon';
import { AGENTS_WITHOUT_USAGE_UPDATE } from '@/config/constants';
import { useChatQuery, useSubThreadsQuery } from '@/hooks/queries/useChatQueries';
import { useModelMap } from '@/hooks/queries/useModelQueries';
import {
  useGitBranchesQuery,
  useGitDiffStatsQuery,
  useGitRemoteUrlQuery,
} from '@/hooks/queries/useSandboxQueries';
import { useGitHubPullsQuery } from '@/hooks/queries/useGitHubQueries';
import { useBlockedChatIds } from '@/hooks/useBlockedChatIds';
import { useChatAgentKind } from '@/hooks/useChatAgentKind';
import { useChatSessionState } from '@/hooks/useChatSessionContext';
import { useStreamStore } from '@/store/streamStore';
import { useUIStore } from '@/store/uiStore';
import { getAgentKindForModelId, type Chat } from '@/types/chat.types';
import type { Workspace } from '@/types/workspace.types';
import { isCloudChat } from '@/utils/chatOrigin';
import { getRelativeTime } from '@/utils/date';
import { formatNumberCompact, stripMarkdownTitle } from '@/utils/format';
import { chatStatusTone } from '@/utils/message';
import { openExternalUrl } from '@/utils/openExternal';
import styles from './SessionPanel.module.scss';

// Dialogs and view toggles act on the focused pane; the panel describes the
// routed chat, so point them at the primary pane first.
function focusPrimaryAgent() {
  useUIStore.getState().focusTile('agent:primary');
}

function Card({
  title,
  action,
  children,
}: {
  title: string;
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <section className={styles.card}>
      <header className={styles['card-header']}>
        <h3 className={styles['card-title']}>{title}</h3>
        {action}
      </header>
      {children}
    </section>
  );
}

function Row({ icon, children, hint }: { icon: ReactNode; children: ReactNode; hint?: string }) {
  return (
    <div className={styles.row}>
      <span className={styles['row-icon']}>{icon}</span>
      {hint ? (
        <FloatingTooltip content={hint} className={styles['row-text']}>
          {children}
        </FloatingTooltip>
      ) : (
        <span className={styles['row-text']}>{children}</span>
      )}
    </div>
  );
}

function ModelRow({
  chatId,
  sessionAgentKind,
}: {
  chatId: string;
  sessionAgentKind: Chat['session_agent_kind'];
}) {
  const { selectedModelId } = useChatSessionState();
  const modelMap = useModelMap();
  const agentKind = useChatAgentKind(chatId, sessionAgentKind);
  const model = modelMap.get(selectedModelId);
  if (!selectedModelId) return null;
  return (
    <Row icon={agentKind ? <ProviderIcon agentKind={agentKind} className={styles.glyph} /> : null}>
      {model?.name ?? selectedModelId}
    </Row>
  );
}

function EnvironmentCard({
  chat,
  workspace,
  branch,
}: {
  chat: Chat;
  workspace: Workspace | undefined;
  branch: string | null;
}) {
  const isCloud = isCloudChat(chat.id);
  const isDocker = workspace?.sandbox_provider === 'docker';
  const path = chat.worktree_cwd ?? workspace?.workspace_path;
  return (
    <Card title="Environment">
      <Row
        icon={isCloud ? <Cloud className={styles.glyph} /> : <Monitor className={styles.glyph} />}
      >
        {isCloud ? 'Cloud' : 'This computer'}
        {workspace && (
          <span className={styles.secondary}>
            {' · '}
            {isDocker ? 'Docker' : 'Host'}
          </span>
        )}
      </Row>
      {workspace && (
        <Row
          icon={isDocker ? <Box className={styles.glyph} /> : <Folder className={styles.glyph} />}
          hint={path}
        >
          {workspace.name}
          {path && <span className={styles.secondary}>{` · ${path}`}</span>}
        </Row>
      )}
      {branch && (
        <Row icon={<GitBranch className={styles.glyph} />} hint={branch}>
          {branch}
        </Row>
      )}
      <ModelRow chatId={chat.id} sessionAgentKind={chat.session_agent_kind} />
    </Card>
  );
}

function ContextCard({ chat }: { chat: Chat }) {
  const { contextUsage, selectedModelId } = useChatSessionState();
  const modelMap = useModelMap();
  const agentKind =
    modelMap.get(selectedModelId)?.agent_kind ?? getAgentKindForModelId(selectedModelId);
  if (!contextUsage || AGENTS_WITHOUT_USAGE_UPDATE.has(agentKind)) return null;
  const { tokensUsed, contextWindow } = contextUsage;
  const percentage = contextWindow > 0 ? Math.min((tokensUsed / contextWindow) * 100, 100) : 0;
  const tone = percentage >= 95 ? 'danger' : percentage >= 75 ? 'warning' : 'normal';
  return (
    <Card
      title="Context"
      action={
        <FloatingTooltip
          content={`${formatNumberCompact(tokensUsed)} / ${formatNumberCompact(contextWindow)} tokens`}
          className={styles['context-value']}
        >
          {percentage >= 1 || percentage === 0 ? percentage.toFixed(0) : '<1'}%
        </FloatingTooltip>
      }
    >
      <div
        className={styles['context-track']}
        role="progressbar"
        aria-label={`Context used in ${chat.title}`}
        aria-valuenow={Math.round(percentage)}
        aria-valuemin={0}
        aria-valuemax={100}
      >
        <div
          className={clsx(styles['context-fill'], styles[`context-fill--${tone}`])}
          style={{ width: `${percentage}%` }}
        />
      </div>
    </Card>
  );
}

function SubThreadRow({ thread, onSelect }: { thread: Chat; onSelect: (chatId: string) => void }) {
  const agentKind = useChatAgentKind(thread.id, thread.session_agent_kind);
  const isStreaming = useStreamStore((s) =>
    s.activeStreamMetadata.some((meta) => meta.chatId === thread.id),
  );
  const isCompleted = useStreamStore((s) => s.completedChatIds.has(thread.id));
  const isBlocked = useBlockedChatIds().has(thread.id);
  const status = chatStatusTone({
    blocked: isBlocked,
    streaming: isStreaming,
    completed: isCompleted,
  });
  return (
    <Button
      variant="unstyled"
      type="button"
      className={styles['thread-row']}
      onClick={() => onSelect(thread.id)}
    >
      <span className={styles['row-icon']}>
        {status === 'running' ? (
          <AsciiSpinner className={styles.spinner} />
        ) : agentKind ? (
          <ProviderIcon agentKind={agentKind} className={styles.glyph} />
        ) : null}
        {status && status !== 'running' && (
          <ChatStatusDot
            tone={status}
            className={clsx(agentKind && styles['status-corner'])}
            ringClassName={styles['status-ring']}
          />
        )}
      </span>
      <span className={styles['row-text']}>{stripMarkdownTitle(thread.title)}</span>
      <span className={styles.timestamp}>{getRelativeTime(thread.updated_at)}</span>
    </Button>
  );
}

function ParentThreadCard({ parentChatId }: { parentChatId: string }) {
  const navigate = useNavigate();
  const { data: parent } = useChatQuery(parentChatId);
  return (
    <Card title="Parent thread">
      <Button
        variant="unstyled"
        type="button"
        className={styles['thread-row']}
        onClick={() => navigate(`/chat/${parentChatId}`)}
      >
        <span className={styles['row-icon']}>
          <CornerLeftUp className={styles.glyph} />
        </span>
        <span className={styles['row-text']}>
          {parent ? stripMarkdownTitle(parent.title) : 'Parent thread'}
        </span>
      </Button>
    </Card>
  );
}

function SubThreadsCard({ chatId }: { chatId: string }) {
  const navigate = useNavigate();
  const { data: subThreads } = useSubThreadsQuery(chatId);
  return (
    <Card
      title="Sub-threads"
      action={
        <FloatingTooltip content="New sub-thread">
          <Button
            variant="unstyled"
            type="button"
            className={styles['header-button']}
            aria-label="New sub-thread"
            onClick={() => {
              focusPrimaryAgent();
              useUIStore.getState().setSubThreadDialogOpen(true);
            }}
          >
            <Plus className={styles.glyph} />
          </Button>
        </FloatingTooltip>
      }
    >
      {subThreads && subThreads.length > 0 ? (
        subThreads.map((thread) => (
          <SubThreadRow
            key={thread.id}
            thread={thread}
            onSelect={(id) => navigate(`/chat/${id}`)}
          />
        ))
      ) : (
        <p className={styles.empty}>No sub-threads yet</p>
      )}
    </Card>
  );
}

function ChangesCard({
  chatId,
  sandboxId,
  worktreeCwd,
  branch,
}: {
  chatId: string;
  sandboxId: string;
  worktreeCwd: string | undefined;
  branch: string;
}) {
  const { data: stats } = useGitDiffStatsQuery(sandboxId, worktreeCwd);
  const { data: remoteUrl } = useGitRemoteUrlQuery(sandboxId, true, worktreeCwd);
  const owner = remoteUrl?.owner ?? '';
  const repo = remoteUrl?.repo ?? '';
  const { data: pulls } = useGitHubPullsQuery(owner, repo, !!owner && !!repo, chatId);
  const existingPR = useMemo(
    () =>
      pulls?.items.find(
        (pr) => pr.head.ref === branch && pr.head.repo.full_name === `${owner}/${repo}`,
      ),
    [pulls?.items, branch, owner, repo],
  );
  if (!stats) return null;
  const hasChanges = stats.files_changed > 0;
  return (
    <Card title="Changes">
      <div className={styles['changes-row']}>
        {hasChanges ? (
          <Button
            variant="unstyled"
            type="button"
            className={styles['diff-stats']}
            onClick={() => {
              focusPrimaryAgent();
              useUIStore.getState().toggleView('diff', false);
            }}
          >
            <FileDiff className={styles.glyph} />
            <span className={styles['stat-add']}>+{stats.additions}</span>
            <span className={styles['stat-del']}>−{stats.deletions}</span>
            <span className={styles.secondary}>
              {stats.files_changed} {stats.files_changed === 1 ? 'file' : 'files'}
            </span>
          </Button>
        ) : (
          <span className={styles.empty}>No changes</span>
        )}
        {existingPR ? (
          <Button
            variant="unstyled"
            type="button"
            className={styles['pr-button']}
            onClick={() => openExternalUrl(existingPR.html_url)}
          >
            PR #{existingPR.number}
            <ExternalLink className={styles['pr-icon']} />
          </Button>
        ) : (
          hasChanges && (
            <Button
              variant="unstyled"
              type="button"
              className={styles['pr-button']}
              onClick={() => {
                focusPrimaryAgent();
                useUIStore.getState().setCreatePRDialogOpen(true);
              }}
            >
              Create PR
            </Button>
          )
        )}
      </div>
    </Card>
  );
}

interface SessionPanelProps {
  chat: Chat;
  workspace: Workspace | undefined;
}

export const SessionPanel = memo(function SessionPanel({ chat, workspace }: SessionPanelProps) {
  const worktreeCwd = chat.worktree_cwd ?? undefined;
  const { data: branches } = useGitBranchesQuery(chat.sandbox_id, true, worktreeCwd);
  const branch = branches?.is_git_repo ? branches.current_branch : null;
  return (
    <aside className={styles.panel} aria-label="Session details">
      <EnvironmentCard chat={chat} workspace={workspace} branch={branch} />
      <ContextCard chat={chat} />
      {chat.parent_chat_id ? (
        <ParentThreadCard parentChatId={chat.parent_chat_id} />
      ) : (
        <SubThreadsCard chatId={chat.id} />
      )}
      {branch && (
        <ChangesCard
          chatId={chat.id}
          sandboxId={chat.sandbox_id}
          worktreeCwd={worktreeCwd}
          branch={branch}
        />
      )}
    </aside>
  );
});
