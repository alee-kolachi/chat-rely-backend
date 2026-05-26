-- After idle-close, mark conversations billable when they meet plan thresholds.

create or replace function public.close_idle_conversations(
  p_now timestamptz default now()
)
returns integer
language plpgsql
security definer
set search_path = public
as $$
declare
  closed_count integer := 0;
  conv_id uuid;
begin
  -- PostgreSQL requires UPDATE .. RETURNING to sit in a CTE here (not a bare subquery in FROM).
  for conv_id in
    with updated as (
      update public.conversations c
         set status = 'idle_closed',
             closed_at = p_now,
             updated_at = p_now
       where c.status = 'open'
         and c.last_activity_at <= p_now - interval '30 minutes'
      returning c.id
    )
    select id from updated
  loop
    closed_count := closed_count + 1;
    perform public.mark_conversation_billable(conv_id);
  end loop;

  return closed_count;
end;
$$;

comment on function public.close_idle_conversations(timestamptz)
  is 'Marks open conversations idle_closed after 30m inactivity, then evaluates mark_conversation_billable for each.';
