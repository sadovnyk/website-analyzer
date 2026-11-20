-- MySQL schema for Website Analyzer.
-- Executed automatically by the MySQL container on the FIRST start
-- (empty data directory) via /docker-entrypoint-initdb.d/.
-- The target database (DB_NAME) is already selected by the entrypoint.

CREATE TABLE IF NOT EXISTS `sites` (
  `id`        INT          NOT NULL AUTO_INCREMENT,
  `url`       VARCHAR(255) NOT NULL,
  `is_active` TINYINT(1)   NOT NULL DEFAULT 1,
  `added_at`  DATETIME              DEFAULT CURRENT_TIMESTAMP,
  `is_pinned` TINYINT(1)   NOT NULL DEFAULT 0,
  PRIMARY KEY (`id`),
  UNIQUE KEY `url` (`url`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;

CREATE TABLE IF NOT EXISTS `scans` (
  `id`            INT          NOT NULL AUTO_INCREMENT,
  `url`           VARCHAR(255) NOT NULL,
  `scanned_at`    DATETIME              DEFAULT CURRENT_TIMESTAMP,
  `status_code`   SMALLINT              DEFAULT NULL,
  `title`         VARCHAR(500)          DEFAULT NULL,
  `description`   VARCHAR(500)          DEFAULT NULL,
  `duration_ms`   INT                   DEFAULT NULL,
  `status_error`  VARCHAR(200)          DEFAULT NULL,
  `ip`            VARCHAR(45)           DEFAULT NULL,  -- fits IPv6
  `country`       VARCHAR(250)          DEFAULT NULL,
  `city`          VARCHAR(250)          DEFAULT NULL,
  `org`           VARCHAR(250)          DEFAULT NULL,
  `geo_error`     VARCHAR(200)          DEFAULT NULL,
  `ssl_days_left` INT                   DEFAULT NULL,
  `valid`         TINYINT(1)            DEFAULT NULL,
  `cert_error`    VARCHAR(200)          DEFAULT NULL,
  `total_links`   INT                   DEFAULT NULL,
  `links_error`   VARCHAR(200)          DEFAULT NULL,
  `site_id`       INT                   DEFAULT NULL,
  `added_at`      DATETIME              DEFAULT CURRENT_TIMESTAMP,  -- not used by the app (duplicates scanned_at)
  PRIMARY KEY (`id`),
  KEY `fk_site` (`site_id`),
  CONSTRAINT `fk_site` FOREIGN KEY (`site_id`) REFERENCES `sites` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_0900_ai_ci;