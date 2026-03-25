CREATE TABLE IF NOT EXISTS `users` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `email` varchar(100) DEFAULT NULL,
  `password` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`),
  UNIQUE KEY `email` (`email`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `cats` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `name` varchar(100) DEFAULT NULL,
  `age` varchar(20) DEFAULT NULL,
  `breed` varchar(50) DEFAULT NULL,
  `gender` varchar(20) DEFAULT NULL,
  `image` varchar(100) DEFAULT NULL,
  PRIMARY KEY (`id`)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

CREATE TABLE IF NOT EXISTS `adoption_requests` (
  `id` int(11) NOT NULL AUTO_INCREMENT,
  `cat_id` int(11) NOT NULL,
  `fullname` varchar(255) NOT NULL,
  `contact` varchar(100) NOT NULL,
  `address` text NOT NULL,
  `status` varchar(50) DEFAULT 'Pending',
  `created_at` timestamp NOT NULL DEFAULT current_timestamp(),
  PRIMARY KEY (`id`),
  KEY `cat_id` (`cat_id`),
  CONSTRAINT `adoption_requests_ibfk_1` FOREIGN KEY (`cat_id`) REFERENCES `cats` (`id`) ON DELETE CASCADE
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

INSERT IGNORE INTO `cats` (`id`, `name`, `age`, `breed`, `gender`, `image`) VALUES
(1, 'Jhemer Whiskers', '2 years', 'Persian', 'Male', 'cat1.jpg'),
(2, 'Luna', '1 year', 'Siamese', 'Female', 'cat2.jpg'),
(3, 'Bella', '2 years', 'Ragdoll', 'Female', 'cat3.jpg'),
(4, 'Milo', '1 year', 'British Shorthair', 'Male', 'cat4.jpg');
