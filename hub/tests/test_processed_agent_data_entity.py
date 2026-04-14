from datetime import datetime

import pytest

from app.entities.agent_data import AccelerometerData, AgentData, GpsData
from app.entities.processed_agent_data import ProcessedAgentData


def test_processed_agent_data_accepts_agent_payload():
    data = ProcessedAgentData(
        road_state="normal",
        agent_data=AgentData(
            user_id=1,
            accelerometer=AccelerometerData(x=0.1, y=0.2, z=0.3),
            gps=GpsData(latitude=50.45, longitude=30.52),
            timestamp=datetime(2026, 3, 20, 12, 0, 0),
        ),
    )
    assert data.agent_data is not None
    assert data.road_state == "normal"
