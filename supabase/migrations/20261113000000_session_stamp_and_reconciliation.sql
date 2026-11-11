-- 1. users.session_stamp — a random marker that is rewritten in the SAME update
--    that reduces a user's access (role change, deactivation, password change).
--    Every session token carries the stamp that was on the row when it was
--    issued, read in the same query as the role. A token built from an older
--    copy of the row therefore carries an older stamp and is refused, however
--    the sign-in and the change were interleaved.
ALTER TABLE users ADD COLUMN IF NOT EXISTS session_stamp TEXT NOT NULL DEFAULT '';

-- 2. drafts.status 'needs_review' — an approved action whose outcome could not
--    be confirmed (e.g. the connection dropped after the request was sent). It
--    is never shown as simply "approved": it stays in a "needs checking" list
--    until a person confirms what happened.
ALTER TABLE drafts DROP CONSTRAINT IF EXISTS drafts_status_check;
ALTER TABLE drafts ADD CONSTRAINT drafts_status_check CHECK (status IN (
    'pending', 'executing', 'approved', 'edited_and_approved', 'rejected', 'expired', 'failed',
    'needs_review'));
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS reconciled_by UUID;
ALTER TABLE drafts ADD COLUMN IF NOT EXISTS reconciled_at TIMESTAMPTZ;
UPDATE drafts SET status = 'needs_review'
    WHERE execution_status = 'unknown' AND status IN ('approved', 'edited_and_approved') AND reconciled_at IS NULL;
