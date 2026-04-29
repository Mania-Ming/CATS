-- ============================================================
-- PICKUP SUPPORT MIGRATION
-- Run in Supabase SQL Editor
-- ============================================================

-- Pickup-specific columns
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS pickup_date date;
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS pickup_time time;
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS pickup_location text;
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS pickup_notes text;

-- Rename delivery_time_start / delivery_time_end to match spec (keep old names too for compat)
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS start_time text;
ALTER TABLE adoption_requests ADD COLUMN IF NOT EXISTS end_time text;

-- Update delivery_method constraint to include all valid values
ALTER TABLE adoption_requests
  DROP CONSTRAINT IF EXISTS adoption_requests_delivery_method_check;

ALTER TABLE adoption_requests
  ADD CONSTRAINT adoption_requests_delivery_method_check
  CHECK (delivery_method IN ('Meet-up', 'Delivery', 'Pickup'));

-- Update delivery_status to support all statuses
ALTER TABLE adoption_requests
  DROP CONSTRAINT IF EXISTS adoption_requests_delivery_status_check;

-- No constraint on delivery_status — values vary by method
-- General: Pending, Approved, Scheduled, Completed, Rejected (on status column)
-- Delivery: Preparing, Out for Delivery, Delivered
-- Pickup: Ready for Pickup, Claimed
