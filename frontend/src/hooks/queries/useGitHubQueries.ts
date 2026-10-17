import { useMemo } from 'react';
import { useMutation, useQuery, useQueryClient, keepPreviousData } from '@tanstack/react-query';
import { githubService } from '@/services/githubService';
import { queryKeys } from '@/hooks/queries/queryKeys';
import { useGitRemoteUrlQuery } from '@/hooks/queries/useSandboxQueries';
import type {
  CreatePRRequest,
  CreatePRResponse,
  GenerateCommitMessageRequest,
  GenerateCommitMessageResponse,
  GeneratePRDescriptionRequest,
  GeneratePRDescriptionResponse,
} from '@/types/github.types';

export function useGitHubReposQuery(query: string, enabled: boolean) {
  return useQuery({
    queryKey: queryKeys.github.repos(query),
    queryFn: () => githubService.searchRepositories(query, 1, 20),
    enabled,
    placeholderData: keepPreviousData,
    staleTime: 30_000,
  });
}

// chatId routes each call to the backend that owns the chat (local or cloud VPS)
// so its GitHub credentials are used — see githubService.
export function useGitHubPullsQuery(
  owner: string,
  repo: string,
  enabled: boolean,
  chatId?: string,
  head?: string,
  state?: 'open' | 'closed' | 'all',
) {
  return useQuery({
    queryKey: queryKeys.github.pulls(owner, repo, chatId, head, state),
    queryFn: () => githubService.listPullRequests(owner, repo, chatId, head, state),
    enabled: enabled && !!owner && !!repo,
    staleTime: 30_000,
  });
}

export function useGitHubCollaboratorsQuery(
  owner: string,
  repo: string,
  enabled: boolean,
  chatId?: string,
) {
  return useQuery({
    queryKey: queryKeys.github.collaborators(owner, repo, chatId),
    queryFn: () => githubService.getCollaborators(owner, repo, chatId),
    enabled: enabled && !!owner && !!repo,
    staleTime: 300_000,
  });
}

export function useExistingPullRequest(
  sandboxId: string,
  worktreeCwd: string | undefined,
  branch: string,
  chatId?: string,
) {
  const { data: remoteUrl } = useGitRemoteUrlQuery(sandboxId, !!sandboxId, worktreeCwd);
  const owner = remoteUrl?.owner ?? '';
  const repo = remoteUrl?.repo ?? '';
  const { data: pulls } = useGitHubPullsQuery(
    owner,
    repo,
    !!owner && !!repo && !!branch,
    chatId,
    `${owner}:${branch}`,
    'all',
  );
  const pr = useMemo(() => {
    if (!branch || !owner || !repo) return undefined;
    const branchPulls = pulls?.items.filter(
      (item) => item.head.ref === branch && item.head.repo.full_name === `${owner}/${repo}`,
    );
    return (
      branchPulls?.find((item) => item.state === 'open') ?? branchPulls?.find((item) => item.merged)
    );
  }, [pulls?.items, branch, owner, repo]);
  return { owner, repo, pr };
}

export function useCreatePullRequestMutation(chatId?: string) {
  const queryClient = useQueryClient();
  return useMutation<CreatePRResponse, Error, CreatePRRequest>({
    mutationFn: (request) => githubService.createPullRequest(request, chatId),
    onSuccess: (_data, { owner, repo }) =>
      queryClient.invalidateQueries({ queryKey: queryKeys.github.pulls(owner, repo, chatId) }),
  });
}

export function useGeneratePRDescriptionMutation(chatId?: string) {
  return useMutation<GeneratePRDescriptionResponse, Error, GeneratePRDescriptionRequest>({
    mutationFn: (request) => githubService.generatePRDescription(request, chatId),
  });
}

export function useGenerateCommitMessageMutation(chatId?: string) {
  return useMutation<GenerateCommitMessageResponse, Error, GenerateCommitMessageRequest>({
    mutationFn: (request) => githubService.generateCommitMessage(request, chatId),
  });
}
