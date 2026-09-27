-- 008: push_subscriptions holds Web Push credentials (p256dh/auth keys), not public content.
-- 99-grants.sql gives anon a blanket SELECT on every public table; narrow that one table back down
-- without touching the blanket grant everything else still relies on. Idempotent.
revoke select on push_subscriptions from anon;
notify pgrst, 'reload schema';
