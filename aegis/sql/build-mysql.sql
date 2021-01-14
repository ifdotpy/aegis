CREATE TABLE build (
  build_id BIGINT(20) NOT NULL AUTO_INCREMENT,
  branch VARCHAR(100) NOT NULL,
  revision VARCHAR(100) NOT NULL,
  version VARCHAR(100) DEFAULT NULL,
  build_output_tx MEDIUMTEXT DEFAULT NULL,
  build_exit_status INTEGER DEFAULT NULL,
  build_exec_sec DECIMAL DEFAULT NULL,
  build_size DECIMAL DEFAULT NULL,
  previous_version VARCHAR(100) DEFAULT NULL,
  deploy_dttm TIMESTAMP DEFAULT NULL,
  deploy_output_tx MEDIUMTEXT DEFAULT NULL,
  deploy_exit_status INTEGER DEFAULT NULL,
  revert_dttm TIMESTAMP DEFAULT NULL,
  revert_output_tx MEDIUMTEXT DEFAULT NULL,
  revert_exit_status INTEGER DEFAULT NULL,
  create_dttm TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
  update_dttm TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  delete_dttm TIMESTAMP DEFAULT NULL,
  PRIMARY KEY (build_id),
  UNIQUE (version)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
