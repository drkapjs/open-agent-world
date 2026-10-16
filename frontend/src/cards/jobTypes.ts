export type JobAction = 'refresh' | 'pause' | 'resume' | 'cancel';
export interface BackgroundJob {
  id: string; provider: string; request_key: string; external_id: string | null;
  status: string; detail: string; paused: boolean; wake_requested: boolean;
  revision: number; created_at: number; updated_at: number;
  last_observed_at: number | null; next_check_at: number | null;
  observation_error: string | null; tracking_released: boolean | null;
  agent_id: string; agent_name: string; sandbox_id: string; sandbox_name: string;
  source_run_id: string; actions: JobAction[];
}
export interface JobsSnapshot {
  sources: { id: string; name: string; type: string; can_manage: boolean }[];
  jobs: BackgroundJob[];
}
export interface JobsHost {
  list(): Promise<JobsSnapshot>;
  control(job: BackgroundJob, action: JobAction): Promise<BackgroundJob>;
}
export const jobStatusLabels: Record<string, string> = {
  submitting: 'Submitting', queued: 'Queued', running: 'Running', succeeded: 'Succeeded',
  failed: 'Failed', cancelled: 'Cancelled', interrupted: 'Interrupted', unknown: 'Unknown',
};
export function jobGroup(job: BackgroundJob): 'active' | 'attention' | 'finished' {
  if (job.observation_error || job.paused && !job.tracking_released && (['submitting', 'queued', 'running'].includes(job.status) || job.wake_requested)
    || ['unknown', 'failed', 'interrupted'].includes(job.status)) return 'attention';
  return ['submitting', 'queued', 'running'].includes(job.status) && !job.tracking_released ? 'active' : 'finished';
}
