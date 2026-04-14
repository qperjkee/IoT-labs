from typing import Set, Dict, List, Any
from fastapi import FastAPI, HTTPException, Query, WebSocket, WebSocketDisconnect
from sqlalchemy import (
    and_,
    create_engine,
    MetaData,
    Table,
    Column,
    Integer,
    String,
    Float,
    DateTime,
    func,
    insert,
    update as sql_update,
    delete as sql_delete,
    select,
)
from sqlalchemy.orm import sessionmaker
from datetime import datetime
from pydantic import BaseModel, field_validator
from config import (
    POSTGRES_HOST,
    POSTGRES_PORT,
    POSTGRES_DB,
    POSTGRES_USER,
    POSTGRES_PASSWORD,
)

# FastAPI app setup
app = FastAPI(
    title="Road Monitoring API",
    description="API for ingesting agent accelerometer data.",
    version="1.0.0",
)

# SQLAlchemy setup
DATABASE_URL = f"postgresql+psycopg2://{POSTGRES_USER}:{POSTGRES_PASSWORD}@{POSTGRES_HOST}:{POSTGRES_PORT}/{POSTGRES_DB}"
engine = create_engine(DATABASE_URL)
metadata = MetaData()
processed_agent_data = Table(
    "processed_agent_data",
    metadata,
    Column("id", Integer, primary_key=True, index=True),
    Column("road_state", String),
    Column("user_id", Integer),
    Column("x", Float),
    Column("y", Float),
    Column("z", Float),
    Column("latitude", Float),
    Column("longitude", Float),
    Column("timestamp", DateTime),
)
SessionLocal = sessionmaker(bind=engine)
metadata.create_all(engine)


# Pydantic models
class ProcessedAgentDataInDB(BaseModel):
    id: int
    road_state: str
    user_id: int
    x: float
    y: float
    z: float
    latitude: float
    longitude: float
    timestamp: datetime


class AccelerometerData(BaseModel):
    x: float
    y: float
    z: float


class GpsData(BaseModel):
    latitude: float
    longitude: float


class AgentData(BaseModel):
    user_id: int
    accelerometer: AccelerometerData
    gps: GpsData
    timestamp: datetime

    @classmethod
    @field_validator("timestamp", mode="before")
    def check_timestamp(cls, value):
        if isinstance(value, datetime):
            return value
        try:
            return datetime.fromisoformat(value)
        except (TypeError, ValueError):
            raise ValueError(
                "Invalid timestamp format. Expected ISO 8601 format (YYYY-MM-DDTHH:MM:SSZ)."
            )


class ProcessedAgentData(BaseModel):
    road_state: str
    agent_data: AgentData


class IngestedData(BaseModel):
    road_state: str
    agent_data: AgentData


# WebSocket subscriptions
subscriptions: Dict[int, Set[WebSocket]] = {}
public_subscriptions: Set[WebSocket] = set()


@app.websocket("/ws/")
async def websocket_public_endpoint(websocket: WebSocket):
    await websocket.accept()
    public_subscriptions.add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        public_subscriptions.discard(websocket)


@app.websocket("/ws/{user_id}")
async def websocket_endpoint(websocket: WebSocket, user_id: int):
    await websocket.accept()
    if user_id not in subscriptions:
        subscriptions[user_id] = set()
    subscriptions[user_id].add(websocket)
    try:
        while True:
            await websocket.receive_text()
    except WebSocketDisconnect:
        subscriptions[user_id].discard(websocket)


async def send_data_to_subscribers(user_id: int, data: Any):
    if user_id in subscriptions:
        for websocket in subscriptions[user_id]:
            await websocket.send_json(data)


def _build_timestamp_filters(
    from_ts: datetime | None, to_ts: datetime | None, timestamp_column
):
    filters = []
    if from_ts is not None:
        filters.append(timestamp_column >= from_ts)
    if to_ts is not None:
        filters.append(timestamp_column <= to_ts)
    return filters


def _row_to_db_model(row) -> ProcessedAgentDataInDB:
    return ProcessedAgentDataInDB(**dict(row))


@app.post(
    "/processed_agent_data/",
    tags=["Data Ingestion"],
    summary="Ingest agent data",
)
async def create_processed_agent_data(data: List[IngestedData]):
    sent = 0
    db = SessionLocal()
    try:
        for item in data:
            row = {
                "road_state": item.road_state,
                "user_id": item.agent_data.user_id,
                "x": item.agent_data.accelerometer.x,
                "y": item.agent_data.accelerometer.y,
                "z": item.agent_data.accelerometer.z,
                "latitude": item.agent_data.gps.latitude,
                "longitude": item.agent_data.gps.longitude,
                "timestamp": item.agent_data.timestamp,
            }
            db.execute(insert(processed_agent_data).values(**row))
            await send_data_to_subscribers(
                item.agent_data.user_id,
                {
                    "road_state": row["road_state"],
                    "user_id": row["user_id"],
                    "x": row["x"],
                    "y": row["y"],
                    "z": row["z"],
                    "latitude": row["latitude"],
                    "longitude": row["longitude"],
                    "timestamp": row["timestamp"].isoformat(),
                },
            )
            sent += 1
        db.commit()
    finally:
        db.close()
    return {"sent": sent}


@app.get(
    "/processed_agent_data/",
    response_model=list[ProcessedAgentDataInDB],
    tags=["Analytics"],
)
def list_processed_agent_data():
    db = SessionLocal()
    try:
        rows = db.execute(select(processed_agent_data)).mappings().all()
        return [dict(r) for r in rows]
    finally:
        db.close()


@app.get(
    "/analytics/road_state_summary",
    tags=["Analytics"],
    summary="Get summary of road states",
)
def road_state_summary(
    from_ts: datetime | None = Query(default=None, alias="from"),
    to_ts: datetime | None = Query(default=None, alias="to"),
):
    if from_ts is not None and to_ts is not None and from_ts > to_ts:
        raise HTTPException(status_code=400, detail="'from' must be <= 'to'")

    db = SessionLocal()
    try:
        stmt = select(
            processed_agent_data.c.road_state,
            func.count(processed_agent_data.c.id).label("events_count"),
        )
        filters = _build_timestamp_filters(
            from_ts, to_ts, processed_agent_data.c.timestamp
        )
        if filters:
            stmt = stmt.where(and_(*filters))
        stmt = stmt.group_by(processed_agent_data.c.road_state).order_by(
            func.count(processed_agent_data.c.id).desc()
        )
        rows = db.execute(stmt).mappings().all()
        return [
            {"road_state": row["road_state"], "events_count": int(row["events_count"])}
            for row in rows
        ]
    finally:
        db.close()


@app.get(
    "/processed_agent_data/{processed_agent_data_id}",
    response_model=ProcessedAgentDataInDB,
    tags=["Analytics"],
)
def read_processed_agent_data(processed_agent_data_id: int):
    db = SessionLocal()
    try:
        stmt = select(processed_agent_data).where(
            processed_agent_data.c.id == processed_agent_data_id
        )
        row = db.execute(stmt).mappings().first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"ProcessedAgentData with id={processed_agent_data_id} not found",
            )
        return _row_to_db_model(row)
    finally:
        db.close()


@app.put(
    "/processed_agent_data/{processed_agent_data_id}",
    response_model=ProcessedAgentDataInDB,
    tags=["Analytics"],
)
def update_processed_agent_data(processed_agent_data_id: int, data: ProcessedAgentData):
    values = {
        "road_state": data.road_state,
        "user_id": data.agent_data.user_id,
        "x": data.agent_data.accelerometer.x,
        "y": data.agent_data.accelerometer.y,
        "z": data.agent_data.accelerometer.z,
        "latitude": data.agent_data.gps.latitude,
        "longitude": data.agent_data.gps.longitude,
        "timestamp": data.agent_data.timestamp,
    }
    db = SessionLocal()
    try:
        stmt = (
            sql_update(processed_agent_data)
            .where(processed_agent_data.c.id == processed_agent_data_id)
            .values(**values)
            .returning(*processed_agent_data.c)
        )
        row = db.execute(stmt).mappings().first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"ProcessedAgentData with id={processed_agent_data_id} not found",
            )
        db.commit()
        return _row_to_db_model(row)
    finally:
        db.close()


@app.delete(
    "/processed_agent_data/{processed_agent_data_id}",
    response_model=ProcessedAgentDataInDB,
    tags=["Analytics"],
)
def delete_processed_agent_data(processed_agent_data_id: int):
    db = SessionLocal()
    try:
        stmt = (
            sql_delete(processed_agent_data)
            .where(processed_agent_data.c.id == processed_agent_data_id)
            .returning(*processed_agent_data.c)
        )
        row = db.execute(stmt).mappings().first()
        if row is None:
            raise HTTPException(
                status_code=404,
                detail=f"ProcessedAgentData with id={processed_agent_data_id} not found",
            )
        db.commit()
        return _row_to_db_model(row)
    finally:
        db.close()


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=8000)
