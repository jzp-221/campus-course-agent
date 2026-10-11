-- Public local-demo template, not a database export.
-- Import only into a new local demo database selected by the mysql command.
-- Existing usernames are left untouched. Never use these demo passwords online.
SET NAMES utf8mb4;

CREATE TABLE IF NOT EXISTS `users` (
  `id` INT NOT NULL AUTO_INCREMENT,
  `username` VARCHAR(50) NOT NULL,
  `password` VARCHAR(255) NOT NULL,
  `name` VARCHAR(50) NOT NULL,
  `role` VARCHAR(50) NOT NULL,
  `email` VARCHAR(50) NULL,
  `phone` VARCHAR(50) NULL,
  `avatar` VARCHAR(50) NULL,
  `status` INT NOT NULL,
  `create_time` DATETIME NOT NULL,
  `update_time` DATETIME NOT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `username` (`username`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4 COLLATE=utf8mb4_unicode_ci;

INSERT IGNORE INTO `users` (`username`, `password`, `name`, `role`, `status`, `create_time`, `update_time`) VALUES ('admin', '$2b$12$jCGrJw7nBravSA0bEK3gou6Mu/GY.x6ITHCnUBNQNRC2TA9EgoDV2', '演示管理员', 'admin', 1, NOW(), NOW());
INSERT IGNORE INTO `users` (`username`, `password`, `name`, `role`, `status`, `create_time`, `update_time`) VALUES ('aaa', '$2b$12$Cpp5FFmQAWFv6k6qEaIciuZxCGYhjfKvHwsvB9WQRSBsrNaOVhT0a', '演示学生 A', 'student', 1, NOW(), NOW());
INSERT IGNORE INTO `users` (`username`, `password`, `name`, `role`, `status`, `create_time`, `update_time`) VALUES ('bbb', '$2b$12$P0ga7OmDfCRfAAIS5Qbws.JdQlah7yZUaIgRCNyP8LbGQXJlSam.6', '演示学生 B', 'student', 1, NOW(), NOW());
