// Whether an image-model install job is still going. Its own module so the pure dashboard
// helpers can use it without pulling in the hooks (react-query, the fetch client); useComfyui.ts
// re-exports it.

/** True while the job is still downloading (or waiting to). Takes any `ImageJob`. */
export const isImageJobActive = (job: { state: string } | null | undefined): boolean =>
  job?.state === 'queued' || job?.state === 'running'
