-- Allow dashboard website jobs to finish crawling, return to the worker queue, then run embedding separately.

ALTER TYPE public.indexing_job_phase ADD VALUE IF NOT EXISTS 'embedding_queued';
