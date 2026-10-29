CREATE TABLE IF NOT EXISTS `reader_jobs` (
	`id` text PRIMARY KEY NOT NULL,
	`node_id` text NOT NULL,
	`payload` text NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`lease_until` integer,
	`created_at` integer NOT NULL
);

CREATE TABLE IF NOT EXISTS `reader_nodes` (
	`id` text PRIMARY KEY NOT NULL,
	`paper_date` text NOT NULL,
	`paper_no` integer NOT NULL,
	`kind` text NOT NULL,
	`parent_id` text,
	`level` integer DEFAULT 0 NOT NULL,
	`generation` integer DEFAULT 1 NOT NULL,
	`status` text DEFAULT 'queued' NOT NULL,
	`title` text DEFAULT '' NOT NULL,
	`content` text DEFAULT '' NOT NULL,
	`scope` text DEFAULT '' NOT NULL,
	`created_at` integer NOT NULL,
	`expires_at` integer,
	`error` text DEFAULT '' NOT NULL
);

CREATE TABLE IF NOT EXISTS `reader_settings` (
	`key` text PRIMARY KEY NOT NULL,
	`value` text NOT NULL
);
