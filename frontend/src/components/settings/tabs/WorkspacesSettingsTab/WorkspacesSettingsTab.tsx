import { useCallback, useMemo, useState } from 'react';
import { Box, Cloud, Database, FolderGit2, HardDrive } from 'lucide-react';
import toast from 'react-hot-toast';
import { ListManagementTab } from '@/components/ui/ListManagementTab/ListManagementTab';
import { RenameModal } from '@/components/ui/RenameModal/RenameModal';
import { FloatingTooltip } from '@/components/ui/FloatingTooltip/FloatingTooltip';
import {
  useDeleteWorkspaceMutation,
  useUpdateWorkspaceMutation,
  useWorkspaceDiskUsageQuery,
  useWorkspacesQuery,
} from '@/hooks/queries/useWorkspaceQueries';
import {
  useCloudDeleteWorkspaceMutation,
  useCloudUpdateWorkspaceMutation,
  useCloudWorkspacesQuery,
} from '@/hooks/queries/useCloudQueries';
import { useCloudSettingsStore } from '@/store/cloudSettingsStore';
import type { Workspace, WorkspaceDiskUsage } from '@/types/workspace.types';
import { formatRelativeTime } from '@/utils/date';
import { formatBytes } from '@/utils/format';
import styles from './WorkspacesSettingsTab.module.scss';

// onCloud tags origin so renames/deletes hit the owning backend.
interface WorkspaceListItem {
  workspace: Workspace;
  onCloud: boolean;
}

const SOURCE_ICONS = { git: FolderGit2, local: HardDrive, empty: Box } as const;

function describeSource(workspace: Workspace): string {
  if (workspace.source_type === 'git') return workspace.source_url ?? 'Git repository';
  if (workspace.source_type === 'local') return workspace.workspace_path;
  return 'Empty workspace';
}

function describeDiskUsage(usage: WorkspaceDiskUsage): string {
  const parts = [
    usage.files_bytes != null && `Files ${formatBytes(usage.files_bytes)}`,
    usage.sandbox_bytes != null && `Sandbox ${formatBytes(usage.sandbox_bytes)}`,
  ].filter(Boolean);
  return `${parts.join(' · ')} — freed when the workspace is deleted`;
}

export function WorkspacesSettingsTab() {
  const cloudUrl = useCloudSettingsStore((state) => state.cloudUrl);
  const cloudConnected = !!cloudUrl;
  const { data: localWorkspaces } = useWorkspacesQuery();
  const { data: cloudWorkspaces, isLoading: cloudLoading } =
    useCloudWorkspacesQuery(cloudConnected);
  // Local backend only; a cloud workspace's disk is the VPS's.
  const { data: diskUsage } = useWorkspaceDiskUsageQuery();
  const diskUsageById = useMemo(
    () => new Map((diskUsage ?? []).map((usage) => [usage.workspace_id, usage])),
    [diskUsage],
  );

  const deleteWorkspace = useDeleteWorkspaceMutation();
  const cloudDeleteWorkspace = useCloudDeleteWorkspaceMutation();
  const updateWorkspace = useUpdateWorkspaceMutation();
  const cloudUpdateWorkspace = useCloudUpdateWorkspaceMutation();
  const [renaming, setRenaming] = useState<WorkspaceListItem | null>(null);

  const items = useMemo<WorkspaceListItem[] | null>(() => {
    // Wait for both backends so cloud workspaces don't pop in after an empty state.
    if (!localWorkspaces || (cloudConnected && cloudLoading)) return null;
    return [
      ...localWorkspaces.items.map((workspace) => ({ workspace, onCloud: false })),
      ...(cloudWorkspaces ?? []).map((workspace) => ({ workspace, onCloud: true })),
    ];
  }, [localWorkspaces, cloudWorkspaces, cloudConnected, cloudLoading]);

  const handleDelete = useCallback(
    async (index: number) => {
      const item = items?.[index];
      if (!item) return;
      const mutation = item.onCloud ? cloudDeleteWorkspace : deleteWorkspace;
      try {
        await mutation.mutateAsync(item.workspace.id);
        toast.success('Workspace deleted');
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Failed to delete workspace');
        throw error;
      }
    },
    [items, deleteWorkspace, cloudDeleteWorkspace],
  );

  const handleRename = useCallback(
    async (name: string) => {
      if (!renaming) return;
      const mutation = renaming.onCloud ? cloudUpdateWorkspace : updateWorkspace;
      try {
        await mutation.mutateAsync({ workspaceId: renaming.workspace.id, data: { name } });
        toast.success('Workspace renamed');
        setRenaming(null);
      } catch (error) {
        toast.error(error instanceof Error ? error.message : 'Failed to rename workspace');
      }
    },
    [renaming, updateWorkspace, cloudUpdateWorkspace],
  );

  return (
    <>
      <ListManagementTab<WorkspaceListItem>
        title="Workspaces"
        description="Rename or delete workspaces, including ones without threads that don't show in the sidebar. Create new ones from the workspace picker in the composer."
        items={items}
        emptyIcon={Box}
        emptyText="No workspaces yet"
        deleteConfirmTitle="Delete workspace"
        deleteConfirmMessage={(item) =>
          `Delete "${item.workspace.name}"? Its ${item.workspace.chat_count} ${
            item.workspace.chat_count === 1 ? 'thread' : 'threads'
          }, sandbox and files are removed. This can't be undone.`
        }
        getItemKey={(item) => `${item.onCloud ? 'cloud' : 'local'}:${item.workspace.id}`}
        onEdit={(index) => {
          const item = items?.[index];
          if (item) setRenaming(item);
        }}
        onDelete={handleDelete}
        renderItem={(item) => {
          const SourceIcon =
            SOURCE_ICONS[item.workspace.source_type as keyof typeof SOURCE_ICONS] ?? Box;
          const usage = item.onCloud ? undefined : diskUsageById.get(item.workspace.id);
          return (
            <>
              <div className={styles['item-header']}>
                <SourceIcon className={styles['source-icon']} aria-hidden="true" />
                <h3 className={styles['item-title']}>{item.workspace.name}</h3>
                {item.onCloud && (
                  <span className={styles.badge}>
                    <Cloud className={styles['badge-icon']} />
                    Cloud
                  </span>
                )}
              </div>
              <div className={styles['item-meta']}>
                <span className={styles.source}>{describeSource(item.workspace)}</span>
                <span>
                  {item.workspace.chat_count}{' '}
                  {item.workspace.chat_count === 1 ? 'thread' : 'threads'}
                </span>
                <span>{item.workspace.sandbox_provider === 'docker' ? 'Docker' : 'Host'}</span>
                {usage?.total_bytes != null && (
                  <FloatingTooltip content={describeDiskUsage(usage)} className={styles.size}>
                    <Database className={styles['size-icon']} aria-hidden="true" />
                    {formatBytes(usage.total_bytes)}
                  </FloatingTooltip>
                )}
                <span className={styles.muted}>
                  {item.workspace.last_chat_at
                    ? `Active ${formatRelativeTime(item.workspace.last_chat_at)}`
                    : `Created ${formatRelativeTime(item.workspace.created_at)}`}
                </span>
              </div>
            </>
          );
        }}
        logContext="WorkspacesSettingsTab"
      />
      <RenameModal
        isOpen={renaming !== null}
        onClose={() => setRenaming(null)}
        onSave={handleRename}
        currentTitle={renaming?.workspace.name ?? ''}
        isLoading={updateWorkspace.isPending || cloudUpdateWorkspace.isPending}
        title="Rename workspace"
      />
    </>
  );
}
