CREATE TABLE processed_agent_data (
    id SERIAL PRIMARY KEY,
    road_state VARCHAR(255) NOT NULL,
    user_id INTEGER NOT NULL,
    x FLOAT,
    y FLOAT,
    z FLOAT,
    latitude FLOAT,
    longitude FLOAT,
    timestamp TIMESTAMP
);

CREATE INDEX idx_processed_agent_data_timestamp
    ON processed_agent_data (timestamp);

CREATE INDEX idx_processed_agent_data_user_id
    ON processed_agent_data (user_id);

CREATE INDEX idx_processed_agent_data_road_state
    ON processed_agent_data (road_state);
